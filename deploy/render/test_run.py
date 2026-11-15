"""Synthetic native-host lifecycle tests; no providers, personal data or sockets."""

import io
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import run


class NativeHostTests(unittest.TestCase):
    def test_render_origin_is_used_without_loosening_browser_policy(self):
        env, port = run.settings({"RENDER_EXTERNAL_URL": "https://care.onrender.com/"})
        self.assertEqual(env["CORS_ORIGINS"], "https://care.onrender.com")
        self.assertEqual(port, 10000)
        self.assertEqual(env["BACKGROUND_TASKS_ENABLED"], "false")
        for origin in (
            "",
            "http://care.onrender.com",
            "https://*.onrender.com",
            "https://care.onrender.com/path",
            "https://user:secret@care.onrender.com",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                run.settings({"RENDER_EXTERNAL_URL": origin})

    def test_explicit_custom_origin_is_preserved_and_ports_cannot_collide(self):
        env, port = run.settings(
            {"CORS_ORIGINS": "https://care.example", "PORT": "13000"}
        )
        self.assertEqual(env["CORS_ORIGINS"], "https://care.example")
        self.assertEqual(port, 13000)
        for port in ("8000", "443", "65536", "bad"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                run.settings({"PORT": port})

    def test_worker_failure_stops_all_children(self):
        children = [MagicMock(pid=101), MagicMock(pid=102), MagicMock(pid=103)]
        for child in children:
            child.poll.return_value = None
        children[1].poll.return_value = 1
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        with (
            patch.object(run.threading, "Event", return_value=stop),
            patch.object(run.signal, "signal"),
            patch.object(run.shutil, "which", return_value="/synthetic/node"),
            patch.dict(
                run.os.environ, {"CORS_ORIGINS": "https://care.example"}, clear=True
            ),
            patch.object(run.subprocess, "Popen", side_effect=children) as spawn,
            patch.object(run, "wait_api") as wait,
            patch.object(run.os, "killpg") as kill,
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            self.assertEqual(run.main(), 1)
        wait.assert_called_once_with(children[0], stop)
        self.assertEqual(spawn.call_count, 3)
        self.assertTrue(
            all(call.kwargs["start_new_session"] for call in spawn.call_args_list)
        )
        api_command = spawn.call_args_list[0].args[0]
        self.assertEqual(api_command[api_command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(
            {call.args[0] for call in kill.call_args_list}, {101, 102, 103}
        )
        for child in children:
            child.wait.assert_called_once()

    def test_failed_database_start_does_not_launch_worker_or_web(self):
        child = MagicMock(pid=101)
        with (
            patch.object(run.signal, "signal"),
            patch.object(run.shutil, "which", return_value="/synthetic/node"),
            patch.dict(
                run.os.environ, {"CORS_ORIGINS": "https://care.example"}, clear=True
            ),
            patch.object(run.subprocess, "Popen", return_value=child) as spawn,
            patch.object(run, "wait_api", side_effect=RuntimeError("private detail")),
            patch.object(run.os, "killpg"),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(run.main(), 1)
            self.assertNotIn("private detail", stderr.getvalue())
        spawn.assert_called_once()
        child.wait.assert_called_once()

    def test_stubborn_process_group_is_killed_and_reaped(self):
        child = MagicMock(pid=101)
        child.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 20), 0]
        with patch.object(run.os, "killpg") as kill:
            run.stop_children([child])
        self.assertEqual(kill.call_args_list[-1].args, (101, run.signal.SIGKILL))
        self.assertEqual(child.wait.call_count, 2)

    def test_api_must_have_database_connection_before_other_processes_start(self):
        process = MagicMock()
        process.poll.return_value = None
        stop = MagicMock()
        stop.is_set.return_value = False
        with patch.object(
            run,
            "urlopen",
            side_effect=[
                io.BytesIO(b'{"database":"unavailable"}'),
                io.BytesIO(b'{"database":"ok"}'),
            ],
        ) as health:
            run.wait_api(process, stop)
        self.assertEqual(health.call_count, 2)
        stop.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
