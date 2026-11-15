#!/usr/bin/env python3
"""Milestone 1 recon: inspect the Fitbit Air's proprietary BLE services.

Connects to the Air, reads every readable characteristic (device info), and
subscribes to every notify/indicate characteristic — including the proprietary
services — then logs whatever the band pushes, with timestamps. No application
characteristic values are written; subscribing enables notifications through
CoreBluetooth. This probe does not send a Gattlink reset or a DTLS handshake.

Run in Mac Terminal (needs Bluetooth permission):
    .venv/bin/python -u air_probe.py            # 60s listen
    .venv/bin/python -u air_probe.py --seconds 120
Output: air_probe.json (device info + per-characteristic frame summaries),
        air_probe_frames.jsonl (every raw frame, hex, timestamped).
"""

import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT
HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
# Human labels for the UUIDs we already identified on the Air.
KNOWN = {
    "0000180a-0000-1000-8000-00805f9b34fb": "Device Information",
    "0000180d-0000-1000-8000-00805f9b34fb": "Heart Rate (standard)",
    "abbafd00-e56a-484c-b832-8b17cf6cbfe8": "Read/notify status service",
    "abbaff00-e56a-484c-b832-8b17cf6cbfe8": "Gattlink transport",
    "ac2f0045-8182-4be5-91e0-2992e6b40ebb": "Proprietary C",
    "4eee1c00-4133-479b-8663-02c84bdc14be": "Proprietary D",
}
DEVICE_INFO_NAMES = {
    "00002a29": "manufacturer",
    "00002a24": "model",
    "00002a25": "serial",
    "00002a27": "hardware_rev",
    "00002a26": "firmware_rev",
    "00002a28": "software_rev",
    "00002a23": "system_id",
    "00002a50": "pnp_id",
}


def now():
    return datetime.now(timezone.utc).isoformat()


async def bounded(awaitable, seconds):
    """Bound an operation while preserving cancellation during completion races."""
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait([task], timeout=seconds)
        if not done:
            raise asyncio.TimeoutError()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def is_candidate(device, adv):
    name = adv.local_name or device.name or ""
    # A generic heart-rate advertiser might belong to somebody else. A saved or
    # explicit UUID can still target an identified Air that advertises no name.
    return "fitbit air" in name.lower()


def save_report(directory, report):
    path = Path(directory) / "air_probe.json"
    temporary = path.with_suffix(".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    temporary.replace(path)


def remembered_identifier():
    try:
        device = json.loads((ROOT / "services.json").read_text()).get("device", {})
        if "fitbit" in (device.get("name") or "").lower():
            return device.get("identifier")
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


async def check_hr_writer():
    """A growing log is direct evidence that the recorder has not been stopped."""
    path = ROOT / "heart_rate.jsonl"
    if not path.exists():
        return
    before = path.stat()
    await asyncio.sleep(2.2)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(
            "heart_rate.jsonl is still being written. Press Ctrl+C in the "
            "fitbit_hr.py Terminal before probing, then rerun this command."
        )


async def find_air(scanner, backend, identifier=None):
    if identifier:
        print(f"Looking for the selected Air: {identifier}", flush=True)
        device = await scanner.find_device_by_address(identifier, timeout=15.0, backend=backend)
        if device is None:
            raise RuntimeError(
                "Selected Air was not found. Keep it nearby and disconnect "
                "other BLE clients. Use --scan if its identifier changed."
            )
        return device
    print("Scanning 15s for the Fitbit Air...", flush=True)
    found = await scanner.discover(timeout=15.0, return_adv=True, backend=backend)
    matches = [d for d, adv in found.values() if is_candidate(d, adv)]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one Fitbit candidate, found {len(matches)}. "
            "Keep the Air nearby and other Fitbits away."
        )
    print(f"Found {matches[0].name!r} ({matches[0].address}).", flush=True)
    return matches[0]


