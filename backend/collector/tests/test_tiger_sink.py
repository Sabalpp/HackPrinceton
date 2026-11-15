"""Outbox failure tests: SQLite is temporary and PostgreSQL is always mocked."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from farcare_collector.integrations import tiger_sink
from farcare_collector.services.health_pipeline import Store

TARGET = "synthetic-target-key"


def point(value=78, second=0):
    return {
        "dataSource": {"device": {"displayName": "Synthetic sink fixture"}, "platform": "FITBIT"},
        "heartRate": {
            "sampleTime": {"physicalTime": "2026-11-14T15:00:{:02d}Z".format(second)},
            "beatsPerMinute": str(value),
        },
    }


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def executemany(self, statement, values):
        owner = self.connection
        owner.write_calls += 1
        if owner.fail_write_at == owner.write_calls:
            raise RuntimeError("synthetic remote write failure")
        records = list(values)
        owner.attempted_batches.append(records)
        owner.statements.append(statement)
        ignore_conflict = "ON CONFLICT" in statement.upper() and "DO NOTHING" in statement.upper()
        owner.pending.extend((record, ignore_conflict) for record in records)


class FakeConnection:
    """Models staged writes, transaction commit, and the remote uniqueness key."""

    def __init__(self, before_commit=None, fail_commit_at=None, fail_write_at=None):
        self.before_commit = before_commit
        self.fail_commit_at = fail_commit_at
        self.fail_write_at = fail_write_at
        self.pending = []
        self.remote = {}
        self.attempted_batches = []
        self.statements = []
        self.commit_calls = 0
        self.write_calls = 0
        self.rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.pending.clear()
            self.rollback_count += 1
        return False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commit_calls += 1
        if self.before_commit:
            self.before_commit()
        if self.fail_commit_at == self.commit_calls:
            raise RuntimeError("synthetic remote commit failure")
        next_state = dict(self.remote)
        for record, ignore_conflict in self.pending:
            key = tuple(record[:3])
            if key in next_state and not ignore_conflict:
                raise RuntimeError("synthetic remote unique violation")
            next_state.setdefault(key, record)
        self.remote = next_state
        self.pending.clear()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "outbox.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def add(self, count=1):
        with self.store.db:
            for second in range(count):
                self.store.ingest(
                    point(78 + second, second),
                    "google_health",
                    "heart-rate",
                    "synthetic-page.json",
                    "2026-11-14T18:00:00Z",
                )

    def cursor(self):
        row = self.store.db.execute(
            "SELECT last_seq FROM deliveries WHERE target=?", (TARGET,)
        ).fetchone()
        return row[0] if row else 0

    def deliver(self, connection, **kwargs):
        # No credentials file is opened and no network connection is made.
        with (
            patch.object(tiger_sink, "connect", return_value=connection),
            patch.object(tiger_sink, "target_key", return_value=TARGET),
        ):
            return tiger_sink.deliver(self.store, "unused-synthetic-credentials", **kwargs)

    def test_each_remote_commit_precedes_local_checkpoint(self):
        self.add(3)
        cursors_at_commit = []
        connection = FakeConnection(before_commit=lambda: cursors_at_commit.append(self.cursor()))
        result = self.deliver(connection, batch_size=2)
        self.assertEqual(cursors_at_commit, [0, 2])
        self.assertEqual(self.cursor(), 3)
        self.assertEqual(result, {"acknowledged_versions": 3, "last_seq": 3})
        self.assertEqual(len(connection.remote), 3)

    def test_remote_write_failure_leaves_checkpoint_unchanged(self):
        self.add(2)
        connection = FakeConnection(fail_write_at=1)
        with self.assertRaisesRegex(RuntimeError, "write failure"):
            self.deliver(connection)
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(connection.remote, {})
        self.assertEqual(connection.commit_calls, 0)

    def test_remote_commit_failure_leaves_checkpoint_unchanged(self):
        self.add(2)
        connection = FakeConnection(fail_commit_at=1)
        with self.assertRaisesRegex(RuntimeError, "commit failure"):
            self.deliver(connection)
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(connection.remote, {})
        self.assertEqual(connection.pending, [])
        self.assertEqual(connection.rollback_count, 1)

    def test_failure_after_one_committed_batch_keeps_only_its_checkpoint(self):
        self.add(3)
        connection = FakeConnection(fail_commit_at=2)
        with self.assertRaisesRegex(RuntimeError, "commit failure"):
            self.deliver(connection, batch_size=2)
        self.assertEqual(self.cursor(), 2)
        self.assertEqual(len(connection.remote), 2)
        connection.fail_commit_at = None
        result = self.deliver(connection, batch_size=2)
        self.assertEqual(result["acknowledged_versions"], 1)
        self.assertEqual(self.cursor(), 3)
        self.assertEqual(len(connection.remote), 3)

    def test_local_checkpoint_failure_replays_committed_batch_idempotently(self):
        self.add(2)
        self.store.db.execute("""CREATE TRIGGER synthetic_checkpoint_failure BEFORE INSERT ON deliveries
            BEGIN SELECT RAISE(ABORT, 'synthetic checkpoint failure'); END""")
        self.store.db.commit()
        connection = FakeConnection()
        with self.assertRaises(sqlite3.IntegrityError):
            self.deliver(connection)
        self.assertEqual(len(connection.remote), 2)  # Remote data committed first.
        self.assertEqual(self.cursor(), 0)
        self.store.db.execute("DROP TRIGGER synthetic_checkpoint_failure")
        self.store.db.commit()
        self.store.close()
        self.store = Store(self.root / "outbox.sqlite3")
        result = self.deliver(connection)
        self.assertEqual(len(connection.attempted_batches), 2)
        self.assertEqual(connection.attempted_batches[0], connection.attempted_batches[1])
        self.assertEqual(len(connection.remote), 2)
        self.assertEqual(self.cursor(), 2)
        self.assertEqual(result["acknowledged_versions"], 2)

    def test_empty_outbox_does_not_advance_cursor_or_commit_batch(self):
        connection = FakeConnection()
        result = self.deliver(connection)
        self.assertEqual(result, {"acknowledged_versions": 0, "last_seq": 0})
        self.assertEqual(connection.write_calls, 0)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(self.cursor(), 0)

    def test_acknowledged_versions_are_not_sent_again(self):
        self.add(2)
        connection = FakeConnection()
        self.deliver(connection)
        writes = connection.write_calls
        result = self.deliver(connection)
        self.assertEqual(result["acknowledged_versions"], 0)
        self.assertEqual(result["last_seq"], 2)
        self.assertEqual(connection.write_calls, writes)

    def test_older_backfill_is_delivered_with_canonical_eligibility_false(self):
        with self.store.db:
            self.store.ingest(
                point(82), "google_health", "heart-rate", "synthetic-new", "2026-11-14T20:00:00Z"
            )
            self.store.ingest(
                point(78), "google_health", "heart-rate", "synthetic-old", "2026-11-14T18:00:00Z"
            )
        connection = FakeConnection()
        self.deliver(connection)
        transmitted = connection.attempted_batches[0]
        self.assertEqual([record[-1] for record in transmitted], [True, False])
        self.assertEqual(len({record[1] for record in transmitted}), 1)
        self.assertEqual(len(connection.remote), 2)
        self.assertEqual(list(self.store.measurements())[0]["value"], 82)
        for record in transmitted:
            self.assertIsInstance(json.loads(record[12]), dict)


if __name__ == "__main__":
    unittest.main()
