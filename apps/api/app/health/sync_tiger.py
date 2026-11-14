"""OWNER: Sabal. Publish Tiger daily projections through FarCare's ingest API.

Run from apps/api with its Python 3.12 environment:
  python -m app.health.sync_tiger --subject-id demo --parent-id parent_001 \
      --days 7 --api-url http://127.0.0.1:8000

Defaults to one sync. --watch-seconds 60 re-reads Tiger each cycle and skips an
unchanged successful payload. Credentials come only from app Settings; no token,
DSN or source data is logged. Restarting safely replays stable observation IDs.
The API route supplies parent validation, receipts and health.synced/SSE events.
"""

import argparse
import hashlib
import ipaddress
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError
from sqlalchemy import text

from app.config import get_settings
from app.db import session_scope, utcnow
from app.health.tiger import fetch_daily
from app.models import Person
from app.schemas.contracts import HealthIngestResult, HealthObservation


class SyncError(Exception):
    """A message safe to print without source records or credentials."""


@dataclass
class SyncState:
    successful_digest: str | None = None


@dataclass(frozen=True)
class PublishResult:
    published: bool
    accepted: int = 0
    changed: int = 0


def validate_api_url(value: str) -> str:
    """HTTP is allowed only for literal loopback destinations; never follow redirects."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise SyncError("Invalid API URL") from None
    host = parts.hostname
    if (
        parts.scheme not in ("http", "https")
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise SyncError("API URL must be an HTTP(S) base URL without credentials or query")
    loopback = host.lower() in ("localhost", "localhost.")
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parts.scheme == "http" and not loopback:
        raise SyncError("Non-local API destinations require HTTPS")
    return value.rstrip("/")


def publish_if_changed(
    client: httpx.Client,
    api_url: str,
    observations: list[HealthObservation],
    *,
    ingest_key: str,
    state: SyncState,
) -> PublishResult:
    """Advance the in-memory successful digest only after every batch is acknowledged."""
    base = validate_api_url(api_url)
    if not ingest_key or not ingest_key.strip():
        raise SyncError("INGEST_API_KEY must be configured before syncing")
    payload = [
        obs.model_dump(mode="json") for obs in sorted(observations, key=lambda o: o.observation_id)
    ]
    if not payload:
        return PublishResult(published=False)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if state.successful_digest == digest:
        return PublishResult(published=False)
    accepted = changed = 0
    for index in range(0, len(payload), 1000):
        batch = payload[index : index + 1000]
        try:
            response = client.post(
                f"{base}/api/health/observations",
                json=batch,
                headers={"X-FarCare-Ingest-Key": ingest_key},
                follow_redirects=False,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        except httpx.HTTPError:
            raise SyncError("Could not reach the FarCare ingest API") from None
        if response.status_code != 200:
            raise SyncError(
                f"FarCare ingest returned HTTP {response.status_code}; batch not acknowledged"
            )
        try:
            receipt = HealthIngestResult.model_validate(response.json())
        except (ValueError, ValidationError):
            raise SyncError("FarCare ingest returned an invalid receipt") from None
        if receipt.accepted != len(batch) or not 0 <= receipt.changed <= receipt.accepted:
            raise SyncError("FarCare ingest receipt did not acknowledge the complete batch")
        accepted += receipt.accepted
        changed += receipt.changed
    state.successful_digest = digest
    return PublishResult(published=True, accepted=accepted, changed=changed)


def _days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("days must be an integer") from None
    if not 1 <= days <= 366:
        raise argparse.ArgumentTypeError("days must be between 1 and 366")
    return days


def _watch(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("watch seconds must be a number") from None
    if not math.isfinite(seconds) or seconds < 1:
        raise argparse.ArgumentTypeError("watch seconds must be finite and at least 1")
    return seconds


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--subject-id", required=True, help="Explicit upstream Tiger subject mapping"
    )
    result.add_argument(
        "--parent-id", required=True, help="Existing FarCare parent receiving the data"
    )
    result.add_argument("--days", type=_days, default=7)
    result.add_argument("--api-url", required=True)
    result.add_argument(
        "--timezone", help="Require this IANA timezone to match the existing parent profile"
    )
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Sync once (the default)")
    mode.add_argument("--watch-seconds", type=_watch)
    return result


def read_projection(args: argparse.Namespace) -> list[HealthObservation]:
    """Use the same configured DB for parent mapping and canonical measurements."""
    try:
        with session_scope() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SET LOCAL statement_timeout = '15s'"))
            parent = session.get(Person, args.parent_id)
            if parent is None or parent.role != "parent":
                raise SyncError("The selected parent does not exist in the configured database")
            if args.timezone and args.timezone != parent.timezone:
                raise SyncError(
                    "Projection timezone must match the parent profile so completed-day "
                    "comparisons use the same calendar. Omit --timezone to use the profile."
                )
            timezone = parent.timezone
            now = utcnow()
            end = now.astimezone(ZoneInfo(timezone)).date()
            return fetch_daily(
                session,
                subject_id=args.subject_id,
                parent_id=args.parent_id,
                start=end - timedelta(days=args.days - 1),
                end=end,
                now=now,
                timezone=timezone,
            )
    except SyncError:
        raise
    except Exception:
        # SQL/driver/settings exceptions can embed a DSN or query parameter values.
        raise SyncError(
            "Could not read the Tiger projection; check database, schema and timezone configuration"
        ) from None


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        api_url = validate_api_url(args.api_url)
        if not args.subject_id.strip():
            raise SyncError("An explicit non-empty subject_id is required")
        settings = get_settings()
        if not settings.ingest_api_key.strip():
            raise SyncError("INGEST_API_KEY must be configured before syncing")
        state = SyncState()
        with httpx.Client(
            follow_redirects=False, timeout=httpx.Timeout(30.0, connect=10.0)
        ) as client:
            while True:
                try:
                    observations = read_projection(args)
                    result = publish_if_changed(
                        client,
                        api_url,
                        observations,
                        ingest_key=settings.ingest_api_key,
                        state=state,
                    )
                    print(
                        f"Published {result.accepted} daily observations; {result.changed} changed."
                        if result.published
                        else "No changes since the last acknowledged sync.",
                        flush=True,
                    )
                except SyncError as exc:
                    if args.watch_seconds is None:
                        raise
                    print(
                        f"Sync failed: {exc}. Will retry without advancing state.",
                        file=sys.stderr,
                        flush=True,
                    )
                if args.watch_seconds is None:
                    return 0
                time.sleep(args.watch_seconds)
    except SyncError as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Sync stopped.")
        return 0
    except Exception:
        print("Sync failed: check application configuration; no data was logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
