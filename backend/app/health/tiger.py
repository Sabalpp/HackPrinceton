"""OWNER: Sabal. Project canonical Tiger measurements into FarCare's daily contract.

Tiger is storage, not a sensor source. Only Google Health's declared steps, daily
resting HR and minutes-asleep records enter this projection. Live BLE BPM is never
resting HR. The existing contract cannot express SpO2, HRV, live BPM, coverage or
per-field provenance; those require coordinated additive contract/UI changes.

Days are source local-date labels encoded as UTC midnight, not physical sample
times. Missing/ambiguous metrics remain null, including today's incomplete steps.
Steps require one stream and non-overlapping intervals. Sleep requires exactly one
explicit main, non-nap session, assigned to its wake date. Resting HR alone is
rounded to the nearest integer (half up) because the existing contract is integer.
"""

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session

from app.schemas.contracts import DataMode, HealthObservation

METRICS = ("steps", "resting_heart_rate_bpm", "sleep_minutes_asleep")

# Use the same global revision ordering as measurements_current, but filter a
# candidate set before sorting revisions. Sorting the entire compressed history
# can exhaust the worker's five-second read budget after a cloud backfill.
# Repeat the scope after revision resolution: a correction may change identity,
# metric or local date. Include cross-midnight intervals in the candidate scope.
_DAILY_SCOPE = """
subject_id = :subject_id
  AND source = 'google_health'
  AND metric IN ('steps', 'resting_heart_rate_bpm', 'sleep_minutes_asleep')
  AND local_date <= :end_date
  AND (
      local_date >= :start_date
      OR attributes->>'end_local_date' >= :start_label
      OR attributes->>'coverage_end_local_date' >= :start_label
  )
"""
CURRENT_MEASUREMENTS_SQL = f"""
WITH candidates AS MATERIALIZED (
 SELECT DISTINCT event_id FROM air_health.measurement_versions
 WHERE canonical_eligible AND {_DAILY_SCOPE}
), resolved AS MATERIALIZED (
 SELECT DISTINCT ON (v.event_id) v.*
 FROM (SELECT * FROM air_health.measurement_versions WHERE canonical_eligible OFFSET 0) v
 JOIN candidates c ON c.event_id=v.event_id
 ORDER BY v.event_id,v.source_revision_at DESC,v.received_at DESC,v.version_hash DESC
)
SELECT event_id, subject_id, source, source_stream, metric, observed_at, ended_at,
       local_date, value, unit, kind, attributes
FROM resolved WHERE {_DAILY_SCOPE}
ORDER BY local_date, observed_at, event_id
"""  # noqa: S608 -- scope is a fixed SQL literal; all request values remain bound.


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _integer(value: Any, low: int, high: int, *, round_hr: bool = False) -> int | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or not low <= number <= high:
        return None
    if round_hr:
        return math.floor(number + 0.5)
    return int(number) if number.is_integer() else None


