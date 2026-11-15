"""Synthetic supervisor tests. No databases, providers or real subprocesses."""

import io
import unittest
from unittest.mock import patch

import supervise


class Child:
    def __init__(self, exited=False):
        self.exited = exited
        self.terminated = False

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.terminated = True
        self.exited = True

    def wait(self, timeout=None):
        return 0


class Stop:
    def is_set(self):
        return False

    def wait(self, _seconds):
        return False


class SupervisorTests(unittest.TestCase):
    def test_sync_is_opt_in_with_explicit_person_mapping(self):
        self.assertEqual(len(supervise.commands({})), 2)
        with self.assertRaises(ValueError):
            supervise.commands({"TIGER_SYNC_ENABLED": "true"})
        plans = supervise.commands(
            {
                "TIGER_SYNC_ENABLED": "true",
                "TIGER_SUBJECT_ID": "synthetic-subject",
                "TIGER_PARENT_ID": "synthetic-parent",
            }
        )
        self.assertEqual(len(plans), 3)
        self.assertIn("synthetic-subject", plans[2])
        self.assertIn("synthetic-parent", plans[2])
        self.assertIn("http://127.0.0.1:8000", plans[2])

    def test_worker_exit_stops_api_and_marks_container_failed(self):
        api, worker = Child(), Child(exited=True)
        with (
            patch.object(supervise.threading, "Event", return_value=Stop()),
            patch.object(supervise.signal, "signal"),
            patch.object(
                supervise.subprocess, "Popen", side_effect=[api, worker]
            ) as spawn,
            patch.object(
                supervise, "urlopen", return_value=io.BytesIO(b'{"database":"ok"}')
            ),
            patch.dict(supervise.os.environ, {}, clear=True),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(supervise.main(), 1)
        self.assertEqual(spawn.call_count, 2)
        self.assertTrue(api.terminated)

    def test_api_exit_prevents_starting_or_orphaning_worker(self):
        api = Child(exited=True)
        with (
            patch.object(supervise.threading, "Event", return_value=Stop()),
            patch.object(supervise.signal, "signal"),
            patch.object(supervise.subprocess, "Popen", return_value=api) as spawn,
            patch.dict(supervise.os.environ, {}, clear=True),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(supervise.main(), 1)
        self.assertEqual(spawn.call_count, 1)


if __name__ == "__main__":
    unittest.main()
