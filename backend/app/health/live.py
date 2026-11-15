"""Read bounded, canonical sensor history for one server-authorized watch mapping.

The latest eligible revision is resolved by event identity, matching
air_health.measurements_current. Candidate lookups use the existing
(subject_id, metric, observed_at) index, then resolve their revisions in one
batch. This avoids sorting the entire canonical view or repeatedly scanning
compressed chunks for each sample on every dashboard refresh.
No medical thresholds, interpolated samples, or inferred sensor values live here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db import utcnow, watch_data_session
from app.health.live_models import WatchSourceLink
from app.models import Person
from app.schemas.experience import SensorHealthReport, SensorMetric, SensorPoint


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    label: str
    unit: str
    kind: str
    freshness_seconds: int


LIVE_HEART_RATE = MetricSpec("heart_rate_bpm", "Live heart rate", "bpm", "sample", 20)
CLOUD_METRICS = (
    MetricSpec("heart_rate_bpm", "Cloud heart-rate sample", "bpm", "sample", 900),
    MetricSpec("resting_heart_rate_bpm", "Daily resting heart rate", "bpm", "daily", 172800),
    MetricSpec("steps", "Steps in latest reported interval", "count", "interval", 3600),
    MetricSpec(
        "sleep_minutes_asleep", "Sleep in latest reported session", "min", "session", 172800
    ),
    MetricSpec("spo2_percent", "Blood oxygen sample", "%", "sample", 900),
    MetricSpec("spo2_daily_mean_percent", "Daily mean blood oxygen", "%", "daily", 172800),
    MetricSpec("hrv_rmssd_ms", "Reported HRV (RMSSD)", "ms", "sample", 86400),
    MetricSpec("hrv_daily_average_ms", "Daily reported HRV", "ms", "daily", 172800),
    MetricSpec("hrv_deep_sleep_rmssd_ms", "Deep-sleep HRV (RMSSD)", "ms", "daily", 172800),
    MetricSpec("respiratory_rate_bpm", "Daily breathing rate", "bpm", "daily", 172800),
    MetricSpec("skin_temp_nightly_c", "Nightly skin temperature", "degC", "daily", 172800),
    MetricSpec(
        "skin_temp_baseline_c", "Reported skin-temperature baseline", "degC", "daily", 172800
    ),
    MetricSpec("distance_m", "Distance in latest reported interval", "m", "interval", 3600),
    MetricSpec(
        "active_zone_minutes", "Active zone minutes in latest interval", "min", "interval", 3600
    ),
    MetricSpec("non_rem_heart_rate_bpm", "Non-REM heart rate", "bpm", "daily", 172800),
    MetricSpec(
        "skin_temp_relative_stddev30d_c",
        "Skin-temperature variation (30 days)",
        "degC",
        "daily",
        172800,
    ),
    MetricSpec(
        "spo2_daily_lower_bound_percent", "Daily blood oxygen lower bound", "%", "daily", 172800
    ),
    MetricSpec(
        "spo2_daily_upper_bound_percent", "Daily blood oxygen upper bound", "%", "daily", 172800
    ),
    MetricSpec(
        "spo2_daily_stddev_percent", "Daily blood oxygen standard deviation", "%", "daily", 172800
    ),
    MetricSpec(
        "sleep_minutes_awake", "Time awake in latest sleep session", "min", "session", 172800
    ),
    MetricSpec("sleep_period_minutes", "Length of latest sleep session", "min", "session", 172800),
    MetricSpec(
        "sleep_minutes_after_wake",
        "Minutes after waking in latest sleep session",
        "min",
        "session",
        172800,
    ),
    MetricSpec(
        "sleep_minutes_to_fall_asleep",
        "Minutes to fall asleep in latest session",
        "min",
        "session",
        172800,
    ),
    MetricSpec("exercise_duration_seconds", "Latest exercise duration", "s", "session", 172800),
    MetricSpec(
        "exercise_active_duration_seconds",
        "Latest exercise active duration",
        "s",
        "session",
        172800,
    ),
    MetricSpec("exercise_calories_kcal", "Latest exercise energy", "kcal", "session", 172800),
    MetricSpec("exercise_steps", "Latest exercise steps", "count", "session", 172800),
    MetricSpec("exercise_distance_m", "Latest exercise distance", "m", "session", 172800),
    MetricSpec(
        "exercise_average_pace_s_per_m", "Latest exercise average pace", "s/m", "session", 172800
    ),
    MetricSpec(
        "exercise_average_heart_rate_bpm",
        "Latest exercise average heart rate",
        "bpm",
        "session",
        172800,
    ),
    MetricSpec(
        "exercise_active_zone_minutes",
        "Latest exercise active zone minutes",
        "min",
        "session",
        172800,
    ),
)

# Only static allowlisted metric names enter this SQL. No subject/parent/request
# text is interpolated. Inputs, clocks, bounds and subject identity are parameters.
_WANTED = ",\n".join(
    ["('ble', 'heart_rate_bpm', 600, 300, 7)"]
    + [f"('google_health', '{spec.metric}', 20, 2, 35)" for spec in CLOUD_METRICS]
)
# Resolve candidate revisions in one batch. Per-event LATERAL lookups repeatedly
# scan compressed Timescale chunks and can exceed the live-read timeout.
# Resolve globally before filtering identity/metric so corrections cannot leak
# an old revision after an event changes subject, metric or timestamp.
# OFFSET 0 is an intentional planner boundary: Timescale otherwise chooses an
# identity-ordered merge join that sorts/decompresses the whole revision history
# before applying the small candidate set. Keep the revision scan unordered so
# the candidate hash join runs before DISTINCT ON sorts the matching revisions.
# This changes no connection-wide planner settings and preserves global identity.
SENSOR_ROWS_SQL = f"""
WITH wanted(source, metric, raw_limit, result_limit, lookback_days) AS (VALUES {_WANTED}),
candidates AS MATERIALIZED (
 SELECT DISTINCT recent.event_id FROM wanted w CROSS JOIN LATERAL (
  SELECT event_id FROM air_health.measurement_versions
  WHERE subject_id=:subject_id AND source=w.source AND metric=w.metric AND canonical_eligible
  AND observed_at >= CAST(:now AS timestamptz)-w.lookback_days*INTERVAL '1 day'
  AND observed_at <= CAST(:now AS timestamptz)+INTERVAL '1 day'
  ORDER BY observed_at DESC LIMIT w.raw_limit
 ) recent
), resolved AS MATERIALIZED (
 SELECT DISTINCT ON (v.event_id) v.*
 FROM (SELECT * FROM air_health.measurement_versions WHERE canonical_eligible OFFSET 0) v
 JOIN candidates c ON c.event_id=v.event_id
 ORDER BY v.event_id,v.source_revision_at DESC,v.received_at DESC,v.version_hash DESC
)
SELECT current_records.* FROM wanted w CROSS JOIN LATERAL (
 SELECT event_id,subject_id,source,source_stream,metric,observed_at,ended_at,local_date,value,unit,kind,attributes
 FROM resolved WHERE subject_id=:subject_id AND source=w.source AND metric=w.metric
 AND observed_at >= CAST(:now AS timestamptz)-w.lookback_days*INTERVAL '1 day'
 ORDER BY observed_at DESC,event_id LIMIT w.result_limit
) current_records
"""  # noqa: S608


def _instant(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _day(value: Any) -> date | None:
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


def missing_metric(spec: MetricSpec) -> SensorMetric:
    return SensorMetric(
        metric=spec.metric,
        label=spec.label,
        unit=spec.unit,
        message="No source-reported reading is available in the recent history window.",
    )


def project_metric(
    row: Mapping[str, Any] | None,
    spec: MetricSpec,
    *,
    source: str,
    now: datetime,
    timezone: str,
) -> SensorMetric:
    if row is None:
        return missing_metric(spec)
    result = missing_metric(spec)
    result.freshness = "invalid"
    result.message = (
        "The latest record has an invalid value, unit, time or source; it is not displayed."
    )
    stamp, finish = _instant(row.get("observed_at")), _instant(row.get("ended_at"))
    local_day = _day(row.get("local_date"))
    value = row.get("value")
    attrs = row.get("attributes")
    if (
        row.get("metric") != spec.metric
        or row.get("source") != source
        or not row.get("source_stream")
        or row.get("unit") != spec.unit
        or row.get("kind") != spec.kind
        or isinstance(value, bool)
        or not isinstance(value, Real)
        or stamp is None
        or local_day is None
        or isinstance(attrs, Mapping)
        and attrs.get("data_mode", "live") != "live"
    ):
        return result
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return result
    if not isfinite(value):
        return result
    if (
        (spec.unit != "degC" and value < 0)
        or (spec.unit == "%" and value > 100)
        or (spec.unit == "count" and not value.is_integer())
        or (spec.metric == "heart_rate_bpm" and (value <= 0 or value > 65535))
        or (spec.unit == "degC" and value < -273.15)
    ):
        return result
    today = now.astimezone(ZoneInfo(timezone)).date()
    if spec.kind == "daily":
        # Daily timestamps are civil-date anchors, not physical sample instants.
        if local_day > today:
            return result
        fresh = (today - local_day).days <= 2
        age = None
    else:
        if stamp > now:
            return result
        if spec.kind in ("session", "interval") and (
            finish is None or finish <= stamp or finish > now
        ):
            return result
        age = (now - (finish or stamp)).total_seconds()
        fresh = age <= spec.freshness_seconds
    return SensorMetric(
        metric=spec.metric,
        label=spec.label,
        unit=spec.unit,
        value=value,
        source=source,
        kind=spec.kind,
        observed_at=stamp,
        ended_at=finish,
        local_date=local_day.isoformat(),
        freshness="fresh" if fresh else "stale",
        age_seconds=age,
        message=(
            "Latest received BLE sample. This is not resting heart rate or a medical assessment."
            if source == "ble" and fresh
            else "Historical reading; new data has not arrived within this metric's freshness window."
            if not fresh
            else "Source-reported cloud value. Its date or interval is shown; it is not a live sensor stream."
        ),
    )


def empty_report(parent_id: str, *, now: datetime, status: str, message: str) -> SensorHealthReport:
    return SensorHealthReport(
        parent_id=parent_id,
        connection_status=status,
        generated_at=now,
        live_heart_rate=missing_metric(LIVE_HEART_RATE),
        cloud_metrics=[missing_metric(spec) for spec in CLOUD_METRICS],
        message=message,
    )


def daily_change_message(
    current: SensorMetric,
    latest_row: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]],
    spec: MetricSpec,
    *,
    now: datetime,
    timezone: str,
) -> str:
    """Describe two reported days, never substitute this for a personal baseline."""
    if spec.kind != "daily" or current.value is None or latest_row is None:
        return current.message
    previous = [
        row
        for row in records
        if row.get("source_stream") == latest_row.get("source_stream")
        and _day(row.get("local_date")) is not None
        and _day(row.get("local_date")) < _day(current.local_date)
    ]
    if not previous:
        return current.message
    previous_day = max(_day(row.get("local_date")) for row in previous)
    candidates = [row for row in previous if _day(row.get("local_date")) == previous_day]
    # Conflicting same-day summaries cannot define a unique comparison.
    projected = [
        project_metric(row, spec, source="google_health", now=now, timezone=timezone)
        for row in candidates
    ]
    if any(row.value is None for row in projected) or len({row.value for row in projected}) != 1:
        return current.message
    prior = projected[0]
    change = current.value - prior.value
    if not isfinite(change):
        return current.message
    unit = "percentage points" if spec.unit == "%" else "°C" if spec.unit == "degC" else spec.unit
    text = (
        f" Previous reported day ({previous_day}): {prior.value:g} {spec.unit}. "
        f"Change = {current.value:g} − {prior.value:g} = {change:+.2f} {unit}."
    )
    # Celsius has an arbitrary zero; a relative percentage is not meaningful.
    if prior.value > 0 and spec.unit not in ("degC", "%"):
        percent = 100 * (change / prior.value)
        if isfinite(percent):
            text += f" Relative change = change / previous × 100 = {percent:+.1f}%."
    return (
        current.message
        + text
        + " Two reported days are not a learned personal baseline or a medical assessment."
    )


def project_sensor_rows(
    rows: Iterable[Mapping[str, Any]], *, parent: Person, subject_id: str, now: datetime
) -> SensorHealthReport:
    if _instant(now) is None or not subject_id.strip():
        raise ValueError("An aware clock and explicit subject identity are required")
    # Repeat identity checks even though the database query is already scoped.
    filtered = [r for r in rows if r.get("subject_id") == subject_id]
    by_metric: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in filtered:
        by_metric.setdefault((row.get("source"), row.get("metric")), []).append(row)
    for records in by_metric.values():
        records.sort(
            key=lambda r: _instant(r.get("observed_at")) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    def latest(source, metric):
        records = by_metric.get((source, metric), [])
        if not records:
            return None
        first = records[0]
        tied = [r for r in records if r.get("observed_at") == first.get("observed_at")]
        if len({(r.get("source_stream"), r.get("value")) for r in tied}) > 1:
            return {**first, "value": None}
        return first

    def cloud_metric(spec):
        row = latest("google_health", spec.metric)
        metric = project_metric(
            row, spec, source="google_health", now=now, timezone=parent.timezone
        )
        metric.message = daily_change_message(
            metric,
            row,
            by_metric.get(("google_health", spec.metric), []),
            spec,
            now=now,
            timezone=parent.timezone,
        )
        if spec.kind == "interval" and metric.value is not None:
            metric.message += " This is the latest reported interval, not a complete daily total."
        return metric

    hr = project_metric(
        latest("ble", "heart_rate_bpm"),
        LIVE_HEART_RATE,
        source="ble",
        now=now,
        timezone=parent.timezone,
    )
    hr_rows = by_metric.get(("ble", "heart_rate_bpm"), [])
    active_streams = {
        r.get("source_stream")
        for r in hr_rows
        if (_instant(r.get("observed_at")) or datetime.min.replace(tzinfo=UTC))
        >= now - timedelta(seconds=LIVE_HEART_RATE.freshness_seconds)
    }
    if len(active_streams) > 1:
        hr = missing_metric(LIVE_HEART_RATE)
        hr.freshness = "invalid"
        hr.message = "More than one watch stream is active for this subject; check the collector identity before using these readings."
    latest_stream = hr_rows[0].get("source_stream") if hr_rows else None
    history = []
    seen = set()
    values_at_time: dict[Any, set[Any]] = {}
    for row in hr_rows:
        values_at_time.setdefault(row.get("observed_at"), set()).add(row.get("value"))
    conflicting_times = {stamp for stamp, values in values_at_time.items() if len(values) > 1}
    for row in hr_rows[:300]:
        if (
            len(active_streams) > 1
            or row.get("source_stream") != latest_stream
            or row.get("observed_at") in conflicting_times
        ):
            continue
        point = project_metric(
            row, LIVE_HEART_RATE, source="ble", now=now, timezone=parent.timezone
        )
        if (
            point.value is None
            or point.observed_at is None
            or point.observed_at < now - timedelta(minutes=15)
        ):
            continue
        # Multiple records at the same timestamp do not imply extra observations.
        if point.observed_at not in seen:
            seen.add(point.observed_at)
            history.append(SensorPoint(observed_at=point.observed_at, value=point.value))
    history.sort(key=lambda p: p.observed_at)
    return SensorHealthReport(
        parent_id=parent.id,
        connection_status="available",
        generated_at=now,
        live_heart_rate=hr,
        cloud_metrics=[cloud_metric(spec) for spec in CLOUD_METRICS],
        recent_heart_rate=history,
        message="Read directly from the linked Tiger source. Missing values remain missing; no raw waveform, blood pressure or HRV is inferred from BPM.",
    )


def _query_sensor_rows(
    session: Session, *, subject_id: str, now: datetime
) -> list[Mapping[str, Any]]:
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("The sensor source requires Tiger PostgreSQL")
    session.execute(text("SET LOCAL statement_timeout = '3s'"))
    return list(
        session.execute(text(SENSOR_ROWS_SQL), {"subject_id": subject_id, "now": now}).mappings()
    )


def read_sensor_health(
    session: Session, parent: Person, *, now: datetime | None = None
) -> SensorHealthReport:
    clock = now or utcnow()
    link = None
    try:
        with session.begin_nested():
            link = session.get(WatchSourceLink, parent.id)
        if link is None or not link.enabled:
            return empty_report(
                parent.id,
                now=clock,
                status="not_linked",
                message="A verified watch source has not been linked to this parent yet.",
            )
        # A failed read must not poison the request's surrounding DB transaction; with
        # TIGER_DATABASE_URL set it runs on a separate read-only Tiger connection.
        with watch_data_session(session) as watch:
            rows = _query_sensor_rows(watch, subject_id=link.subject_id, now=clock)
        report = project_sensor_rows(rows, parent=parent, subject_id=link.subject_id, now=clock)
    except (SQLAlchemyError, ValueError, KeyError):
        report = empty_report(
            parent.id,
            now=clock,
            status="unavailable",
            message="The sensor history could not be read. Check the collector and Tiger connection; previous readings are not presented as current.",
        )
    if link is not None:
        report.last_daily_sync_at = link.last_sync_at
        report.daily_sync_status = (
            link.sync_status
            if link.sync_status in ("pending", "ok", "unavailable")
            else "unavailable"
        )
    return report
