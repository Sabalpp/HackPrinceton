"""Simulated BLE transport tests. All fixture readings stay in temporary directories."""

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from farcare_collector.integrations import fitbit_hr as hr


class Services:
    def __init__(self, characteristic, present=True):
        self.service = SimpleNamespace(
            uuid=hr.HR_SERVICE,
            handle=1,
            description="Heart Rate",
            characteristics=[characteristic],
            get_characteristic=lambda uuid: characteristic if uuid == hr.HR_MEASUREMENT else None,
        )
        self.present = present

    def __iter__(self):
        return iter([self.service] if self.present else [])

    def get_service(self, uuid):
        return self.service if self.present and uuid == hr.HR_SERVICE else None


class Client:
    def __init__(self, packets=(), subscribe_error=None, service_present=True, drop=False):
        self.characteristic = SimpleNamespace(
            uuid=hr.HR_MEASUREMENT,
            handle=2,
            description="Heart Rate Measurement",
            properties=["notify"],
            descriptors=[],
        )
        self.services = Services(self.characteristic, service_present)
        self.is_connected = False
        self.packets = packets
        self.subscribe_error = subscribe_error
        self.drop = drop
        self.stop_notify = AsyncMock()
        self.disconnect = AsyncMock(side_effect=self.disconnect_now)

    def factory(self, device, **kwargs):
        self.on_disconnect = kwargs["disconnected_callback"]
        return self

    async def connect(self):
        self.is_connected = True

    def disconnect_now(self):
        self.is_connected = False
        self.on_disconnect(self)

    async def start_notify(self, characteristic, callback):
        if self.subscribe_error:
            raise self.subscribe_error
        for packet in self.packets:
            callback(characteristic, packet)
        if self.drop:
            asyncio.get_running_loop().call_soon(self.disconnect_now)


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.status = hr.Status(self.directory)
        self.args = hr.parse_args(
            [
                "--once",
                "--retries",
                "0",
                "--first-reading-timeout",
                "0.03",
                "--idle-timeout",
                "0.03",
            ]
        )
        self.device = SimpleNamespace(name="Google Fitbit Air", address="test-device-uuid")

    async def record(self, client):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            await hr.record_connection(self.device, self.args, self.status, client.factory, None)

    def rows(self):
        return [
            json.loads(line)
            for line in (self.directory / "heart_rate.jsonl").read_text().splitlines()
        ]

    async def test_notification_received_during_subscription_is_saved_once(self):
        client = Client([bytes.fromhex("0048")])
        await self.record(client)
        self.assertEqual(self.rows()[0]["bpm"], 72)
        self.assertEqual(self.rows()[0]["payload_hex"], "0048")
        self.assertEqual(self.status.data["state"], "streaming")
        self.assertEqual(self.status.data["readings"], 1)
        client.stop_notify.assert_awaited_once()
        client.disconnect.assert_awaited_once()
        self.assertFalse(client.is_connected)

    async def test_silent_subscription_times_out_and_never_claims_streaming(self):
        client = Client()
        with self.assertRaisesRegex(hr.NoReadingsError, "0 notifications received"):
            await self.record(client)
        self.assertEqual(self.status.data["state"], "subscribed")
        self.assertEqual(self.status.data["readings"], 0)
        self.assertEqual(self.rows(), [])
        client.disconnect.assert_awaited_once()

    async def test_malformed_packet_is_retained_before_a_good_sample(self):
        await self.record(Client([b"\x01", bytes.fromhex("012c01")]))
        rows = self.rows()
        self.assertIsNone(rows[0]["bpm"])
        self.assertIn("decode_error", rows[0])
        self.assertEqual(rows[1]["bpm"], 300)
        self.assertEqual(self.status.data["notifications"], 2)
        self.assertEqual(self.status.data["decode_errors"], 1)
        self.assertEqual(self.status.data["readings"], 1)

    async def test_malformed_only_stream_is_not_reported_as_silent_or_successful(self):
        with self.assertRaisesRegex(hr.NoReadingsError, "1 notifications received"):
            await self.record(Client([b""]))
        self.assertEqual(self.status.data["readings"], 0)
        self.assertEqual(self.status.data["decode_errors"], 1)

    async def test_continuous_malformed_packets_cannot_extend_deadline(self):
        queue = asyncio.Queue()
        for _ in range(10000):
            queue.put_nowait((hr.now(), hr.HR_MEASUREMENT, b""))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(hr.NoReadingsError):
                await asyncio.wait_for(
                    hr.consume_notifications(
                        queue, asyncio.Event(), io.StringIO(), self.status, self.args
                    ),
                    0.5,
                )
        self.assertEqual(self.status.data["readings"], 0)

    async def test_stream_stall_is_detected_after_a_valid_reading(self):
        self.args.once = False
        with self.assertRaises(hr.StreamStalledError):
            await self.record(Client([bytes.fromhex("0048")]))
        self.assertEqual(self.status.data["readings"], 1)

    async def test_disconnect_is_reported_without_waiting_for_first_sample(self):
        client = Client(drop=True)
        self.args.first_reading_timeout = 60
        with self.assertRaisesRegex(ConnectionError, "disconnected"):
            await asyncio.wait_for(self.record(client), 0.5)
        self.assertEqual(self.status.data["readings"], 0)

    async def test_subscribe_failure_is_not_recorded_as_subscribed(self):
        client = Client(subscribe_error=RuntimeError("fixture: subscription denied"))
        with self.assertRaisesRegex(RuntimeError, "subscription denied"):
            await self.record(client)
        self.assertEqual(self.status.data["state"], "subscribing")
        self.assertNotIn("subscribed_at", self.status.data)
        client.stop_notify.assert_not_awaited()
        client.disconnect.assert_awaited_once()

    async def test_missing_service_disconnects_without_subscribing(self):
        client = Client(service_present=False)
        with self.assertRaisesRegex(RuntimeError, "180D is absent"):
            await self.record(client)
        self.assertEqual(self.status.data["state"], "connected")
        self.assertNotIn("subscribed_at", self.status.data)
        client.disconnect.assert_awaited_once()

    async def test_cleanup_error_does_not_hide_recording_error(self):
        client = Client()
        client.stop_notify.side_effect = RuntimeError("fixture: cleanup failed")
        with self.assertRaises(hr.NoReadingsError):
            await self.record(client)
        client.disconnect.assert_awaited_once()

    async def test_failed_output_write_is_propagated(self):
        queue = asyncio.Queue()
        queue.put_nowait((hr.now(), hr.HR_MEASUREMENT, bytes.fromhex("0048")))

        class BrokenOutput:
            def write(self, _):
                raise OSError("fixture: disk full")

        with self.assertRaisesRegex(OSError, "disk full"):
            await hr.consume_notifications(
                queue, asyncio.Event(), BrokenOutput(), self.status, self.args
            )
        self.assertEqual(self.status.data["readings"], 0)

    async def test_saved_fitbit_is_targeted_without_broad_scan(self):
        (self.directory / "services.json").write_text(
            json.dumps({"device": {"name": self.device.name, "identifier": self.device.address}})
        )
        scanner = SimpleNamespace(
            find_device_by_address=AsyncMock(return_value=self.device), discover=AsyncMock()
        )
        with contextlib.redirect_stdout(io.StringIO()):
            selected = await hr.select_device(self.args, self.status, scanner, None)
        self.assertIs(selected, self.device)
        scanner.find_device_by_address.assert_awaited_once()
        scanner.discover.assert_not_awaited()

    async def test_missing_saved_device_never_connects_to_a_stranger(self):
        self.args.device = self.device.address
        scanner = SimpleNamespace(
            find_device_by_address=AsyncMock(return_value=None), discover=AsyncMock()
        )
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Saved device was not seen"):
                await hr.select_device(self.args, self.status, scanner, None)
        scanner.discover.assert_not_awaited()

    async def test_ambiguous_scan_requires_selection_instead_of_guessing(self):
        adv = SimpleNamespace(
            local_name="Google Fitbit Air", service_uuids=[hr.HR_SERVICE], rssi=-40
        )
        scanner = SimpleNamespace(
            discover=AsyncMock(
                return_value={
                    "a": (self.device, adv),
                    "b": (SimpleNamespace(name="Google Fitbit Air", address="second-device"), adv),
                }
            )
        )
        with (
            patch.object(hr.sys.stdin, "isatty", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                await hr.select_device(self.args, self.status, scanner, None)

    async def test_retry_is_bounded_and_keeps_the_selected_device(self):
        self.args.retries = 1
        with patch.object(hr, "select_device", AsyncMock(return_value=self.device)) as select:
            with patch.object(
                hr,
                "record_connection",
                AsyncMock(
                    side_effect=[
                        hr.NoReadingsError("fixture: silent"),
                        hr.NoReadingsError("fixture: still silent"),
                    ]
                ),
            ) as record:
                with (
                    patch.object(hr.asyncio, "sleep", AsyncMock()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    with self.assertRaisesRegex(hr.NoReadingsError, "still silent"):
                        await hr.run(self.args, self.status, None, None)
        select.assert_awaited_once()
        self.assertEqual(record.await_count, 2)
        self.assertTrue(all(call.args[0] is self.device for call in record.await_args_list))
        self.assertEqual(self.status.data["attempt"], 2)

    async def test_old_scan_number_cannot_select_a_hidden_unrelated_device(self):
        other_fitbit = SimpleNamespace(name="Google Fitbit Air", address="second-device")
        unrelated = SimpleNamespace(name=None, address="unrelated-device")

        def adv(name, rssi):
            return SimpleNamespace(local_name=name, service_uuids=[], rssi=rssi)

        scanner = SimpleNamespace(
            discover=AsyncMock(
                return_value={
                    "a": (self.device, adv("Google Fitbit Air", -40)),
                    "b": (unrelated, adv(None, -45)),
                    "c": (other_fitbit, adv("Google Fitbit Air", -50)),
                }
            )
        )
        with patch.object(hr.sys.stdin, "isatty", return_value=True):
            with patch("builtins.input", side_effect=["2", "3"]) as choice:
                with contextlib.redirect_stdout(io.StringIO()):
                    selected = await hr.select_device(self.args, self.status, scanner, None)
        self.assertIs(selected, other_fitbit)
        self.assertEqual(choice.call_count, 2)


class SelectionAndCliTests(unittest.TestCase):
    def test_uuid_substring_does_not_match_the_heart_rate_service(self):
        device = SimpleNamespace(name=None)
        adv = SimpleNamespace(
            local_name=None, service_uuids=["1234180d-1111-2222-3333-444455556666"]
        )
        self.assertFalse(hr.is_candidate(device, adv))

    def test_nonfinite_or_nonpositive_timeouts_are_rejected(self):
        for value in ("nan", "inf", "-1", "0"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                hr.parse_args(["--first-reading-timeout", value])

    def test_host_bluetooth_error_records_failed_stage_and_does_not_erase_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_services = '{"fixture": "existing discovery"}'
            (root / "services.json").write_text(old_services)

            async def fail(args, status):
                status.update("scanning")
                raise RuntimeError("BLE is unsupported")

            with patch.object(hr, "ROOT", root), patch.object(hr, "main", fail):
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(hr.cli(["--once"]), 1)
            status = json.loads((root / "ble_status.json").read_text())
            self.assertEqual(status["state"], "error")
            self.assertEqual(status["failed_stage"], "scanning")
            self.assertEqual(status["readings"], 0)
            self.assertEqual((root / "services.json").read_text(), old_services)

    def test_status_write_failure_is_reported_without_a_second_uncaught_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(hr, "ROOT", Path(directory)):
                with patch.object(hr.Status, "update", side_effect=OSError("fixture: disk full")):
                    with (
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()) as errors,
                    ):
                        self.assertEqual(hr.cli(["--once"]), 1)
        self.assertIn("disk full", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
