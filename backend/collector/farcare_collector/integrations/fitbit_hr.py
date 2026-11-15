#!/usr/bin/env python3
"""Record real BLE heart-rate notifications from a Fitbit on this Mac."""

import argparse
import asyncio
import json
import math
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from farcare_collector.paths import WORKSPACE_ROOT

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
ROOT = WORKSPACE_ROOT
SHARING_HELP = (
    "Google Health > Connections (top left) > Fitbit Air > Share heart rate > Get started"
)


class NoReadingsError(RuntimeError):
    """A subscription was accepted, but no measurement could be decoded."""


class StreamStalledError(RuntimeError):
    """Measurements stopped while the connection remained open."""


class Status:
    """Persist observed stages, so service discovery is never mistaken for data."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "ble_status.json"
        self.data = {
            "started_at": now(),
            "state": "starting",
            "attempt": 0,
            "notifications": 0,
            "readings": 0,
            "decode_errors": 0,
        }

    def update(self, state=None, **values):
        if state is not None:
            self.data["state"] = state
        self.data.update(values)
        self.data["updated_at"] = now()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n")
        temporary.replace(self.path)


def decode_bpm(payload):
    if not payload:
        raise ValueError("Empty Heart Rate Measurement payload")
    size = 2 if payload[0] & 1 else 1
    if len(payload) < 1 + size:
        raise ValueError(f"Truncated Heart Rate Measurement: {payload.hex()}")
    return int.from_bytes(payload[1 : 1 + size], "little")


def now():
    return datetime.now(timezone.utc).isoformat()


def saved_device(directory):
    try:
        device = json.loads((Path(directory) / "services.json").read_text()).get("device", {})
        if isinstance(device, dict) and "fitbit" in (device.get("name") or "").lower():
            return device.get("identifier")
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def is_candidate(device, adv):
    name = adv.local_name or device.name or ""
    return "fitbit" in name.lower() or HR_SERVICE in {u.lower() for u in (adv.service_uuids or [])}


async def select_device(args, status, scanner, backend):
    identifier = args.device or (
        None if args.scan or args.all_devices else saved_device(status.directory)
    )
    status.update("scanning")
    if identifier:
        print(f"Looking for your selected device: {identifier}", flush=True)
        device = await scanner.find_device_by_address(identifier, timeout=15.0, backend=backend)
        if device is None:
            raise RuntimeError(
                "Saved device was not seen. Keep the Air nearby, open "
                + SHARING_HELP
                + ", then retry. Use --scan to select a different device."
            )
        status.update("device_selected", device={"name": device.name, "identifier": device.address})
        return device

    print("Scanning for 15 seconds; showing Fitbit/heart-rate candidates.", flush=True)
    found = await scanner.discover(timeout=15.0, return_adv=True, backend=backend)
    devices = sorted(found.values(), key=lambda pair: (-pair[1].rssi, pair[0].address))
    rows = []
    visible_numbers = set()
    for index, (device, adv) in enumerate(devices, 1):
        row = {
            "number": index,
            "name": adv.local_name or device.name,
            "identifier": device.address,
            "service_uuids": adv.service_uuids,
            "rssi": adv.rssi,
        }
        rows.append(row)
        if args.all_devices or is_candidate(device, adv):
            visible_numbers.add(index)
            print(f"[{index}] {row['name'] or '(unnamed)'} | {device.address} | RSSI {adv.rssi}")
            print(f"    Advertised services: {', '.join(adv.service_uuids) or '(none advertised)'}")
    (status.directory / "scan_results.json").write_text(
        json.dumps({"timestamp": now(), "devices": rows}, indent=2)
    )
    print(f"Found {len(devices)} devices; full scan saved to scan_results.json.", flush=True)
    if not devices:
        raise RuntimeError(
            "15-second scan returned no BLE devices. Check Bluetooth permission and that Bluetooth is enabled."
        )
    matches = [d for d, adv in devices if is_candidate(d, adv)]
    if len(matches) == 1:
        device = matches[0]
        print(f"\nAuto-selected {device.name!r} ({device.address}).", flush=True)
    else:
        if not matches and not args.all_devices:
            raise RuntimeError(
                "No Fitbit/heart-rate candidate found. Open "
                + SHARING_HELP
                + " and retry. --all-devices displays the full scan."
            )
        if not sys.stdin.isatty():
            raise RuntimeError(
                "Device selection is ambiguous. Run in Terminal or pass --device UUID."
            )
        print("\nSelect your identified device by number.", flush=True)
        while True:
            choice = input("Device number (or q to quit): ").strip()
            if choice.lower() == "q":
                return None
            if choice.isdigit() and int(choice) in visible_numbers:
                device = devices[int(choice) - 1][0]
                break
            print("Enter one of the listed device numbers.")
    status.update("device_selected", device={"name": device.name, "identifier": device.address})
    return device


async def consume_notifications(queue, disconnected, output, status, args):
    """Bound both first-sample and later stalls; malformed frames don't reset timeouts."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.first_reading_timeout
    received = 0
    readings = 0
    disconnect_task = asyncio.create_task(disconnected.wait())
    packet_task = None
    try:
        while True:
            packet_task = asyncio.create_task(queue.get())
            remaining = deadline - loop.time()
            done = set()
            if remaining > 0:
                done, _ = await asyncio.wait(
                    [packet_task, disconnect_task],
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if disconnected.is_set():
                raise ConnectionError("Device disconnected; recording stopped.")
            if packet_task not in done:
                if not readings:
                    raise NoReadingsError(
                        f"No decodable heart-rate reading within {args.first_reading_timeout:g}s "
                        f"after subscribing ({received} notifications received). "
                        "This does not identify the cause. On your phone open "
                        + SHARING_HELP
                        + ", follow any sharing confirmation, and keep the band on your wrist. "
                        "Close other heart-rate receiver apps before retrying."
                    )
                raise StreamStalledError(
                    f"No decodable reading for {args.idle_timeout:g}s; stream stalled."
                )
            timestamp, uuid, payload = packet_task.result()
            received += 1
            row = {
                "receipt_timestamp": timestamp,
                "characteristic_uuid": uuid,
                "payload_hex": payload.hex(),
                "bpm": None,
            }
            try:
                row["bpm"] = decode_bpm(payload)
            except ValueError as exc:
                row["decode_error"] = str(exc)
                print(f"DECODE ERROR: {exc}", file=sys.stderr, flush=True)
            output.write(json.dumps(row) + "\n")
            output.flush()
            counts = {"notifications": status.data["notifications"] + 1}
            if row["bpm"] is None:
                counts["decode_errors"] = status.data["decode_errors"] + 1
                status.update(**counts)
            else:
                readings += 1
                deadline = loop.time() + args.idle_timeout
                counts.update(
                    readings=status.data["readings"] + 1,
                    last_reading_at=timestamp,
                    last_bpm=row["bpm"],
                )
                status.update("streaming", **counts)
                print(f"{timestamp}  BPM={row['bpm']}  payload={payload.hex()}", flush=True)
                if args.once:
                    return
    finally:
        tasks = [task for task in (packet_task, disconnect_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def record_connection(device, args, status, client_factory, backend):
    disconnected = asyncio.Event()
    queue = asyncio.Queue()
    client = client_factory(
        device, backend=backend, disconnected_callback=lambda _: disconnected.set(), timeout=30.0
    )
    subscribed = False
    try:
        status.update("connecting")
        print(f"Connecting to {device.name!r} ({device.address}) ...", flush=True)
        await asyncio.wait_for(client.connect(), timeout=30.0)
        status.update("connected", connected_at=now())
        services = []
        for service in client.services:
            services.append(
                {
                    "uuid": service.uuid,
                    "handle": service.handle,
                    "description": service.description,
                    "characteristics": [
                        {
                            "uuid": c.uuid,
                            "handle": c.handle,
                            "description": c.description,
                            "properties": list(c.properties),
                            "descriptors": [
                                {"uuid": d.uuid, "handle": d.handle, "description": d.description}
                                for d in c.descriptors
                            ],
                        }
                        for c in service.characteristics
                    ],
                }
            )
        (status.directory / "services.json").write_text(
            json.dumps(
                {
                    "timestamp": now(),
                    "device": {"name": device.name, "identifier": device.address},
                    "backend": "CoreBluetooth",
                    "services": services,
                },
                indent=2,
            )
        )
        print(f"Discovered {len(services)} services; saved services.json.", flush=True)
        hr = client.services.get_service(HR_SERVICE)
        if hr is None:
            raise RuntimeError(
                "Heart Rate Service 180D is absent. Full discovery is saved in services.json."
            )
        characteristic = hr.get_characteristic(HR_MEASUREMENT)
        if characteristic is None:
            raise RuntimeError("Heart Rate Measurement 2A37 is absent from Heart Rate Service.")
        if not {"notify", "indicate"}.intersection(characteristic.properties):
            raise RuntimeError(f"Heart Rate Measurement cannot notify: {characteristic.properties}")

        def receive(sender, data):
            queue.put_nowait((now(), sender.uuid, bytes(data)))

        status.update("subscribing")
        await asyncio.wait_for(client.start_notify(characteristic, receive), timeout=15.0)
        subscribed = True
        status.update("subscribed", subscribed_at=now())
        print(
            f"Subscribed to 2A37; waiting up to {args.first_reading_timeout:g}s for the first reading.",
            flush=True,
        )
        print(
            "Keep the sharing screen open on your phone and follow any confirmation. Ctrl+C stops.",
            flush=True,
        )
        with (status.directory / "heart_rate.jsonl").open("a", buffering=1) as output:
            await consume_notifications(queue, disconnected, output, status, args)
    finally:
        # Cleanup failures must not replace the original connection/recording error.
        if subscribed and client.is_connected:
            try:
                await asyncio.wait_for(client.stop_notify(HR_MEASUREMENT), timeout=5.0)
            except Exception as exc:
                print(f"Could not stop notifications cleanly: {exc}", file=sys.stderr, flush=True)
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10.0)
        except Exception as exc:
            print(f"Could not disconnect cleanly: {exc}", file=sys.stderr, flush=True)


async def run(args, status, scanner, client_factory, scanner_backend=None, client_backend=None):
    device = await select_device(args, status, scanner, scanner_backend)
    if device is None:
        status.update("stopped", reason="Device selection cancelled")
        return
    for attempt in range(1, args.retries + 2):
        status.update(attempt=attempt)
        try:
            await record_connection(device, args, status, client_factory, client_backend)
            status.update("complete")
            return
        except (NoReadingsError, StreamStalledError, ConnectionError) as exc:
            if attempt > args.retries:
                raise
            status.update("retrying", last_error=str(exc))
            print(
                f"{exc}\nReconnecting to the same device once more ({attempt}/{args.retries}).",
                flush=True,
            )
            await asyncio.sleep(2)


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Connect only to this macOS device UUID")
    parser.add_argument(
        "--scan", action="store_true", help="Ignore the saved Fitbit and select again"
    )
    parser.add_argument(
        "--all-devices", action="store_true", help="Scan and display every nearby device"
    )
    parser.add_argument(
        "--once", action="store_true", help="Stop successfully after one decoded measurement"
    )
    parser.add_argument(
        "--first-reading-timeout", type=positive_seconds, default=60.0, metavar="SECONDS"
    )
    parser.add_argument("--idle-timeout", type=positive_seconds, default=60.0, metavar="SECONDS")
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Reconnect after a silent/stalled/dropped stream (0-3; default 1)",
    )
    parser.add_argument("--debug", action="store_true", help="Show exception traceback")
    args = parser.parse_args(argv)
    if not 0 <= args.retries <= 3:
        parser.error("--retries must be between 0 and 3")
    return args


async def main(args, status):
    if sys.platform != "darwin":
        raise RuntimeError("Run this tool locally on macOS.")
    from bleak import BleakClient, BleakScanner
    from bleak.backends.corebluetooth.client import BleakClientCoreBluetooth
    from bleak.backends.corebluetooth.scanner import BleakScannerCoreBluetooth

    print(
        "Wear the Air and keep it near this Mac. On your phone: " + SHARING_HELP + ".", flush=True
    )
    print("If macOS requests Bluetooth access, approve it for the hosting app.", flush=True)
    await run(
        args, status, BleakScanner, BleakClient, BleakScannerCoreBluetooth, BleakClientCoreBluetooth
    )


def cli(argv=None):
    args = parse_args(argv)
    status = Status(ROOT)
    try:
        status.update()
        asyncio.run(main(args, status))
        return 0
    except KeyboardInterrupt:
        status.update("stopped", reason="Keyboard interrupt")
        print("\nStopped. Completed readings remain in heart_rate.jsonl.", flush=True)
        return 130
    except Exception as exc:
        failed_stage = status.data["state"]
        print(
            f"ERROR during {failed_stage}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True
        )
        try:
            status.update(
                "error", failed_stage=failed_stage, error_type=type(exc).__name__, error=str(exc)
            )
        except OSError as status_error:
            print(f"Could not save ble_status.json: {status_error}", file=sys.stderr, flush=True)
        else:
            print("Diagnosis saved to ble_status.json.", flush=True)
        if "BLE is unsupported" in str(exc):
            print(
                "This process cannot access CoreBluetooth. Run the command in Mac Terminal; "
                "this error does not establish that the Mac lacks Bluetooth.",
                file=sys.stderr,
                flush=True,
            )
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(cli())
