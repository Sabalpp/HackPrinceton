import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from farcare_collector.services.health_pipeline import run_refresh_command


class CloudShutdownTests(unittest.TestCase):
    def test_cancel_before_start_spawns_nothing(self):
        stop = threading.Event()
        stop.set()
        with patch("farcare_collector.services.health_pipeline.subprocess.Popen") as start:
            self.assertIsNone(run_refresh_command(["synthetic"], stop))
        start.assert_not_called()

    def test_running_connector_is_terminated_and_reaped_on_cancel(self):
        stop = threading.Event()
        process = Mock()
        process.poll.return_value = None

        def wait(timeout):
            if not stop.is_set():
                stop.set()
                raise subprocess.TimeoutExpired(["synthetic"], timeout)
            return -15

        process.wait.side_effect = wait
        with patch(
            "farcare_collector.services.health_pipeline.subprocess.Popen", return_value=process
        ):
            self.assertIsNone(run_refresh_command(["synthetic"], stop))
        process.terminate.assert_called_once()
        process.kill.assert_not_called()
        self.assertEqual(process.wait.call_count, 2)

    def test_timeout_kills_a_connector_that_ignores_termination(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired(["synthetic"], 3), -9]
        with patch(
            "farcare_collector.services.health_pipeline.subprocess.Popen", return_value=process
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_refresh_command(["synthetic"], threading.Event(), timeout=0)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
