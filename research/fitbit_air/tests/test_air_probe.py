"""Offline probe lifecycle checks; synthetic bytes are confined to temp directories."""

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from research.fitbit_air import air_probe as probe

TX = "abbaff01-e56a-484c-b832-8b17cf6cbfe8"
RX = "abbaff02-e56a-484c-b832-8b17cf6cbfe8"
SERVICE = "abbaff00-e56a-484c-b832-8b17cf6cbfe8"


class FakeClient:
    def __init__(self, packets=(), fail_connect=False, fail_subscribe=False, cleanup_packet=None):
        self.packets = packets
        self.fail_connect = fail_connect
        self.fail_subscribe = fail_subscribe
        self.cleanup_packet = cleanup_packet
        self.is_connected = False
        self.subscribed = asyncio.Event()
        self.rx = SimpleNamespace(uuid=RX, handle=67, properties=["notify"])
        self.services = [
            SimpleNamespace(
                uuid=SERVICE,
                handle=64,
                characteristics=[
                    SimpleNamespace(uuid=TX, handle=65, properties=["write-without-response"]),
                    self.rx,
                ],
            ),
            SimpleNamespace(
                uuid="0000180a-0000-1000-8000-00805f9b34fb",
                handle=16,
                characteristics=[
                    SimpleNamespace(
                        uuid="00002a26-0000-1000-8000-00805f9b34fb", handle=25, properties=["read"]
                    )
                ],
            ),
        ]
        self.write_gatt_char = AsyncMock(
            side_effect=AssertionError("Application writes are forbidden in M1")
        )
        self.read_gatt_char = AsyncMock(return_value=b"fixture-firmware")
        self.stop_notify = AsyncMock(side_effect=self.stop)
        self.disconnect = AsyncMock(side_effect=self.disconnect_now)

    def factory(self, device, **kwargs):
        self.on_disconnect = kwargs["disconnected_callback"]
        return self

    async def connect(self):
        if self.fail_connect:
            raise RuntimeError("fixture connect failure")
        self.is_connected = True

    async def start_notify(self, char, callback):
        if self.fail_subscribe:
            raise RuntimeError("fixture subscription failure")
        self.callback = callback
        for packet in self.packets:
            callback(char, packet)
        self.subscribed.set()

    def stop(self, char):
        if self.cleanup_packet is not None:
            self.callback(self.rx, self.cleanup_packet)

    def disconnect_now(self):
        self.is_connected = False
        self.on_disconnect(self)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.device = SimpleNamespace(name="Google Fitbit Air", address="fixture-device")

    async def run_probe(self, client, seconds=0.01):
        with contextlib.redirect_stdout(io.StringIO()):
            return await probe.probe(self.device, seconds, client.factory, None, self.directory)

    def report(self):
        return json.loads((self.directory / "air_probe.json").read_text())

    def frames(self):
        return [
            json.loads(row)
            for row in (self.directory / "air_probe_frames.jsonl").read_text().splitlines()
        ]

    async def test_records_raw_bytes_and_gatt_properties_without_application_writes(self):
        client = FakeClient([b"\x80\x01", b"\x00\xff"])
        report = await self.run_probe(client)
        frames = self.frames()
        self.assertEqual([row["hex"] for row in frames], ["8001", "00ff"])
        self.assertTrue(all(row["run_id"] == report["run_id"] for row in frames))
        self.assertEqual(report["frame_counts"][RX], 2)
        self.assertEqual(report["proprietary_frames"], 2)
        self.assertEqual(report["device_info"]["firmware_rev"]["hex"], b"fixture-firmware".hex())
        self.assertEqual(report["services"][0]["characteristics"][0]["uuid"], TX)
        self.assertEqual(report["state"], "complete")
        client.write_gatt_char.assert_not_awaited()
        client.stop_notify.assert_awaited_once_with(RX)
        client.disconnect.assert_awaited_once()

    async def test_silence_is_a_completed_observation_with_zero_frames(self):
        report = await self.run_probe(FakeClient())
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["total_frames"], 0)
        self.assertEqual(report["proprietary_frames"], 0)
        self.assertEqual(report["subscribed"], [RX])
        self.assertEqual(self.frames(), [])

    async def test_failed_connection_still_records_error_and_disconnects(self):
        client = FakeClient(fail_connect=True)
        with self.assertRaisesRegex(RuntimeError, "connect failure"):
            await self.run_probe(client)
        self.assertEqual(self.report()["state"], "error")
        self.assertEqual(self.report()["phase"], "connecting")
        client.disconnect.assert_awaited_once()

    async def test_failed_subscriptions_are_not_misreported_as_silent_device(self):
        client = FakeClient(fail_subscribe=True)
        with self.assertRaisesRegex(RuntimeError, "No notification subscriptions"):
            await self.run_probe(client)
        report = self.report()
        self.assertEqual(report["state"], "error")
        self.assertIn(RX, report["subscribe_errors"])
        self.assertEqual(report["subscribed"], [])

    async def test_read_error_does_not_prevent_notification_capture(self):
        client = FakeClient([b"\x01"])
        client.read_gatt_char.side_effect = RuntimeError("fixture read denied")
        report = await self.run_probe(client)
        self.assertEqual(report["total_frames"], 1)
        self.assertEqual(len(report["read_errors"]), 1)

    async def test_frames_arriving_during_cleanup_are_retained_before_file_close(self):
        client = FakeClient([b"\x01"], cleanup_packet=b"\x02")
        report = await self.run_probe(client)
        self.assertEqual([row["hex"] for row in self.frames()], ["01", "02"])
        self.assertEqual(report["total_frames"], 2)
        # A late callback after disconnect must not attempt to write a closed file.
        client.callback(client.rx, b"\x03")
        self.assertEqual(len(self.frames()), 2)

    async def test_early_disconnect_is_distinct_from_completed_listening(self):
        client = FakeClient()
        task = asyncio.create_task(self.run_probe(client, seconds=60))
        await client.subscribed.wait()
        client.disconnect_now()
        report = await asyncio.wait_for(task, 0.5)
        self.assertEqual(report["state"], "disconnected")
        self.assertLess(report["observed_listen_seconds"], 60)

    async def test_cancel_preserves_partial_report_and_disconnects(self):
        client = FakeClient([b"\x04"])
        task = asyncio.create_task(self.run_probe(client, seconds=60))
        # Cancel during the listening phase, rather than racing Python 3.9's
        # wait_for completion of start_notify.
        for _ in range(100):
            await asyncio.sleep(0)
            if task.done():
                await task
            if (self.directory / "air_probe.json").exists() and self.report().get(
                "phase"
            ) == "listening":
                break
        else:
            self.fail("Probe did not reach the listening phase")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        self.assertEqual(self.report()["state"], "interrupted")
        self.assertEqual(self.frames()[0]["hex"], "04")
        client.disconnect.assert_awaited_once()

    async def test_cancellation_during_subscription_completion_is_not_swallowed(self):
        client = FakeClient([b"\x04"])
        task = asyncio.create_task(self.run_probe(client, seconds=60))
        await client.subscribed.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        self.assertEqual(self.report()["state"], "interrupted")
        client.disconnect.assert_awaited_once()

    async def test_bounded_operation_cancels_on_timeout(self):
        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        with self.assertRaises(asyncio.TimeoutError):
            await probe.bounded(slow(), 0.01)
        self.assertTrue(cancelled.is_set())

    async def test_existing_capture_is_appended_with_distinct_run_id(self):
        first = await self.run_probe(FakeClient([b"\x01"]))
        second = await self.run_probe(FakeClient([b"\x02"]))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(self.frames()), 2)
        self.assertEqual(second["total_frames"], 1)

    async def test_explicit_device_does_not_fall_back_to_an_unrelated_sensor(self):
        scanner = SimpleNamespace(
            find_device_by_address=AsyncMock(return_value=None), discover=AsyncMock()
        )
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "not found"),
        ):
            await probe.find_air(scanner, None, "selected-device")
        scanner.discover.assert_not_awaited()

    async def test_generic_hr_advertiser_is_never_auto_probed(self):
        other = SimpleNamespace(name="Other-brand HR strap", address="not-our-band")
        adv = SimpleNamespace(local_name=other.name, service_uuids=[probe.HR_SERVICE])
        scanner = SimpleNamespace(discover=AsyncMock(return_value={"other": (other, adv)}))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "found 0"),
        ):
            await probe.find_air(scanner, None)

    async def test_disconnect_failure_cannot_claim_a_clean_complete_run(self):
        client = FakeClient([b"\x01"])
        client.disconnect.side_effect = RuntimeError("fixture disconnect failure")
        with contextlib.redirect_stderr(io.StringIO()):
            report = await self.run_probe(client)
        self.assertEqual(report["state"], "partial")
        self.assertEqual(report["capture_state"], "complete")
        self.assertTrue(report["cleanup_errors"])
        self.assertTrue(client.is_connected)
        self.assertEqual(report["total_frames"], 1)

    async def test_flush_failure_does_not_replay_a_consumed_frame(self):
        client = FakeClient()

        async def subscribe(char, callback):
            client.callback = callback
            asyncio.get_running_loop().call_later(0.002, callback, char, b"\x80")

        client.start_notify = subscribe
        original_fdopen = probe.os.fdopen

        class FailFirstFlush:
            def __init__(self, stream):
                self.stream = stream
                self.failed = False

            def write(self, value):
                return self.stream.write(value)

            def flush(self):
                if not self.failed:
                    self.failed = True
                    raise OSError("fixture transient flush failure")
                self.stream.flush()

            def close(self):
                self.stream.close()

        def wrapped(fd, mode, *args, **kwargs):
            stream = original_fdopen(fd, mode, *args, **kwargs)
            return FailFirstFlush(stream) if mode == "a" else stream

        with patch.object(probe.os, "fdopen", wrapped):
            with self.assertRaisesRegex(OSError, "flush failure"):
                await self.run_probe(client, seconds=0.05)
        self.assertEqual(len(self.frames()), 1)
        self.assertEqual(self.frames()[0]["hex"], "80")
        self.assertEqual(self.report()["state"], "error")
        client.disconnect.assert_awaited_once()

    async def test_growing_hr_log_blocks_competing_connection(self):
        path = self.directory / "heart_rate.jsonl"
        path.write_text("fixture\n")

        async def append(_):
            with path.open("a") as output:
                output.write("another fixture\n")

        with (
            patch.object(probe, "ROOT", self.directory),
            patch.object(probe.asyncio, "sleep", append),
        ):
            with self.assertRaisesRegex(RuntimeError, "still being written"):
                await probe.check_hr_writer()


class ProbeCliTests(unittest.TestCase):
    def test_invalid_listen_windows_are_rejected(self):
        for value in ("nan", "inf", "0", "-1", "601"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                probe.cli(["--seconds", value])

    def test_host_bluetooth_failure_is_saved_without_claiming_any_frames(self):
        with tempfile.TemporaryDirectory() as directory:

            async def fail(args, report):
                report["phase"] = "scanning"
                raise RuntimeError("BLE is unsupported")

            with patch.object(probe, "main", fail):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    result = probe.cli(["--output-dir", directory])
            self.assertEqual(result, 1)
            report = json.loads((Path(directory) / "air_probe.json").read_text())
            self.assertEqual(report["state"], "error")
            self.assertEqual(report["phase"], "scanning")
            self.assertNotIn("subscribed", report)
            self.assertFalse((Path(directory) / "air_probe_frames.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
