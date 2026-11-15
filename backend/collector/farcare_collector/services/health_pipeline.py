#!/usr/bin/env python3
"""Durable local ingestion of real BLE and Google Health exports, with Tiger sync."""

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from farcare_collector.paths import COLLECTOR_ROOT, WORKSPACE_ROOT

ROOT = WORKSPACE_ROOT


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp(value):
    # Python 3.9 only accepts 3/6 fractional digits; RFC3339 permits 1..9.
    value = re.sub(
        r"\.(\d+)(?=Z|[+-]\d\d:\d\d$)", lambda match: "." + match[1][:6].ljust(6, "0"), value
    )
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required.")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A separate 0600 file per writer prevents overlapping status/report updates
    # from truncating or renaming one another's temporary file.
    fd, temporary_name = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._file_cache = {}
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS raw_records(
            raw_hash TEXT PRIMARY KEY, source TEXT NOT NULL, data_type TEXT NOT NULL,
            payload BLOB NOT NULL, source_file TEXT NOT NULL, first_seen TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS measurement_versions(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, version_hash TEXT NOT NULL UNIQUE,
            event_id TEXT NOT NULL, observed_at TEXT NOT NULL, metric TEXT NOT NULL,
            source TEXT NOT NULL, source_stream TEXT NOT NULL, subject_id TEXT NOT NULL,
            revision_epoch REAL NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS versions_event_idx ON measurement_versions(event_id);
          CREATE TABLE IF NOT EXISTS current_events(
            event_id TEXT PRIMARY KEY, seq INTEGER NOT NULL REFERENCES measurement_versions(seq),
            latest_revision_epoch REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS quarantine(
            raw_hash TEXT PRIMARY KEY REFERENCES raw_records(raw_hash), reason TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS offsets(
            file_path TEXT PRIMARY KEY, device INTEGER NOT NULL, inode INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL, prefix_size INTEGER NOT NULL, prefix_hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS imported_files(
            file_key TEXT PRIMARY KEY, content_hash TEXT NOT NULL, imported_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS deliveries(target TEXT PRIMARY KEY, last_seq INTEGER NOT NULL);
        """)

    def close(self):
        self.db.close()

    def ingest(
        self,
        record,
        source,
        data_type,
        source_file,
        revision_at,
        subject="demo",
        timezone_name="America/New_York",
        received_at=None,
        raw_bytes=None,
    ):
        from farcare_collector.health.health_normalize import (
            normalize_api_record,
            normalize_ble_record,
            record_hash,
        )

        received_at = timestamp(received_at or now())
        try:
            raw_hash = record_hash(record)
            raw = packed(record).encode()
        except (ValueError, TypeError):
            raw = raw_bytes if raw_bytes is not None else repr(record).encode()
            raw_hash = hashlib.sha256(raw).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO raw_records VALUES (?,?,?,?,?,?)",
            (raw_hash, source, data_type, raw, str(source_file), received_at),
        )
        try:
            if source == "ble":
                points = normalize_ble_record(
                    record, subject_id=subject, timezone_name=timezone_name
                )
            else:
                points = normalize_api_record(
                    data_type, record, subject_id=subject, timezone_name=timezone_name
                )
            # Validate all points before any versions from this record are stored.
            ready = []
            for point in points:
                point = dict(point)
                point["observed_at"] = timestamp(point["observed_at"])
                if point["ended_at"] is not None:
                    point["ended_at"] = timestamp(point["ended_at"])
                point["content_hash"] = digest(packed(point))
                revision = point["attributes"].get("source_update_time") or revision_at
                point["source_revision_at"] = timestamp(revision)
                revision_epoch = datetime.fromisoformat(
                    point["source_revision_at"].replace("Z", "+00:00")
                ).timestamp()
                point["version_hash"] = digest(packed(point))
                point["received_at"] = received_at
                ready.append((point, revision_epoch, packed(point)))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            # Reasons describe schema failures; never include credential/account JSON.
            self.db.execute(
                "INSERT OR REPLACE INTO quarantine VALUES (?,?)",
                (raw_hash, f"{type(exc).__name__}: {str(exc)[:250]}"),
            )
            return Counter(records=1, quarantined=1)
        counts = Counter(records=1, normalized=len(ready))
        for p, revision_epoch, payload in ready:
            current = self.db.execute(
                """SELECT c.latest_revision_epoch,v.payload,v.revision_epoch FROM current_events c
                JOIN measurement_versions v ON v.seq=c.seq WHERE c.event_id=?""",
                (p["event_id"],),
            ).fetchone()
            if current and json.loads(current[1])["content_hash"] == p["content_hash"]:
                self.db.execute(
                    "UPDATE current_events SET latest_revision_epoch=max(latest_revision_epoch,?) WHERE event_id=?",
                    (revision_epoch, p["event_id"]),
                )
                counts["duplicates"] += 1
                continue
            if current and source == "ble":
                # A received notification is immutable. Two values for the same
                # source timestamp are a conflict, not a corrected cloud record.
                self.db.execute(
                    "INSERT OR REPLACE INTO quarantine VALUES (?,?)",
                    (raw_hash, "Conflicting immutable BLE event."),
                )
                counts["quarantined"] += 1
                continue
            # Match the remote view's deterministic tie order. A retained raw
            # record may be retried later; its first_seen is not its new delivery.
            previous = json.loads(current[1]) if current else None
            p["canonical_eligible"] = (
                current is None
                or revision_epoch > current[0]
                or (
                    revision_epoch == current[0]
                    and (revision_epoch, p["received_at"], p["version_hash"])
                    > (current[2], previous["received_at"], previous["version_hash"])
                )
            )
            payload = packed(p)
            inserted = self.db.execute(
                """INSERT OR IGNORE INTO measurement_versions
              (version_hash,event_id,observed_at,metric,source,source_stream,subject_id,revision_epoch,payload)
              VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    p["version_hash"],
                    p["event_id"],
                    p["observed_at"],
                    p["metric"],
                    p["source"],
                    p["source_stream"],
                    p["subject_id"],
                    revision_epoch,
                    payload,
                ),
            )
            if not inserted.rowcount:
                counts["duplicates"] += 1
                continue
            seq = inserted.lastrowid
            if p["canonical_eligible"]:
                self.db.execute(
                    "INSERT INTO current_events VALUES (?,?,?) ON CONFLICT(event_id) "
                    "DO UPDATE SET seq=excluded.seq,latest_revision_epoch=excluded.latest_revision_epoch",
                    (p["event_id"], seq, revision_epoch),
                )
                counts["new_events" if current is None else "revised_events"] += 1
            else:
                counts["older_versions_retained"] += 1
            counts["new_versions"] += 1
        if not counts["quarantined"]:
            self.db.execute("DELETE FROM quarantine WHERE raw_hash=?", (raw_hash,))
        return counts

    def retry_quarantine(self, subject="demo", timezone_name="America/New_York"):
        rows = self.db.execute("""SELECT r.source,r.data_type,r.payload,r.source_file,r.first_seen
            FROM quarantine q JOIN raw_records r USING(raw_hash)""").fetchall()
        counts = Counter()
        with self.db:
            for source, data_type, raw, source_file, first_seen in rows:
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeError):
                    counts["still_invalid_json"] += 1
                    continue
                revision = (
                    record.get("receipt_timestamp", first_seen)
                    if isinstance(record, dict)
                    else first_seen
                )
                if source == "google_health":
                    summary = Path(source_file).parent.parent / "summary.json"
                    if summary.exists():
                        revision = json.loads(summary.read_text())["started_at"]
                counts.update(
                    self.ingest(
                        record,
                        source,
                        data_type,
                        source_file,
                        revision,
                        subject,
                        timezone_name,
                        raw_bytes=raw,
                    )
                )
        return dict(counts)

    def reject_line(self, raw, path, reason):
        raw_hash = hashlib.sha256(raw).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO raw_records VALUES (?,?,?,?,?,?)",
            (raw_hash, "ble", "heart-rate", raw, str(path), now()),
        )
        self.db.execute("INSERT OR REPLACE INTO quarantine VALUES (?,?)", (raw_hash, reason))

    def import_api(self, directory, subject="demo", timezone_name="America/New_York"):
        directory = Path(directory)
        summary = json.loads((directory / "summary.json").read_text())
        revision = timestamp(summary["started_at"])
        counts = Counter()
        for path in sorted(directory.glob("*/page-*.json")):
            key = subject + "|" + timezone_name + "|" + str(path.resolve())
            info = path.stat()
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, revision)
            if self._file_cache.get(key) == signature:
                counts["unchanged_files"] += 1
                continue
            data = path.read_bytes()
            # An identical page fetched later advances the revision watermark.
            file_hash = hashlib.sha256(data + revision.encode()).hexdigest()
            previous = self.db.execute(
                "SELECT content_hash FROM imported_files WHERE file_key=?", (key,)
            ).fetchone()
            if previous and previous[0] == file_hash:
                self._file_cache[key] = signature
                counts["unchanged_files"] += 1
                continue
            body = json.loads(data)
            points = body.get("dataPoints", [])
            if not isinstance(points, list):
                raise ValueError("API page dataPoints must be a list: " + str(path))
            with self.db:
                for point in points:
                    counts.update(
                        self.ingest(
                            point,
                            "google_health",
                            path.parent.name,
                            path,
                            revision,
                            subject,
                            timezone_name,
                        )
                    )
                self.db.execute(
                    "INSERT INTO imported_files VALUES (?,?,?) ON CONFLICT(file_key) "
                    "DO UPDATE SET content_hash=excluded.content_hash,imported_at=excluded.imported_at",
                    (key, file_hash, now()),
                )
            counts["imported_files"] += 1
            self._file_cache[key] = signature
        for path in sorted((directory / "exercise-tcx").glob("*.tcx")):
            raw = path.read_bytes()
            raw_hash = hashlib.sha256(raw).hexdigest()
            with self.db:
                inserted = self.db.execute(
                    "INSERT OR IGNORE INTO raw_records VALUES (?,?,?,?,?,?)",
                    (raw_hash, "google_health", "exercise-tcx", raw, str(path), now()),
                )
            counts["new_tcx_archives"] += inserted.rowcount
        return dict(counts)

    def tail_ble(self, path, subject="demo", timezone_name="America/New_York"):
        path = Path(path).resolve()
        if not path.exists():
            return {"missing_file": True, "records": 0}
        key = subject + "|" + timezone_name + "|" + str(path)
        previous = self.db.execute(
            "SELECT device,inode,byte_offset,prefix_size,prefix_hash "
            "FROM offsets WHERE file_path=?",
            (key,),
        ).fetchone()
        counts = Counter()
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            offset = 0
            if previous:
                dev, inode, old_offset, prefix_size, prefix_hash = previous
                prefix = stream.read(prefix_size)
                if (
                    (dev, inode) == (info.st_dev, info.st_ino)
                    and old_offset <= info.st_size
                    and hashlib.sha256(prefix).hexdigest() == prefix_hash
                ):
                    offset = old_offset
                else:
                    counts["rotation_or_truncation"] += 1
            stream.seek(offset)
            with self.db:
                while stream.tell() < info.st_size:
                    start = stream.tell()
                    raw = stream.readline(info.st_size - start)
                    if not raw.endswith(b"\n"):
                        counts["partial_line_pending"] += 1
                        break
                    offset = stream.tell()
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                        revision = (
                            record.get("receipt_timestamp") if isinstance(record, dict) else None
                        )
                        counts.update(
                            self.ingest(
                                record,
                                "ble",
                                "heart-rate",
                                path,
                                revision or now(),
                                subject,
                                timezone_name,
                                raw_bytes=raw,
                            )
                        )
                    except (ValueError, UnicodeError, TypeError):
                        self.reject_line(raw, path, "Invalid complete JSONL record.")
                        counts.update(records=1, quarantined=1)
                prefix_size = min(offset, 256)
                stream.seek(0)
                prefix_hash = hashlib.sha256(stream.read(prefix_size)).hexdigest()
                self.db.execute(
                    "INSERT INTO offsets VALUES (?,?,?,?,?,?) ON CONFLICT(file_path) "
                    "DO UPDATE SET device=excluded.device,inode=excluded.inode,byte_offset=excluded.byte_offset,"
                    "prefix_size=excluded.prefix_size,prefix_hash=excluded.prefix_hash",
                    (key, info.st_dev, info.st_ino, offset, prefix_size, prefix_hash),
                )
        counts["byte_offset"] = offset
        return dict(counts)

    def measurements(self):
        query = """SELECT v.payload FROM current_events c JOIN measurement_versions v ON v.seq=c.seq
                   ORDER BY v.observed_at,v.event_id"""
        for row in self.db.execute(query):
            yield json.loads(row[0])

    def summary(self):
        counts = {
            name: self.db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ("raw_records", "measurement_versions", "current_events", "quarantine")
        }
        counts["metrics"] = [
            dict(source=r[0], metric=r[1], count=r[2], first=r[3], latest=r[4])
            for r in self.db.execute("""SELECT v.source,v.metric,count(*),min(v.observed_at),max(v.observed_at)
                FROM current_events c JOIN measurement_versions v ON v.seq=c.seq
                GROUP BY v.source,v.metric ORDER BY v.source,v.metric""")
        ]
        return counts


def run_refresh_command(command, stop_event, timeout=600):
    """Bound and cancel the owned connector process without orphaning it."""
    if stop_event.is_set():
        return None
    process = subprocess.Popen(
        command,
        env=dict(os.environ, FARCARE_WORKSPACE=str(ROOT)),
        cwd=str(COLLECTOR_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout
    try:
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                return process.wait(timeout=min(0.25, remaining))
            except subprocess.TimeoutExpired:
                continue
        return None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def cloud_refresh(days, stop_event=None):
    from farcare_collector.integrations import google_health

    # A background collector must not launch a new interactive OAuth flow.
    if not google_health.TOKEN_FILE.exists():
        raise RuntimeError(
            "Google OAuth setup required; run python -m farcare_collector.integrations.google_health interactively first."
        )
    cached = json.loads(google_health.TOKEN_FILE.read_text())
    if not cached.get("refresh_token") or set(google_health.SCOPES) - set(
        cached.get("scope", "").split()
    ):
        raise RuntimeError(
            "Google OAuth refresh grant/scopes missing; run python -m farcare_collector.integrations.google_health interactively."
        )
    config = google_health.load_credentials()
    if cached.get("client_id") != config.get("client_id"):
        raise RuntimeError("Google OAuth cache does not match the available client.")
    # Uses the existing connector, normal refresh-token flow and its private exports.
    result = run_refresh_command(
        [
            sys.executable,
            "-m",
            "farcare_collector.integrations.google_health",
            "--days",
            str(days),
            "--no-browser",
        ],
        stop_event or threading.Event(),
    )
    if result:
        raise RuntimeError(
            "Google refresh failed; existing records remain available. Run python -m farcare_collector.integrations.google_health for diagnostics."
        )


def build_report(store, directory, timezone_name):
    from farcare_collector.health.health_features import build_features

    report = build_features(store.measurements(), timezone_name=timezone_name)
    report["pipeline"] = store.summary()
    report["generated_at"] = now()
    private_json(Path(directory) / "features.json", report)
    from farcare_collector.health.health_dashboard import render

    path = Path(directory) / "report.html"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(render(report))
    return report


def build_background_report(database, directory, timezone_name):
    """Use a separate SQLite connection so report work cannot pause BLE tailing."""
    store = Store(database)
    try:
        return build_report(store, directory, timezone_name)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("ingest", "report", "sync", "watch", "status", "retry-quarantine")
    )
    parser.add_argument("--db", type=Path, default=ROOT / "pipeline_data/health.sqlite3")
    parser.add_argument("--api-dir", type=Path, action="append", default=[])
    parser.add_argument("--ble", type=Path, default=ROOT / "heart_rate.jsonl")
    parser.add_argument("--subject", default="demo")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--output", type=Path, default=ROOT / "pipeline_data")
    parser.add_argument(
        "--credentials", type=Path, help="Tiger PostgreSQL .env file; never printed or copied."
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1,
        help="Target ingest/delivery cadence in seconds (default: 1).",
    )
    parser.add_argument(
        "--cloud-every-seconds",
        type=float,
        default=900,
        help="Watch mode refresh cadence; 0 disables cloud requests.",
    )
    parser.add_argument("--cloud-days", type=int, default=2)
    args = parser.parse_args()
    if (
        not 1 <= args.poll_seconds <= 300
        or not 0 <= args.cloud_every_seconds <= 86400
        or not 1 <= args.cloud_days <= 30
    ):
        parser.error("Invalid polling interval or cloud lookback.")
    store = Store(args.db)

    def stop_on_term(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_term)
    cloud_stop = threading.Event()
    cloud_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="google-cloud-refresh")
    cloud_task = None
    report_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="health-report")
    report_task = None
    try:
        if args.command == "retry-quarantine":
            print(json.dumps(store.retry_quarantine(args.subject, args.timezone)))
            return 0
        if args.command == "status":
            print(json.dumps(store.summary(), indent=2))
            return 0
        if args.command == "report":
            build_report(store, args.output, args.timezone)
            print("Saved " + str(args.output / "report.html"))
            return 0
        if args.command == "sync":
            if not args.credentials:
                parser.error("sync requires --credentials")
            from farcare_collector.integrations.tiger_archive import (
                apply_archive_schema,
                deliver_archive,
            )
            from farcare_collector.integrations.tiger_sink import apply_schema, deliver

            apply_schema(args.credentials)
            apply_archive_schema(args.credentials)
            print(
                json.dumps(
                    {
                        "measurements": deliver(store, args.credentials),
                        "archive": deliver_archive(store, args.credentials),
                    }
                )
            )
            return 0
        next_cloud = time.monotonic()
        next_report = 0
        cached_summary = store.summary()
        schema_ready = False
        while True:
            cycle_started = time.monotonic()
            status = {
                "checked_at": now(),
                "command": args.command,
                "poll_seconds": args.poll_seconds,
            }
            for directory in args.api_dir:
                status.setdefault("api_imports", []).append(
                    store.import_api(directory, args.subject, args.timezone)
                )
            status["ble"] = store.tail_ble(args.ble, args.subject, args.timezone)
            if (
                args.command == "watch"
                and args.cloud_every_seconds
                and time.monotonic() >= next_cloud
            ):
                if cloud_task is None:
                    cloud_task = cloud_worker.submit(cloud_refresh, args.cloud_days, cloud_stop)
                    status["cloud_refresh"] = "running"
                next_cloud = time.monotonic() + args.cloud_every_seconds
            if cloud_task is not None and cloud_task.done():
                try:
                    cloud_task.result()
                    status["cloud_refresh"] = "ok"
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    status["cloud_refresh"] = type(exc).__name__ + ": " + str(exc)[:250]
                cloud_task = None
            if args.command == "watch":
                # New API exports appear as the existing cloud connector completes pages.
                for directory in sorted((ROOT / "api_data").glob("*")):
                    if directory.is_dir() and (directory / "summary.json").exists():
                        try:
                            store.import_api(directory, args.subject, args.timezone)
                        except (ValueError, KeyError, OSError):
                            status["api_page_pending"] = True
            if args.credentials:
                try:
                    from farcare_collector.integrations.tiger_archive import (
                        apply_archive_schema,
                        deliver_archive,
                    )
                    from farcare_collector.integrations.tiger_sink import apply_schema, deliver

                    if not schema_ready:
                        apply_schema(args.credentials)
                        apply_archive_schema(args.credentials)
                        schema_ready = True
                    status["tiger"] = deliver(store, args.credentials)
                    status["tiger_archive"] = deliver_archive(store, args.credentials)
                except Exception as exc:
                    # psycopg error strings can include connection details; log type only.
                    status["tiger"] = {"state": "retry_pending", "error_type": type(exc).__name__}
            if args.command == "watch":
                if report_task is not None and report_task.done():
                    try:
                        report = report_task.result()
                        cached_summary = report["pipeline"]
                        status["report_generated_at"] = report["generated_at"]
                    except Exception as exc:
                        status["report_error_type"] = type(exc).__name__
                    report_task = None
                if time.monotonic() >= next_report and report_task is None:
                    report_task = report_worker.submit(
                        build_background_report, args.db, args.output, args.timezone
                    )
                    next_report = time.monotonic() + 60
                status["report_running"] = report_task is not None
            else:
                cached_summary = build_report(store, args.output, args.timezone)["pipeline"]
            # The complete history summary is refreshed with the minute report;
            # per-cycle BLE and delivery checkpoints above remain current.
            status["store"] = cached_summary
            status["cycle_seconds"] = round(time.monotonic() - cycle_started, 3)
            private_json(args.output / "status.json", status)
            print(json.dumps({k: v for k, v in status.items() if k != "store"}), flush=True)
            if args.command != "watch":
                break
            # Work time counts toward the interval. Never overlap collector cycles.
            time.sleep(max(0, args.poll_seconds - (time.monotonic() - cycle_started)))
        return 0
    except KeyboardInterrupt:
        print("Collector stopped; committed data and delivery cursor preserved.")
        return 130
    finally:
        cloud_stop.set()
        cloud_worker.shutdown(wait=True, cancel_futures=True)
        report_worker.shutdown(wait=True, cancel_futures=True)
        store.close()


if __name__ == "__main__":
    sys.exit(main())
