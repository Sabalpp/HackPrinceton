#!/usr/bin/env python3
"""Prepare the private offline FarCare preview from existing real tester records."""

import argparse
import hashlib
import json
import logging
import os
import socket
import sqlite3
import sys
import tempfile
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import REPO_ROOT, WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT
PRIVATE = ROOT / "pipeline_data"
DATABASE = PRIVATE / "farcare_preview.sqlite3"
METADATA = PRIVATE / "farcare_preview_metadata.json"
SOURCE = PRIVATE / "farcare_source_rows.json"
ZONE = "America/New_York"
APPLICATION_ID = 0x46435250
SYNTHETIC_HANDLES = {
    "parent_001": "+15550100001",
    "child_001": "+15550100002",
    "responder_001": "+15550100003",
    "responder_002": "+15550100004",
}


def configure_preview(database):
    """Set safe process settings before importing any application modules."""
    database = Path(database)
    permitted_name = database.name == DATABASE.name or (
        database.name.startswith(".farcare_preview-") and database.name.endswith(".sqlite3")
    )
    if (
        not permitted_name
        or database.is_symlink()
        or not database.resolve().is_relative_to(PRIVATE.resolve())
    ):
        raise ValueError("Preview database must be a regular private preview file")
    os.environ.update(
        APP_ENV="test",
        DATABASE_URL="sqlite:///" + str(database),
        BACKGROUND_TASKS_ENABLED="false",
        PROACTIVE_CHECKINS_ENABLED="false",
        PHOTON_API_KEY="",
        PHOTON_PROJECT_ID="",
        PHOTON_WEBHOOK_SECRET="",
        PHOTON_DELIVERY="off",
        GEMINI_API_KEY="",
        INGEST_API_KEY="",
        MESSAGING_CHANNEL="console",
        DEMO_MODE="true",
        SEED_DEMO_DATA="false",
        SEED_HEALTH_DATA="false",
        DEMO_CHILD_TOKEN="dev-child-token",
        DEMO_PARENT_TOKEN="dev-parent-token",
        DEMO_ADMIN_TOKEN="dev-admin-token",
        DEMO_PARENT_HANDLE=SYNTHETIC_HANDLES["parent_001"],
        DEMO_CHILD_HANDLE=SYNTHETIC_HANDLES["child_001"],
        DEMO_RESPONDER_HANDLE=SYNTHETIC_HANDLES["responder_001"],
        DEMO_RESPONDER2_HANDLE=SYNTHETIC_HANDLES["responder_002"],
        DEMO_EMERGENCY_NUMBER="",
    )
    os.environ.pop("TEST_DATABASE_URL", None)
    logging.disable(logging.CRITICAL)
    api = str(REPO_ROOT / "backend")
    if api not in sys.path:
        sys.path.insert(0, api)
    from app.config import get_settings
    from app.db import reset_engine

    get_settings.cache_clear()
    reset_engine()


def private_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def offline_guard():
    def blocked(*_args, **_kwargs):
        raise RuntimeError("Preview preparation cannot access a network")

    guard = ExitStack()
    guard.enter_context(patch.object(socket.socket, "connect", blocked))
    guard.enter_context(patch.object(socket.socket, "connect_ex", blocked))
    guard.enter_context(patch.object(socket, "create_connection", blocked))
    guard.enter_context(patch.object(socket, "getaddrinfo", blocked))
    return guard


def real_projection(clock):
    if SOURCE.is_symlink():
        raise ValueError("The source snapshot must be a regular local file")
    raw = SOURCE.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows:
        raise ValueError("The real health snapshot is empty or invalid")
    dates = [date.fromisoformat(row["local_date"]) for row in rows]
    from app.health.tiger import project_daily

    observations = project_daily(
        rows,
        subject_id="demo",
        parent_id="parent_001",
        start=min(dates),
        end=clock.astimezone(ZoneInfo(ZONE)).date(),
        now=clock,
        timezone=ZONE,
    )
    return raw, rows, observations


