"""Synthetic regression: minute reports must not stall incoming BLE records."""

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from farcare_collector.services import health_pipeline
from test_health_pipeline import ble_record


class RealtimeTests(unittest.TestCase):
    def test_slow_report_does_not_block_next_ble_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ble.jsonl"
            database = root / "outbox.sqlite3"
            source.write_text(json.dumps(ble_record(78, 0)) + "\n")
            report_started, release_report = threading.Event(), threading.Event()
            cycles = []

            def slow_report(*_args):
                report_started.set()
                if not release_report.wait(3):
                    raise AssertionError("Collector waited for the report before tailing BLE")
                return {"pipeline": {}, "generated_at": health_pipeline.now()}

            def next_cycle(delay):
                cycles.append(delay)
                self.assertTrue(report_started.wait(1))
                if len(cycles) == 1:
                    with source.open("a") as output:
                        # Equal BPM at a different timestamp is still a new sample.
                        output.write(json.dumps(ble_record(78, 1)) + "\n")
                else:
                    try:
                        with sqlite3.connect(database) as db:
                            self.assertEqual(
                                db.execute("SELECT count(*) FROM measurement_versions").fetchone()[
                                    0
                                ],
                                2,
                            )
                        self.assertFalse(release_report.is_set())
                    finally:
                        release_report.set()
                    raise KeyboardInterrupt

            args = [
                "farcare_collector.services.health_pipeline.py",
                "watch",
                "--cloud-every-seconds",
                "0",
                "--db",
                str(database),
                "--ble",
                str(source),
                "--output",
                str(root),
            ]
            try:
                with (
                    patch.object(health_pipeline, "ROOT", root),
                    patch.object(health_pipeline, "build_background_report", slow_report),
                    patch.object(health_pipeline.time, "sleep", next_cycle),
                    patch("sys.argv", args),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(health_pipeline.main(), 130)
            finally:
                release_report.set()
            self.assertEqual(len(cycles), 2)
            self.assertTrue(all(0 <= delay <= 1 for delay in cycles))


if __name__ == "__main__":
    unittest.main()
