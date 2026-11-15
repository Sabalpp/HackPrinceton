"""Archive transaction tests; temporary SQLite only, PostgreSQL always mocked."""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from farcare_collector.integrations import tiger_archive
from farcare_collector.services.health_pipeline import Store

TARGET = "synthetic-health-target"
PRIVATE_PATH = "/private/fixture/source/location/never-transmit.json"
FIRST_SEEN = "2026-11-14T19:00:00.123456Z"


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def executemany(self, statement, values):
        connection = self.connection
        connection.write_calls += 1
        if connection.fail_write_at == connection.write_calls:
            raise RuntimeError("synthetic archive write failure")
        rows = list(values)
        connection.attempted_batches.append(rows)
        connection.statements.append(statement)
        ignore_conflict = "ON CONFLICT (raw_hash) DO NOTHING" in statement
        connection.pending.extend((row, ignore_conflict) for row in rows)


class FakeConnection:
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

    def execute(self, statement):
        self.statements.append(statement)

    def commit(self):
        self.commit_calls += 1
        if self.before_commit:
            self.before_commit()
        if self.fail_commit_at == self.commit_calls:
            raise RuntimeError("synthetic archive commit failure")
        next_state = dict(self.remote)
        for row, ignore_conflict in self.pending:
            if row[0] in next_state and not ignore_conflict:
                raise RuntimeError("synthetic archive uniqueness violation")
            next_state.setdefault(row[0], row)
        self.remote = next_state
        self.pending.clear()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "archive.sqlite3"
        self.store = Store(self.database)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def add_raw(self, payload=b'{"bpm":78}\n', source="ble", data_type="heart-rate"):
        raw_hash = hashlib.sha256(payload).hexdigest()
        with self.store.db:
            cursor = self.store.db.execute(
                "INSERT OR IGNORE INTO raw_records VALUES (?,?,?,?,?,?)",
                (raw_hash, source, data_type, payload, PRIVATE_PATH, FIRST_SEEN),
            )
        return raw_hash, cursor.lastrowid

    def add(self, count):
        for index in range(count):
            self.add_raw(('{"fixture_index":%d}\n' % index).encode())

    def checkpoint(self, key=TARGET + ":raw"):
        row = self.store.db.execute(
            "SELECT last_seq FROM deliveries WHERE target=?", (key,)
        ).fetchone()
        return row[0] if row else 0

    def deliver(self, connection, **kwargs):
        # Patching both functions prevents credentials reads and network access.
        with (
            patch.object(tiger_archive, "connect", return_value=connection),
            patch.object(tiger_archive, "target_key", return_value=TARGET),
        ):
            return tiger_archive.deliver_archive(
                self.store, "unused-private-credentials.env", **kwargs
            )

    def test_mixed_payloads_are_preserved_losslessly(self):
        json_bytes = '{ "unicode": "é", "bpm": 78 }\r\n'.encode("utf-8")
        xml_bytes = b'<?xml version="1.0"?>\r\n<TrainingCenterDatabase><Value>78</Value></TrainingCenterDatabase>\r\n'
        invalid_bytes = b"\xff\x00malformed BLE JSON\r\n"
        json_hash, _ = self.add_raw(json_bytes, "google_health")
        xml_hash, _ = self.add_raw(xml_bytes, "google_health", "exercise-tcx")
        invalid_hash, _ = self.add_raw(invalid_bytes)
        connection = FakeConnection()
        result = self.deliver(connection)
        self.assertEqual(result, {"acknowledged_records": 3, "last_rowid": 3})
        for raw_hash, payload, content_type in (
            (json_hash, json_bytes, "application/json"),
            (xml_hash, xml_bytes, "application/xml"),
            (invalid_hash, invalid_bytes, "application/octet-stream"),
        ):
            record = connection.remote[raw_hash]
            self.assertEqual(record[3], payload)
            self.assertIsInstance(record[3], bytes)
            self.assertEqual(record[4], content_type)
            self.assertEqual(record[5], FIRST_SEEN)

    def test_commit_precedes_each_local_checkpoint(self):
        self.add(3)
        checkpoints_at_commit = []
        connection = FakeConnection(
            before_commit=lambda: checkpoints_at_commit.append(self.checkpoint())
        )
        self.assertEqual(
            self.deliver(connection, batch_size=2), {"acknowledged_records": 3, "last_rowid": 3}
        )
        self.assertEqual(checkpoints_at_commit, [0, 2])
        self.assertEqual(self.checkpoint(), 3)

    def test_remote_write_failure_does_not_acknowledge(self):
        self.add(2)
        connection = FakeConnection(fail_write_at=1)
        with self.assertRaisesRegex(RuntimeError, "write failure"):
            self.deliver(connection)
        self.assertEqual(self.checkpoint(), 0)
        self.assertEqual(connection.remote, {})
        self.assertEqual(connection.commit_calls, 0)

    def test_remote_commit_failure_does_not_acknowledge(self):
        self.add(2)
        connection = FakeConnection(fail_commit_at=1)
        with self.assertRaisesRegex(RuntimeError, "commit failure"):
            self.deliver(connection)
        self.assertEqual(self.checkpoint(), 0)
        self.assertEqual(connection.remote, {})
        self.assertEqual(connection.pending, [])
        self.assertEqual(connection.rollback_count, 1)

    def test_failure_after_committed_batch_resumes_remaining_rows(self):
        self.add(3)
        connection = FakeConnection(fail_commit_at=2)
        with self.assertRaisesRegex(RuntimeError, "commit failure"):
            self.deliver(connection, batch_size=2)
        self.assertEqual(self.checkpoint(), 2)
        self.assertEqual(len(connection.remote), 2)
        connection.fail_commit_at = None
        self.assertEqual(
            self.deliver(connection, batch_size=2), {"acknowledged_records": 1, "last_rowid": 3}
        )
        self.assertEqual(len(connection.remote), 3)

    def test_committed_batch_replays_after_local_checkpoint_failure(self):
        self.add(2)
        self.store.db.execute("""CREATE TRIGGER archive_checkpoint_failure BEFORE INSERT ON deliveries
            BEGIN SELECT RAISE(ABORT, 'synthetic local archive checkpoint failure'); END""")
        self.store.db.commit()
        connection = FakeConnection()
        with self.assertRaises(sqlite3.IntegrityError):
            self.deliver(connection)
        self.assertEqual(len(connection.remote), 2)
        self.assertEqual(self.checkpoint(), 0)
        self.store.db.execute("DROP TRIGGER archive_checkpoint_failure")
        self.store.db.commit()
        self.store.close()
        self.store = Store(self.database)
        self.assertEqual(self.deliver(connection), {"acknowledged_records": 2, "last_rowid": 2})
        self.assertEqual(connection.attempted_batches[0], connection.attempted_batches[1])
        self.assertEqual(len(connection.remote), 2)

    def test_archive_checkpoint_is_independent_from_numeric_sink(self):
        self.add(2)
        with self.store.db:
            self.store.db.execute("INSERT INTO deliveries VALUES (?,?)", (TARGET, 9000))
        connection = FakeConnection()
        self.deliver(connection)
        self.assertEqual(self.checkpoint(), 2)
        self.assertEqual(self.checkpoint(TARGET), 9000)
        self.assertEqual(len(connection.remote), 2)

    def test_paths_and_credentials_are_never_transmitted(self):
        payload = b'{"user_health_label":"quoted value; DROP TABLE example; --"}'
        self.add_raw(payload)
        connection = FakeConnection()
        self.deliver(connection)
        self.assertEqual(len(connection.attempted_batches[0][0]), 6)
        transmitted = repr(connection.attempted_batches) + repr(connection.statements)
        self.assertNotIn(PRIVATE_PATH, transmitted)
        self.assertNotIn("unused-private-credentials.env", transmitted)
        self.assertNotIn("source_file", "\n".join(connection.statements))
        statement = connection.statements[0]
        self.assertEqual(statement.count("%s"), 6)
        self.assertNotIn("DROP TABLE", statement)
        self.assertIn("ON CONFLICT (raw_hash) DO NOTHING", statement)

    def test_empty_and_already_acknowledged_records_do_not_rewrite(self):
        connection = FakeConnection()
        self.assertEqual(self.deliver(connection), {"acknowledged_records": 0, "last_rowid": 0})
        self.assertEqual(connection.commit_calls, 0)
        self.add(2)
        self.deliver(connection)
        calls = connection.write_calls
        self.assertEqual(self.deliver(connection), {"acknowledged_records": 0, "last_rowid": 2})
        self.assertEqual(connection.write_calls, calls)

    def test_nonfinite_json_and_invalid_utf8_are_binary(self):
        payloads = (b'{"bad":NaN}', b'{"bad":Infinity}', b"\xffnot-json", b"")
        for payload in payloads:
            self.add_raw(payload)
        connection = FakeConnection()
        self.deliver(connection)
        self.assertEqual({row[3] for row in connection.remote.values()}, set(payloads))
        self.assertTrue(
            all(row[4] == "application/octet-stream" for row in connection.remote.values())
        )

    def test_text_storage_is_rejected_without_changing_bytes(self):
        self.add(1)
        with self.store.db:
            self.store.db.execute("UPDATE raw_records SET payload='accidental SQLite TEXT'")
        connection = FakeConnection()
        with self.assertRaisesRegex(ValueError, "stored as bytes"):
            self.deliver(connection)
        self.assertEqual(self.checkpoint(), 0)
        self.assertEqual(connection.remote, {})

    def test_invalid_batch_size_fails_before_credentials_or_connection(self):
        with (
            patch.object(tiger_archive, "connect") as connection,
            patch.object(tiger_archive, "target_key") as target,
        ):
            for size in (0, -1, True, 1.5, "1000", None):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    tiger_archive.deliver_archive(
                        self.store, "unused-private-credentials.env", batch_size=size
                    )
            connection.assert_not_called()
            target.assert_not_called()

    def test_schema_is_additive_regular_postgresql_with_binary_payload(self):
        connection = FakeConnection()
        with patch.object(tiger_archive, "connect", return_value=connection):
            tiger_archive.apply_archive_schema("unused-private-credentials.env")
        self.assertEqual(len(connection.statements), 1)
        schema = connection.statements[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS air_health.source_records", schema)
        self.assertIn("raw_hash text PRIMARY KEY", schema)
        self.assertIn("payload bytea NOT NULL", schema)
        self.assertNotIn("hypertable", schema)
        self.assertNotIn("DROP ", schema)
        self.assertNotIn("source_file", schema)


if __name__ == "__main__":
    unittest.main()