def ensure_synthetic_handles():
    """Repair only four synthetic routing handles, without reseeding or resetting."""
    if DATABASE.is_symlink() or not DATABASE.is_file():
        raise ValueError("A marked regular preview database is required")
    with sqlite3.connect(DATABASE, timeout=10) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("Refusing to change an unmarked database")
        people = dict(connection.execute("SELECT id,handle FROM people"))
        if not set(SYNTHETIC_HANDLES) <= set(people):
            raise ValueError("The preview family is incomplete; refusing to reseed it")
        if any(
            handle in SYNTHETIC_HANDLES.values() and person not in SYNTHETIC_HANDLES
            for person, handle in people.items()
        ):
            raise ValueError("Synthetic handles conflict with another preview person")
        changed = [
            person for person, handle in SYNTHETIC_HANDLES.items() if people[person] != handle
        ]
        if changed:
            connection.executemany(
                "UPDATE people SET handle=NULL WHERE id=?", [(person,) for person in changed]
            )
            connection.executemany(
                "UPDATE people SET handle=? WHERE id=?",
                [(SYNTHETIC_HANDLES[person], person) for person in changed],
            )
        return len(changed)


def prepare_preview(*, fresh=False):
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if DATABASE.is_symlink() or METADATA.is_symlink():
        raise ValueError("Refusing a symlink at the fixed preview destination")
    if DATABASE.exists():
        with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as existing:
            if existing.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError(
                    "Existing database is not this isolated preview; refusing to replace it"
                )
        if not fresh:
            report = json.loads(METADATA.read_bytes())
            if report.get("format") != "farcare-private-preview-v1":
                raise ValueError("Preview metadata is missing; use --fresh for a marked preview")
            changed = ensure_synthetic_handles()
            report.update(preview_configuration_version=2, synthetic_console_handles=True)
            private_json(METADATA, report)
            os.chmod(DATABASE, 0o600)
            os.chmod(METADATA, 0o600)
            return {**report, "reused": True, "synthetic_handles_updated": changed}

    fd, temporary = tempfile.mkstemp(prefix=".farcare_preview-", suffix=".sqlite3", dir=PRIVATE)
    os.close(fd)
    staged = Path(temporary)
    old_umask = os.umask(0o077)
    try:
        configure_preview(staged)
        with offline_guard():
            from app.db import init_db, reset_engine, session_scope
            from app.health.service import ingest
            from app.models import FamilyLink, Person
            from app.seed import seed
            from sqlmodel import select

            clock = datetime.now(timezone.utc)
            raw, rows, observations = real_projection(clock)
            init_db()
            with session_scope() as session:
                seed(session)  # Explicit synthetic family; health seeding is disabled.
                parent = session.get(Person, "parent_001")
                parent.timezone = ZONE
                parent.display_name = "Fitbit wearer (preview)"
                session.add(parent)
                link = session.exec(
                    select(FamilyLink).where(FamilyLink.parent_id == parent.id)
                ).one()
                link.parent_label = "Demo wearer"
                session.add(link)
                ingest(session, observations)
            reset_engine()
            with sqlite3.connect(staged) as connection:
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            report = {
                "format": "farcare-private-preview-v1",
                "generated_at": clock.isoformat(),
                "database": DATABASE.name,
                "source_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
                "source_records": len(rows),
                "observation_days": len(observations),
                "date_range": {
                    "start": observations[0].timestamp.date().isoformat(),
                    "end": observations[-1].timestamp.date().isoformat(),
                },
                "metric_days": {
                    metric: sum(getattr(obs, metric) is not None for obs in observations)
                    for metric in ("steps", "resting_heart_rate", "sleep_minutes")
                },
                "timezone": ZONE,
                "health_data_mode": "live",
                "fresh_remote_read": False,
                "people": "Synthetic demo family; real consenting tester health records",
                "messaging": "console only; no real recipients or automatic messages",
                "preview_configuration_version": 2,
                "synthetic_console_handles": True,
                "url": "http://localhost:3000",
                "console_url": "http://localhost:8000/console",
                "reused": False,
            }
            os.replace(staged, DATABASE)
            os.chmod(DATABASE, 0o600)
            private_json(METADATA, report)
            return report
    finally:
        os.umask(old_umask)
        if staged.exists():
            staged.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh", action="store_true", help="Rebuild only the marked private preview database"
    )
    args = parser.parse_args()
    try:
        result = prepare_preview(fresh=args.fresh)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": "Private preview preparation failed; no credentials or readings printed.",
                }
            )
        )
        return 1
    print(json.dumps({"status": "ready", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