async def probe(device, seconds, client_factory, backend, directory=None, report=None):
    directory = Path(directory) if directory is not None else ROOT
    frames_path = directory / "air_probe_frames.jsonl"
    if report is None:
        report = {"run_id": str(uuid.uuid4()), "timestamp": now()}
    report.update(
        device={"name": device.name, "identifier": device.address},
        device_info={},
        subscribed=[],
        frame_counts={},
        read_errors={},
        subscribe_errors={},
        cleanup_errors=[],
        services=[],
        requested_listen_seconds=seconds,
        total_frames=0,
        proprietary_frames=0,
    )
    disconnected = asyncio.Event()
    queue = asyncio.Queue()
    frames = None
    packet_task = None
    disconnect_task = None
    accepting = True
    loop = asyncio.get_running_loop()
    origin = loop.time()
    listen_start = None

    def checkpoint(phase):
        report["phase"] = phase
        report["updated_at"] = now()
        save_report(directory, report)

    def write_frame(row):
        frames.write(json.dumps(row) + "\n")
        frames.flush()
        char = row["char"]
        count = report["frame_counts"].get(char, 0) + 1
        report["frame_counts"][char] = count
        report["total_frames"] += 1
        if row["service"] != HR_SERVICE:
            report["proprietary_frames"] += 1
        if count <= 3:
            print(f"  FRAME {char}  {row['len']}B  {row['hex'][:64]}", flush=True)

    report["state"] = "running"
    checkpoint("connecting")
    client = client_factory(
        device, backend=backend, disconnected_callback=lambda _: disconnected.set(), timeout=30.0
    )
    try:
        print(f"Connecting to {device.name!r} ...", flush=True)
        await bounded(client.connect(), 30.0)
        print("Connected. Reading characteristics; no application-value writes.", flush=True)
        report["services"] = [
            {
                "uuid": service.uuid,
                "handle": service.handle,
                "characteristics": [
                    {"uuid": char.uuid, "handle": char.handle, "properties": list(char.properties)}
                    for char in service.characteristics
                ],
            }
            for service in client.services
        ]
        checkpoint("reading")
        # 1) Read every readable characteristic (device info is here).
        for service in client.services:
            for char in service.characteristics:
                if "read" not in char.properties:
                    continue
                short = char.uuid.split("-")[0]
                try:
                    value = bytes(await bounded(client.read_gatt_char(char), 3.0))
                except Exception as exc:
                    report["read_errors"][char.uuid] = f"{type(exc).__name__}: {exc}"
                    continue
                label = DEVICE_INFO_NAMES.get(short)
                text = None
                try:
                    text = value.decode("utf-8").strip("\x00") or None
                except UnicodeDecodeError:
                    pass
                entry = {"hex": value.hex(), "text": text, "service": service.uuid}
                report["device_info"][label or char.uuid] = entry
                if label and label not in ("serial", "system_id") and text:
                    print(f"  {label}: {text}", flush=True)

        # 2) Subscribe to every notify/indicate characteristic and log frames.
        fd = os.open(str(frames_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        frames = os.fdopen(fd, "a", buffering=1)
        checkpoint("subscribing")

        def make_handler(uuid, service_uuid):
            def handler(_sender, data):
                if not accepting:
                    return
                payload = bytes(data)
                queue.put_nowait(
                    {
                        "run_id": report["run_id"],
                        "t": now(),
                        "elapsed_seconds": loop.time() - origin,
                        "service": service_uuid,
                        "char": uuid,
                        "len": len(payload),
                        "hex": payload.hex(),
                    }
                )

            return handler

        for service in client.services:
            for char in service.characteristics:
                if {"notify", "indicate"}.intersection(char.properties):
                    try:
                        await bounded(
                            client.start_notify(char, make_handler(char.uuid, service.uuid)), 10.0
                        )
                        report["subscribed"].append(char.uuid)
                    except Exception as exc:
                        report["subscribe_errors"][char.uuid] = f"{type(exc).__name__}: {exc}"
                    while not queue.empty():
                        write_frame(queue.get_nowait())

        print(
            f"\nSubscribed to {len(report['subscribed'])} notify/indicate characteristics.",
            flush=True,
        )
        if not report["subscribed"]:
            raise RuntimeError("No notification subscriptions succeeded; see subscribe_errors.")
        print(
            f"Listening {seconds:g}s. Showing the first 3 frames per characteristic; saving all frames.",
            flush=True,
        )
        listen_start = loop.time()
        report["listen_started_at"] = now()
        checkpoint("listening")
        disconnect_task = asyncio.create_task(disconnected.wait())
        while loop.time() - listen_start < seconds:
            packet_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                [packet_task, disconnect_task],
                timeout=max(0, seconds - (loop.time() - listen_start)),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if packet_task in done:
                row = packet_task.result()
                packet_task = None
                write_frame(row)
            if disconnected.is_set():
                report["state"] = "disconnected"
                print("Device disconnected before the requested listen window ended.", flush=True)
                break
            if not done:
                break
        if report["state"] == "running":
            report["state"] = "complete"
            print("Listen window elapsed.", flush=True)
    except asyncio.CancelledError:
        report["state"] = "interrupted"
        raise
    except Exception as exc:
        report["state"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if listen_start is not None:
            report["observed_listen_seconds"] = loop.time() - listen_start
        tasks = [t for t in (packet_task, disconnect_task) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        pending_row = None
        if (
            packet_task is not None
            and not packet_task.cancelled()
            and packet_task.exception() is None
        ):
            pending_row = packet_task.result()
        if client.is_connected:
            for char in report["subscribed"]:
                try:
                    await bounded(client.stop_notify(char), 3.0)
                except Exception as exc:
                    report["cleanup_errors"].append(
                        f"stop_notify {char}: {type(exc).__name__}: {exc}"
                    )
        accepting = False
        try:
            await bounded(client.disconnect(), 10.0)
        except Exception as exc:
            report["cleanup_errors"].append(f"disconnect: {type(exc).__name__}: {exc}")
        if client.is_connected:
            report["cleanup_errors"].append("Client still reports connected after disconnect.")
        flush_error = None
        if frames is not None:
            try:
                if pending_row is not None:
                    write_frame(pending_row)
                while not queue.empty():
                    write_frame(queue.get_nowait())
            except Exception as exc:
                flush_error = exc
                report["state"] = "error"
                report.setdefault("error", f"{type(exc).__name__}: {exc}")
            finally:
                frames.close()
        report["ended_at"] = now()
        report["capture_state"] = report["state"]
        if report["cleanup_errors"] and report["state"] == "complete":
            report["state"] = "partial"
        save_report(directory, report)
        if flush_error is not None:
            raise flush_error
    total = sum(report["frame_counts"].values())
    print(
        f"\nDone. {total} frames logged across {len(report['frame_counts'])} characteristics.",
        flush=True,
    )
    print("Report: air_probe.json  |  Raw frames: air_probe_frames.jsonl", flush=True)
    print(f"Proprietary frames: {report['proprietary_frames']}.", flush=True)
    if report["cleanup_errors"]:
        print(
            "Cleanup incomplete: " + "; ".join(report["cleanup_errors"]),
            file=sys.stderr,
            flush=True,
        )
    if report["proprietary_frames"] == 0:
        print(
            "No proprietary notifications were observed in this run. Inspect the saved "
            "reads and subscription errors; this alone does not prove the cause.",
            flush=True,
        )
    return report


async def main(args, report):
    if sys.platform != "darwin":
        raise RuntimeError("Run locally on macOS.")
    from bleak import BleakClient, BleakScanner
    from bleak.backends.corebluetooth.client import BleakClientCoreBluetooth
    from bleak.backends.corebluetooth.scanner import BleakScannerCoreBluetooth

    report["phase"] = "checking_recorder"
    save_report(args.output_dir, report)
    await check_hr_writer()
    report["phase"] = "scanning"
    save_report(args.output_dir, report)
    identifier = args.device or (None if args.scan else remembered_identifier())
    device = await find_air(BleakScanner, BleakScannerCoreBluetooth, identifier)
    return await probe(
        device, args.seconds, BleakClient, BleakClientCoreBluetooth, args.output_dir, report
    )


def listen_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 600:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 600 seconds")
    return number


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds", type=listen_seconds, default=60.0, help="Listen duration (0-600 seconds)"
    )
    parser.add_argument("--device", help="Only connect to this macOS device UUID")
    parser.add_argument(
        "--scan", action="store_true", help="Scan instead of using the saved Air identifier"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT, help="Directory for report and raw frames"
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": str(uuid.uuid4()),
        "timestamp": now(),
        "state": "running",
        "phase": "starting",
    }
    try:
        result = asyncio.run(main(args, report))
        return 0 if result["state"] == "complete" else 1
    except KeyboardInterrupt:
        report.update(state="interrupted", ended_at=now())
        save_report(args.output_dir, report)
        print("\nStopped.", flush=True)
        return 130
    except Exception as exc:
        report.update(state="error", ended_at=now(), error=f"{type(exc).__name__}: {exc}")
        save_report(args.output_dir, report)
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        if "BLE is unsupported" in str(exc):
            print(
                "CoreBluetooth is unavailable to this process. Run the command in your Mac Terminal.",
                flush=True,
            )
        print(f"Diagnostic report: {args.output_dir / 'air_probe.json'}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
