"""Mock Fitbit sender: a stand-in for Sabal's Google Health sync until his real code lands.

It runs like a separate system. It imports nothing from the backend and only speaks the
shared contract (HealthObservation, docs/api-contracts.md) over HTTP, exactly what the real
sync will send. All data is labeled data_mode="simulated".

    # Backfill 35 days where activity drops over the last 5 days (the demo story)
    uv run python scripts/mock_fitbit_sender.py

    # Other stories
    uv run python scripts/mock_fitbit_sender.py --scenario normal --days 14
    uv run python scripts/mock_fitbit_sender.py --scenario recovering

    # Keep "syncing" today's growing step count every 30 s, like a watch during the day
    uv run python scripts/mock_fitbit_sender.py --stream --interval 30

Env: FARCARE_API_URL (default http://localhost:8000), INGEST_API_KEY (blank is fine in dev).
"""

import argparse
import os
import random
import sys
import time
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

SCENARIOS = ("decline", "normal", "recovering")


def _factor(scenario: str, days_ago: int) -> float:
    """Activity multiplier vs. normal for a day `days_ago` days before today."""
    if scenario == "decline" and days_ago < 5:
        return 1 - 0.6 * (5 - days_ago) / 5  # 0.88 -> 0.4 over the last 5 days
    if scenario == "recovering":
        if 5 <= days_ago < 10:
            return 0.45
        if days_ago < 5:
            return 0.45 + 0.11 * (5 - days_ago)  # climbing back toward normal
    return 1.0


def observation(parent_id: str, day: date, scenario: str, fraction_of_day: float = 1.0) -> dict:
    """One daily observation in the exact contract shape. Deterministic per (parent, day)."""
    rng = random.Random(f"{parent_id}:{day.isoformat()}:{scenario}")  # noqa: S311 mock data
    days_ago = (datetime.now(UTC).date() - day).days
    f = _factor(scenario, days_ago)
    weekend = 0.85 if day.weekday() >= 5 else 1.0
    full_day_steps = rng.gauss(4100, 420) * weekend * f
    return {
        "observation_id": f"fitbit_{parent_id}_{day.isoformat()}",
        "parent_id": parent_id,
        "source": "fitbit",
        "data_mode": "simulated",
        "timestamp": datetime(day.year, day.month, day.day, tzinfo=UTC).isoformat(),
        "steps": max(0, int(full_day_steps * fraction_of_day)),
        "resting_heart_rate": int(round(rng.gauss(66, 1.5) + 5 * (1 - f))),
        "sleep_minutes": max(0, int(rng.gauss(425, 22) - 70 * (1 - f))),
    }


def fraction_of_day(tz_name: str) -> float:
    """How much of the parent's active day (until 9 PM local) has passed: today is 'so far'."""
    local = datetime.now(ZoneInfo(tz_name))
    return min(1.0, max(0.05, (local.hour * 60 + local.minute) / (21 * 60)))


def backfill(
    parent_id: str, days: int, scenario: str, tz_name: str = "Asia/Kathmandu"
) -> list[dict]:
    today = datetime.now(UTC).date()
    return [
        observation(
            parent_id,
            today - timedelta(days=i),
            scenario,
            fraction_of_day=fraction_of_day(tz_name) if i == 0 else 1.0,
        )
        for i in range(days - 1, -1, -1)
    ]


def send(client: httpx.Client, api: str, payload: list[dict]) -> dict:
    headers = {}
    if key := os.environ.get("INGEST_API_KEY"):
        headers["X-FarCare-Ingest-Key"] = key
    resp = client.post(f"{api}/api/health/observations", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise SystemExit(
            f"Backend rejected the delivery: HTTP {resp.status_code} {resp.text[:300]}"
        )
    return resp.json()


def report(receipt: dict) -> None:
    print(
        f"  backend received {receipt['accepted']} observation(s) "
        f"({receipt.get('changed', '?')} new or changed), "
        f"{receipt.get('days_received', '?')} day(s), mode={receipt.get('data_mode')}"
    )
    for change in receipt.get("notable_changes", []):
        print(f"  notable: {change}")
    if receipt.get("suggest_checkin"):
        print("  backend flagged: a gentle check-in is suggested")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--api", default=os.environ.get("FARCARE_API_URL", "http://localhost:8000"))
    ap.add_argument("--parent-id", default="parent_001")
    ap.add_argument("--scenario", choices=SCENARIOS, default="decline")
    ap.add_argument("--days", type=int, default=35, help="days to backfill (1-120)")
    ap.add_argument("--stream", action="store_true", help="keep sending today's running total")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between stream syncs")
    ap.add_argument("--timezone", default="Asia/Kathmandu", help="parent's local timezone")
    args = ap.parse_args()
    if not 1 <= args.days <= 120:
        raise SystemExit("--days must be between 1 and 120")

    sys.stdout.reconfigure(line_buffering=True)  # show progress live, even when piped
    with httpx.Client(timeout=15) as client:
        payload = backfill(args.parent_id, args.days, args.scenario, args.timezone)
        print(
            f"[mock Fitbit sync] sending {len(payload)} days ({args.scenario}) "
            f"for {args.parent_id} to {args.api}"
        )
        report(send(client, args.api, payload))
        if not args.stream:
            return 0

        tz = ZoneInfo(args.timezone)
        print(
            f"[mock Fitbit sync] streaming today's steps every {args.interval:.0f}s (Ctrl+C to stop)"
        )
        try:
            while True:
                local = datetime.now(tz)
                fraction = fraction_of_day(args.timezone)
                obs = observation(
                    args.parent_id,
                    datetime.now(UTC).date(),
                    args.scenario,
                    fraction_of_day=fraction,
                )
                print(f"  {local:%H:%M} local: today {obs['steps']:,} steps so far")
                report(send(client, args.api, [obs]))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
