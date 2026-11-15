"""Read bounded, canonical sensor history for one server-authorized watch mapping.

The latest eligible revision is resolved by event identity, matching
air_health.measurements_current. Candidate lookups use the existing
(subject_id, metric, observed_at) index, then the event revision index. This
avoids sorting the entire canonical view on every dashboard refresh.
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

from app.db import utcnow
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
)

# Only static allowlisted metric names enter this SQL. No subject/parent/request
# text is interpolated. Inputs, clocks, bounds and subject identity are parameters.
_WANTED = ",\n".join(
    ["('ble', 'heart_rate_bpm', 600, 300, 7)"]
    + [f"('google_health', '{spec.metric}', 20, 2, 35)" for spec in CLOUD_METRICS]
)
SENSOR_ROWS_SQL = f"""
WITH wanted(source, metric, raw_limit, result_limit, lookback_days) AS (VALUES {_WANTED})
SELECT current_records.*
FROM wanted w
CROSS JOIN LATERAL (
    WITH candidates AS (
        SELECT DISTINCT event_id FROM (
            SELECT event_id
            FROM air_health.measurement_versions
            WHERE subject_id = :subject_id AND source = w.source AND metric = w.metric
              AND canonical_eligible
              AND observed_at >= CAST(:now AS timestamptz) - w.lookback_days * INTERVAL '1 day'
              AND observed_at <= CAST(:now AS timestamptz) + INTERVAL '1 day'
            ORDER BY observed_at DESC
            LIMIT w.raw_limit
        ) recent_candidates
    ), resolved AS (
        SELECT v.* FROM candidates c
        CROSS JOIN LATERAL (
            SELECT * FROM air_health.measurement_versions
            WHERE event_id = c.event_id AND canonical_eligible
            ORDER BY source_revision_at DESC, received_at DESC, version_hash DESC
            LIMIT 1
        ) v
    )
    SELECT event_id, subject_id, source, source_stream, metric, observed_at,
           ended_at, local_date, value, unit, kind, attributes
    FROM resolved
    WHERE subject_id = :subject_id AND source = w.source AND metric = w.metric
      AND observed_at >= CAST(:now AS timestamptz) - w.lookback_days * INTERVAL '1 day'
    ORDER BY observed_at DESC, event_id
    LIMIT w.result_limit
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
        cloud_metrics=[
            project_metric(
                latest("google_health", spec.metric),
                spec,
                source="google_health",
                now=now,
                timezone=parent.timezone,
            )
            for spec in CLOUD_METRICS
        ],
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
        # A failed read must not poison the request's surrounding DB transaction.
        with session.begin_nested():
            rows = _query_sensor_rows(session, subject_id=link.subject_id, now=clock)
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
