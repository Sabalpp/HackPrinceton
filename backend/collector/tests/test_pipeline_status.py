"""Status snapshots stay private and complete when pipeline writers overlap."""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from farcare_collector.services.health_pipeline import private_json


class PrivateJsonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.destination = self.root / "status.json"

    def test_overlapping_writers_each_publish_complete_json(self):
        original = {"state": "previous"}
        private_json(self.destination, original)
        barrier = threading.Barrier(2)
        replace = Path.replace
        snapshots = [{"writer": number, "payload": str(number) * 10000} for number in range(2)]

        def publish_together(temporary, destination):
            # Both writers must finish their temp files before either renames one.
            self.assertEqual(json.loads(destination.read_text()), original)
            barrier.wait(timeout=5)
            return replace(temporary, destination)

        with patch.object(Path, "replace", publish_together), ThreadPoolExecutor(2) as workers:
            pending = [workers.submit(private_json, self.destination, value) for value in snapshots]
            for future in pending:
                future.result(timeout=10)

        self.assertIn(json.loads(self.destination.read_text()), snapshots)
        self.assertEqual(set(self.root.iterdir()), {self.destination})
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_serialization_failure_keeps_previous_snapshot_and_cleans_temp_file(self):
        private_json(self.destination, {"state": "previous"})
        previous = self.destination.read_bytes()
        for value, error in ((float("nan"), ValueError), (object(), TypeError)):
            with self.subTest(error=error.__name__), self.assertRaises(error):
                private_json(self.destination, {"invalid": value})
            self.assertEqual(self.destination.read_bytes(), previous)
            self.assertEqual(set(self.root.iterdir()), {self.destination})

    def test_replace_failure_keeps_previous_snapshot_and_cleans_temp_file(self):
        private_json(self.destination, {"state": "previous"})
        previous = self.destination.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("synthetic failure")):
            with self.assertRaises(PermissionError):
                private_json(self.destination, {"state": "new"})
        self.assertEqual(self.destination.read_bytes(), previous)
        self.assertEqual(set(self.root.iterdir()), {self.destination})

    def test_first_snapshot_creates_private_directory_and_file(self):
        destination = self.root / "private" / "status.json"
        private_json(destination, {"state": "ready"})
        self.assertEqual(json.loads(destination.read_text()), {"state": "ready"})
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
