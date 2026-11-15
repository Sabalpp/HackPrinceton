"""Regression probes for replay consistency; all inputs and databases are temporary."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from farcare_collector.services.health_pipeline import Store, timestamp


def sample(value):
    return {
        "dataSource": {"platform": "FITBIT", "device": {"displayName": "Synthetic replay fixture"}},
        "heartRate": {
            "sampleTime": {"physicalTime": "2026-11-14T15:00:00Z"},
            "beatsPerMinute": str(value),
        },
    }


class ReplayConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "synthetic.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_non_object_quarantine_does_not_abort_other_recoverable_records(self):
        with patch(
            "farcare_collector.health.health_normalize.normalize_api_record",
            side_effect=ValueError("synthetic old schema"),
        ):
            with self.store.db:
                self.store.ingest(
                    sample(78),
                    "google_health",
                    "heart-rate",
                    self.root / "missing-export.json",
                    "2026-11-14T18:00:00Z",
                )
        log = self.root / "synthetic.jsonl"
        log.write_text("[1, 2, 3]\n")
        self.store.tail_ble(log)
        self.assertEqual(self.store.summary()["quarantine"], 2)
        self.store.retry_quarantine()
        self.assertEqual(self.store.summary()["current_events"], 1)
        self.assertEqual(self.store.summary()["quarantine"], 1)

    def test_equal_revision_replay_agrees_with_remote_current_view_order(self):
        with self.store.db:
            self.store.ingest(
                sample(78),
                "google_health",
                "heart-rate",
                "synthetic-current",
                "2026-11-14T18:00:00Z",
                received_at="2026-11-14T20:00:00Z",
            )
            # Quarantine replay can arrive later while preserving an older first_seen.
            self.store.ingest(
                sample(79),
                "google_health",
                "heart-rate",
                "synthetic-replay",
                "2026-11-14T18:00:00Z",
                received_at="2026-11-14T19:00:00Z",
            )
        versions = [
            json.loads(row[0])
            for row in self.store.db.execute("SELECT payload FROM measurement_versions")
        ]
        eligible = [row for row in versions if row["canonical_eligible"]]
        remote_current = max(
            eligible,
            key=lambda row: (
                timestamp(row["source_revision_at"]),
                timestamp(row["received_at"]),
                row["version_hash"],
            ),
        )
        (local_current,) = self.store.measurements()
        self.assertEqual(local_current["version_hash"], remote_current["version_hash"])
        self.assertEqual(local_current["value"], 78)


if __name__ == "__main__":
    unittest.main()
