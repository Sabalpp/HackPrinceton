"""Durability tests using synthetic API/BLE records and temporary SQLite files."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from farcare_collector.services.health_pipeline import Store

REV1 = "2026-11-14T18:00:00Z"
REV2 = "2026-11-14T19:00:00Z"
REV3 = "2026-11-14T20:00:00Z"


def ble_record(value=78, second=0):
    return {
        "receipt_timestamp": "2026-11-14T15:00:{:02d}Z".format(second),
        "characteristic_uuid": "00002a37-0000-1000-8000-00805f9b34fb",
        "payload_hex": (bytes([1]) + value.to_bytes(2, "little")).hex(),
        "bpm": value,
    }


def api_record(value=78, second=0):
    return {
        "dataSource": {"device": {"displayName": "Synthetic fixture"}, "platform": "FITBIT"},
        "heartRate": {
            "sampleTime": {"physicalTime": "2026-11-14T15:00:{:02d}Z".format(second)},
            "beatsPerMinute": str(value),
        },
    }


def sleep_record(minutes=1, updated=REV1, end="2026-11-14T15:01:00Z"):
    return {
        "name": "users/synthetic/sleep/test-session",
        "dataSource": {"device": {"displayName": "Synthetic fixture"}, "platform": "FITBIT"},
        "sleep": {
            "interval": {"startTime": "2026-11-14T15:00:00Z", "endTime": end},
            "type": "STAGES",
            "metadata": {"mainSleep": True, "nap": False},
            "summary": {"minutesAsleep": str(minutes)},
            "createTime": REV1,
            "updateTime": updated,
        },
    }


def line(record):
    return (json.dumps(record, sort_keys=True) + "\n").encode()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "store.sqlite3"
        self.store = Store(self.database_path)
        self.log = self.root / "heart_rate.jsonl"

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def ingest(
        self, record, revision=REV1, data_type="heart-rate", source="google_health", **kwargs
    ):
        with self.store.db:
            return self.store.ingest(
                record, source, data_type, "synthetic-input", revision, **kwargs
            )

    def export(self, records, revision=REV1, name="export", data_type="heart-rate"):
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        (directory / "summary.json").write_text(json.dumps({"started_at": revision}))
        subdirectory = directory / data_type
        subdirectory.mkdir(exist_ok=True)
        (subdirectory / "page-0001.json").write_text(json.dumps({"dataPoints": records}))
        return directory

    def table_count(self, table):
        return self.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def versions(self):
        return [
            json.loads(row[0])
            for row in self.store.db.execute(
                "SELECT payload FROM measurement_versions ORDER BY seq"
            )
        ]

    def test_partial_line_is_retried_only_after_newline_arrives(self):
        first, second = line(ble_record()), line(ble_record(79, 1))
        cut = len(second) // 2
        self.log.write_bytes(first + second[:cut])
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["byte_offset"], len(first))
        self.assertEqual(result["partial_line_pending"], 1)
        self.assertEqual(self.table_count("current_events"), 1)
        again = self.store.tail_ble(self.log)
        self.assertEqual(again.get("records", 0), 0)
        self.assertEqual(again["byte_offset"], len(first))
        with self.log.open("ab") as output:
            output.write(second[cut:])
        final = self.store.tail_ble(self.log)
        self.assertEqual(final["new_events"], 1)
        self.assertEqual(final["byte_offset"], len(first) + len(second))
        self.assertEqual([row["value"] for row in self.store.measurements()], [78, 79])
        self.assertEqual(self.table_count("quarantine"), 0)

    def test_valid_json_without_newline_remains_pending(self):
        encoded = line(ble_record())
        self.log.write_bytes(encoded[:-1])
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["byte_offset"], 0)
        self.assertEqual(self.table_count("raw_records"), 0)
        with self.log.open("ab") as output:
            output.write(b"\n")
        self.assertEqual(self.store.tail_ble(self.log)["new_events"], 1)

    def test_restart_resumes_committed_checkpoint_and_pending_line(self):
        first, second = line(ble_record()), line(ble_record(79, 1))
        self.log.write_bytes(first + second[:-1])
        self.store.tail_ble(self.log)
        self.store.close()
        self.store = Store(self.database_path)
        with self.log.open("ab") as output:
            output.write(b"\n")
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["new_events"], 1)
        self.assertEqual(self.table_count("measurement_versions"), 2)
        self.assertEqual(self.store.tail_ble(self.log).get("records", 0), 0)

    def test_rotation_replays_duplicates_and_ingests_new_records(self):
        first, second = line(ble_record()), line(ble_record(79, 1))
        self.log.write_bytes(first)
        self.store.tail_ble(self.log)
        replacement = self.root / "replacement.jsonl"
        replacement.write_bytes(first + second)
        replacement.replace(self.log)
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["rotation_or_truncation"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["new_events"], 1)
        self.assertEqual(self.table_count("measurement_versions"), 2)

    def test_same_inode_truncation_restarts_and_keeps_existing_measurements(self):
        self.log.write_bytes(line(ble_record()) + line(ble_record(79, 1)))
        self.store.tail_ble(self.log)
        inode = self.log.stat().st_ino
        self.log.write_bytes(line(ble_record(80, 2)))
        self.assertEqual(self.log.stat().st_ino, inode)
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["rotation_or_truncation"], 1)
        self.assertEqual(result["new_events"], 1)
        self.assertEqual(self.table_count("current_events"), 3)

    def test_same_size_rewrite_with_changed_prefix_restarts(self):
        first, replacement = line(ble_record(78, 0)), line(ble_record(79, 1))
        self.assertEqual(len(first), len(replacement))
        self.log.write_bytes(first)
        self.store.tail_ble(self.log)
        self.log.write_bytes(replacement)
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["rotation_or_truncation"], 1)
        self.assertEqual(self.table_count("current_events"), 2)

    def test_invalid_complete_lines_quarantine_without_blocking_next_record(self):
        wrong_bpm = ble_record()
        wrong_bpm["bpm"] = 100  # Disagrees with the verified notification bytes.
        data = b"{incomplete-but-newline-terminated\n" + line(wrong_bpm) + line(ble_record(79, 1))
        self.log.write_bytes(data)
        result = self.store.tail_ble(self.log)
        self.assertEqual(result["quarantined"], 2)
        self.assertEqual(result["byte_offset"], len(data))
        self.assertEqual(self.table_count("quarantine"), 2)
        self.assertEqual(self.table_count("current_events"), 1)
        self.assertEqual(self.store.tail_ble(self.log).get("records", 0), 0)

    def test_conflicting_ble_payload_is_quarantined_and_event_remains_immutable(self):
        self.log.write_bytes(line(ble_record(78, 0)))
        self.store.tail_ble(self.log)
        self.store.close()
        self.store = Store(self.database_path)
        with self.log.open("ab") as output:
            output.write(
                line(ble_record(79, 0))
            )  # Same event identity, different verified payload.
            output.write(line(ble_record(80, 1)))
        result = self.store.tail_ble(self.log)
        self.assertEqual(result.get("quarantined", 0), 1)
        self.assertEqual(self.table_count("quarantine"), 1)
        self.assertEqual(self.table_count("measurement_versions"), 2)
        self.assertEqual(self.table_count("current_events"), 2)
        self.assertEqual([row["value"] for row in self.store.measurements()], [78, 80])
        self.assertEqual(self.table_count("raw_records"), 3)
        self.assertEqual(self.store.tail_ble(self.log).get("records", 0), 0)

    def test_api_import_is_idempotent_after_restart(self):
        directory = self.export([api_record(), api_record(79, 1)])
        first = self.store.import_api(directory)
        self.assertEqual(first["new_events"], 2)
        self.store.close()
        self.store = Store(self.database_path)
        second = self.store.import_api(directory)
        self.assertEqual(second["unchanged_files"], 1)
        self.assertEqual(self.table_count("measurement_versions"), 2)

    def test_unchanged_content_from_new_export_does_not_create_version(self):
        first = self.export([api_record()], revision=REV1, name="first")
        second = self.export([api_record()], revision=REV3, name="second")
        self.store.import_api(first)
        result = self.store.import_api(second)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(self.table_count("measurement_versions"), 1)
        watermark = self.store.db.execute(
            "SELECT latest_revision_epoch FROM current_events"
        ).fetchone()[0]
        self.assertGreater(
            watermark,
            self.store.db.execute("SELECT revision_epoch FROM measurement_versions").fetchone()[0],
        )

    def test_same_path_unchanged_page_advances_watermark_without_new_version(self):
        directory = self.export([api_record(78)], revision=REV1)
        self.store.import_api(directory)
        self.export([api_record(78)], revision=REV3)
        self.store.import_api(directory)
        self.assertEqual(self.table_count("measurement_versions"), 1)
        self.ingest(api_record(79), revision=REV2)
        self.assertEqual(list(self.store.measurements())[0]["value"], 78)
        self.assertFalse(self.versions()[-1]["canonical_eligible"])

    def test_newer_correction_wins_and_old_replay_cannot_revert_it(self):
        original, correction = api_record(78), api_record(82)
        self.ingest(original, REV1)
        self.ingest(correction, REV3)
        before = self.table_count("measurement_versions")
        result = self.ingest(original, REV1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(self.table_count("measurement_versions"), before)
        (current,) = self.store.measurements()
        self.assertEqual(current["value"], 82)
        self.assertEqual(len({row["event_id"] for row in self.versions()}), 1)

    def test_unseen_older_backfill_retained_but_never_current(self):
        self.ingest(api_record(82), REV3)
        result = self.ingest(api_record(78), REV1)
        self.assertEqual(result["older_versions_retained"], 1)
        self.assertEqual(self.table_count("measurement_versions"), 2)
        self.assertEqual(list(self.store.measurements())[0]["value"], 82)
        self.assertFalse(self.versions()[-1]["canonical_eligible"])

    def test_content_duplicate_watermark_blocks_intermediate_old_correction(self):
        self.ingest(api_record(78), REV1)
        self.ingest(api_record(78), REV3)
        self.ingest(api_record(79), REV2)
        self.assertEqual(list(self.store.measurements())[0]["value"], 78)
        self.assertEqual(self.table_count("measurement_versions"), 2)
        self.assertFalse(self.versions()[-1]["canonical_eligible"])

    def test_explicit_source_update_time_beats_export_fetch_time(self):
        original = sleep_record(updated=REV1)
        correction = sleep_record(minutes=2, updated=REV2, end="2026-11-14T15:02:00Z")
        self.ingest(correction, revision=REV2, data_type="sleep")
        self.ingest(original, revision=REV3, data_type="sleep")
        (current,) = self.store.measurements()
        self.assertEqual(current["value"], 2)
        self.assertTrue(current["ended_at"].startswith("2026-11-14T15:02:00"))
        self.assertFalse(self.versions()[-1]["canonical_eligible"])

    def test_ble_batch_failure_rolls_back_records_and_checkpoint_together(self):
        self.log.write_bytes(line(ble_record()) + line(ble_record(79, 1)))
        self.store.db.execute("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON measurement_versions
            WHEN (SELECT count(*) FROM measurement_versions) >= 1
            BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.tail_ble(self.log)
        self.assertEqual(self.table_count("raw_records"), 0)
        self.assertEqual(self.table_count("measurement_versions"), 0)
        self.assertEqual(self.table_count("current_events"), 0)
        self.assertEqual(self.table_count("offsets"), 0)
        self.store.db.execute("DROP TRIGGER synthetic_failure")
        self.store.db.commit()
        self.assertEqual(self.store.tail_ble(self.log)["new_events"], 2)

    def test_api_page_failure_rolls_back_import_marker_and_entire_page(self):
        directory = self.export([api_record(), api_record(79, 1)])
        self.store.db.execute("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON measurement_versions
            WHEN (SELECT count(*) FROM measurement_versions) >= 1
            BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.import_api(directory)
        self.assertEqual(self.table_count("raw_records"), 0)
        self.assertEqual(self.table_count("measurement_versions"), 0)
        self.assertEqual(self.table_count("current_events"), 0)
        self.assertEqual(self.table_count("imported_files"), 0)
        self.store.db.execute("DROP TRIGGER synthetic_failure")
        self.store.db.commit()
        self.assertEqual(self.store.import_api(directory)["new_events"], 2)

    def test_schema_invalid_api_point_is_quarantined_without_losing_valid_point(self):
        invalid = copy.deepcopy(api_record())
        invalid["heartRate"]["beatsPerMinute"] = "not-a-number"
        directory = self.export([invalid, api_record(79, 1)])
        result = self.store.import_api(directory)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(result["new_events"], 1)
        self.assertEqual(self.table_count("quarantine"), 1)
        self.assertEqual(self.table_count("current_events"), 1)


if __name__ == "__main__":
    unittest.main()