def _attrs(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _days(start: date, end: date) -> Iterable[date]:
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def observation_id(parent_id: str, subject_id: str, day: date) -> str:
    """Stable identity without exposing the upstream subject identifier in the ID."""
    identity = hashlib.sha256(f"{parent_id}\0{subject_id}".encode()).hexdigest()[:32]
    return f"fitbit_{identity}_{day.isoformat()}"


def _daily_steps(rows: list[Mapping[str, Any]], now: datetime) -> int | None:
    if not rows or len({row.get("source_stream") for row in rows}) != 1:
        return None
    intervals: list[tuple[datetime, datetime, int]] = []
    for row in rows:
        begin, finish = _instant(row.get("observed_at")), _instant(row.get("ended_at"))
        value = _integer(row.get("value"), 0, 200_000)
        if (
            not row.get("source_stream")
            or row.get("kind") != "interval"
            or row.get("unit") != "count"
            or begin is None
            or finish is None
            or finish <= begin
            or finish > now
            or value is None
        ):
            return None
        intervals.append((begin, finish, value))
    intervals.sort()
    if any(right[0] < left[1] for left, right in zip(intervals, intervals[1:], strict=False)):
        return None
    total = sum(item[2] for item in intervals)
    return total if total <= 200_000 else None


def _daily_resting_hr(rows: list[Mapping[str, Any]]) -> int | None:
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get("kind") != "daily" or row.get("unit") != "bpm" or not row.get("source_stream"):
        return None
    return _integer(row.get("value"), 20, 250, round_hr=True)


def _daily_sleep(rows: list[Mapping[str, Any]], now: datetime) -> int | None:
    if len(rows) != 1:
        return None
    row = rows[0]
    begin, finish = _instant(row.get("observed_at")), _instant(row.get("ended_at"))
    if (
        row.get("kind") != "session"
        or row.get("unit") != "min"
        or not row.get("source_stream")
        or begin is None
        or finish is None
        or not begin < finish <= now
    ):
        return None
    return _integer(row.get("value"), 0, 1440)


def project_daily(
    rows: Iterable[Mapping[str, Any]],
    *,
    subject_id: str,
    parent_id: str,
    start: date,
    end: date,
    now: datetime,
    timezone: str,
) -> list[HealthObservation]:
    """Pure projection; all requested days exist so null corrections clear old values.

    `timezone` is explicit and used for today's cutoff and absent interval date
    metadata. Canonical source civil-date labels take precedence over that fallback.
    A missing nap flag is allowed only with explicit main_sleep=True; a true or
    malformed nap flag is never treated as a non-nap session.
    """
    if not subject_id or not subject_id.strip():
        raise ValueError("An explicit subject_id is required")
    instant = _instant(now)
    if instant is None:
        raise ValueError("now must be timezone-aware")
    if (
        isinstance(start, datetime)
        or isinstance(end, datetime)
        or not 0 <= (end - start).days < 366
    ):
        raise ValueError("Projection must contain between 1 and 366 dates")
    zone = ZoneInfo(timezone)
    today = instant.astimezone(zone).date()
    steps: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    resting: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    sleep: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    ambiguous_steps: set[date] = set()

    for row in rows:
        # Defense in depth: even a fixture or alternate query cannot mix subjects
        # or substitute instantaneous BLE HR for a declared daily resting metric.
        if row.get("subject_id") != subject_id or row.get("source") != "google_health":
            continue
        metric, day = row.get("metric"), _date(row.get("local_date"))
        if metric not in METRICS or day is None:
            continue
        attrs = _attrs(row)
        if metric == "steps":
            begin_day = _date(attrs.get("start_local_date", day))
            finish = _instant(row.get("ended_at"))
            covered_day = _date(attrs.get("coverage_end_local_date"))
            if "coverage_end_local_date" not in attrs and finish is not None:
                covered_day = (finish - timedelta(microseconds=1)).astimezone(zone).date()
            invalid_dates = begin_day is None or covered_day is None or covered_day < begin_day
            if invalid_dates:
                if start <= day <= end:
                    ambiguous_steps.add(day)
                continue
            if begin_day != covered_day or attrs.get("crosses_local_midnight") is True:
                ambiguous_steps.update(_days(max(start, begin_day), min(end, covered_day)))
                if start <= day <= end:
                    ambiguous_steps.add(day)
                continue
            if day != begin_day:
                ambiguous_steps.update(d for d in (day, begin_day) if start <= d <= end)
                continue
            if start <= day <= end:
                steps[day].append(row)
        elif metric == "resting_heart_rate_bpm":
            if start <= day <= end:
                resting[day].append(row)
        else:
            if attrs.get("main_sleep") is not True or attrs.get("nap", False) is not False:
                continue
            finish = _instant(row.get("ended_at"))
            wake = _date(attrs.get("end_local_date"))
            if "end_local_date" not in attrs and finish is not None:
                wake = finish.astimezone(zone).date()
            if wake is not None and start <= wake <= end:
                sleep[wake].append(row)

    return [
        HealthObservation(
            observation_id=observation_id(parent_id, subject_id, day),
            parent_id=parent_id,
            source="fitbit",
            data_mode=DataMode.live,
            timestamp=datetime.combine(day, time.min, UTC),
            steps=(
                _daily_steps(steps[day], instant)
                if day < today and day not in ambiguous_steps
                else None
            ),
            resting_heart_rate=_daily_resting_hr(resting[day]) if day <= today else None,
            sleep_minutes=_daily_sleep(sleep[day], instant) if day <= today else None,
        )
        for day in _days(start, end)
    ]


def fetch_daily(
    session: Session,
    *,
    subject_id: str,
    parent_id: str,
    start: date,
    end: date,
    now: datetime,
    timezone: str,
) -> list[HealthObservation]:
    """Read canonical daily records in the application's existing database."""
    if not subject_id or not subject_id.strip():
        raise ValueError("An explicit subject_id is required")
    rows = session.execute(
        text(CURRENT_MEASUREMENTS_SQL),
        {
            "subject_id": subject_id,
            "start_date": start,
            "end_date": end,
            "start_label": start.isoformat(),
        },
    ).mappings()
    return project_daily(
        rows,
        subject_id=subject_id,
        parent_id=parent_id,
        start=start,
        end=end,
        now=now,
        timezone=timezone,
    )
