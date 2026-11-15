"""Descriptive daily features from canonical measurements, with no clinical labels.

This module is deliberately independent of files, network access, and device APIs.
``local_date`` is the source's authoritative civil date; timestamps establish
ordering and interval overlap, not a replacement date for a reported summary.
"""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "descriptive-personal-baseline-v1"

_ADDITIVE = {
    "steps",
    "distance_m",
    "active_zone_minutes",
    "sleep_minutes_asleep",
    "sleep_minutes_awake",
    "sleep_period_minutes",
    "exercise_duration_seconds",
    "exercise_steps",
    "exercise_distance_m",
    "exercise_active_zone_minutes",
    "exercise_calories_kcal",
    "exercise_energy_kcal",
}
_SLEEP = {"sleep_minutes_asleep", "sleep_minutes_awake", "sleep_period_minutes"}
_EXPECTED_UNITS = {
    "heart_rate_bpm": "bpm",
    "resting_heart_rate_bpm": "bpm",
    "non_rem_heart_rate_bpm": "bpm",
    "respiratory_rate_bpm": "bpm",
    "hrv_rmssd_ms": "ms",
    "hrv_daily_average_ms": "ms",
    "hrv_deep_sleep_rmssd_ms": "ms",
    "spo2_percent": "%",
    "spo2_daily_mean_percent": "%",
    "skin_temp_nightly_c": "degC",
    "skin_temp_baseline_c": "degC",
    "skin_temp_relative_stddev30d_c": "degC",
    "steps": "count",
    "distance_m": "m",
    "active_zone_minutes": "min",
    "sleep_minutes_asleep": "min",
    "sleep_minutes_awake": "min",
    "sleep_period_minutes": "min",
    "exercise_duration_seconds": "s",
    "exercise_steps": "count",
    "exercise_distance_m": "m",
    "exercise_active_zone_minutes": "min",
    "exercise_average_heart_rate_bpm": "bpm",
}

LIMITATIONS = [
    "Descriptive personal changes only; no disease, fall, apnea, or glucose inference.",
    "Each subject, source, and source stream is analyzed separately; totals across streams are not added.",
    "Missing dates are missing observations, never zero activity or proof of inactivity.",
    "Recorded intervals and sample spans are not evidence of wear coverage; wear coverage is unknown.",
    "A daily heart-rate sample mean is not resting heart rate, and BPM samples cannot establish HRV.",
    "Positive step intervals do not establish full-day activity coverage; step totals describe recorded intervals only.",
    "Sleep sessions remain assigned to the source's local_date; no inferred allocation across midnight.",
    "Baselines are retrospective medians and MADs of prior valid dates in the same stream; they do not establish clinical risk.",
    "Differences in sampling, device use, or export completeness can change descriptive summaries.",
]


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("local_date must be YYYY-MM-DD")
    return value


def _mean(values):
    # Divide first to avoid overflowing the sum of otherwise finite observations.
    return math.fsum(value / len(values) for value in values)


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    # statistics.median adds the two central values before division, which can
    # overflow even when both inputs and their median are finite.
    return ordered[middle - 1] / 2 + ordered[middle] / 2


def _finite(value):
    return value if value is None or math.isfinite(value) else None


def _validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("measurement must be an object")
    for field in ("event_id", "subject_id", "source_stream", "metric", "unit"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError("missing or invalid " + field)
    if raw.get("source") not in ("google_health", "ble"):
        raise ValueError("unsupported source")
    if raw.get("kind") not in ("sample", "interval", "daily", "session"):
        raise ValueError("unsupported kind")
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("value must be a finite number")
    value = float(value)
    metric = raw["metric"]
    expected = _EXPECTED_UNITS.get(metric)
    if expected is not None and raw["unit"] != expected:
        raise ValueError("unexpected unit for " + metric)
    if metric in _ADDITIVE and value < 0:
        raise ValueError("negative additive measurement")
    if metric.startswith("spo2_") and not 0 <= value <= 100:
        raise ValueError("percentage outside 0..100")
    if raw["unit"] in ("bpm", "ms") and value < 0:
        raise ValueError("negative rate or interval")
    attributes = raw.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("attributes must be an object")
    start = _instant(raw.get("observed_at"))
    end = _instant(raw["ended_at"]) if raw.get("ended_at") is not None else None
    if end is not None and end <= start:
        raise ValueError("ended_at must be after observed_at")
    if raw["kind"] in ("interval", "session") and end is None:
        raise ValueError("interval/session requires ended_at")
    record = {
        field: raw[field]
        for field in ("event_id", "subject_id", "source", "source_stream", "metric", "unit", "kind")
    }
    record.update(
        local_date=_day(raw.get("local_date")),
        value=value,
        attributes=dict(attributes),
        start=start,
        end=end,
    )
    return record


def _identity(record):
    return tuple(
        record[field] for field in ("subject_id", "source", "source_stream", "metric", "event_id")
    )


def _group_key(record):
    return tuple(record[field] for field in ("subject_id", "source", "source_stream", "local_date"))


def _crosses_midnight(record, zone):
    attrs = record["attributes"]
    explicit = attrs.get("crosses_local_midnight")
    if isinstance(explicit, bool):
        return explicit
    if attrs.get("coverage_end_local_date"):
        return attrs["coverage_end_local_date"] != attrs.get(
            "start_local_date", record["local_date"]
        )
    # The end is exclusive: [23:59, 00:00) belongs to one civil day.
    end = record["end"] - timedelta(microseconds=1)
    return record["start"].astimezone(zone).date() != end.astimezone(zone).date()


def _intervals(records):
    return sorted(
        (record["start"], record["end"]) for record in records if record["end"] is not None
    )


def _overlaps(records):
    end = None
    for start, current_end in _intervals(records):
        if end is not None and start < end:
            return True
        end = current_end if end is None else max(end, current_end)
    return False


def _coverage(records):
    timed = [
        record
        for record in records
        if record["kind"] != "daily"
        and not record["attributes"].get("timestamp_is_storage_anchor")
        and record["attributes"].get("observed_at_is_measurement_time") is not False
    ]
    starts = [record["start"] for record in timed]
    ends = [record["end"] or record["start"] for record in timed]
    interval_seconds = 0.0
    previous_start = previous_end = None
    for start, end in _intervals(timed):
        if previous_end is None:
            previous_start, previous_end = start, end
        elif start <= previous_end:
            previous_end = max(previous_end, end)
        else:
            interval_seconds += (previous_end - previous_start).total_seconds()
            previous_start, previous_end = start, end
    if previous_end is not None:
        interval_seconds += (previous_end - previous_start).total_seconds()
    return {
        "observed_start": _iso(min(starts)) if starts else None,
        "observed_end": _iso(max(ends)) if ends else None,
        "observed_span_seconds": (max(ends) - min(starts)).total_seconds() if starts else None,
        "distinct_sample_timestamps": len(
            {record["start"] for record in timed if record["kind"] == "sample"}
        ),
        "session_count": sum(record["kind"] == "session" for record in timed),
        "main_sleep_session_count": sum(
            record["kind"] == "session"
            and record["attributes"].get("main_sleep", record["attributes"].get("mainSleep"))
            is True
            for record in timed
        ),
        "nap_session_count": sum(
            record["kind"] == "session" and record["attributes"].get("nap") is True
            for record in timed
        ),
        "recorded_interval_union_seconds": interval_seconds if _intervals(timed) else None,
        "wear_coverage": "unknown",
        "missing_is_zero": False,
    }


def _summary(records, zone, extra_flags=()):
    metric = records[0]["metric"]
    kinds = {record["kind"] for record in records}
    units = {record["unit"] for record in records}
    flags = set(extra_flags)
    eligible = []
    for record in records:
        if record["attributes"].get("representation") == "exercise_summary" and metric in {
            "steps",
            "distance_m",
            "active_zone_minutes",
        }:
            flags.add("exercise_summary_requires_separate_metric")
            continue
        if record["kind"] == "interval" and metric in _ADDITIVE and _crosses_midnight(record, zone):
            flags.add("cross_midnight_additive_interval")
            continue
        eligible.append(record)
    result = {
        "unit": next(iter(units)) if len(units) == 1 else None,
        "kind": next(iter(kinds)) if len(kinds) == 1 else "mixed",
        "aggregation": None,
        "value": None,
        "measurement_count": len(records),
        "eligible_count": len(eligible),
        "excluded_count": len(records) - len(eligible),
        "sample_statistics": None,
        "coverage": _coverage(eligible),
        "quality_flags": [],
        "baseline_eligible": False,
    }
    if len(kinds) != 1:
        flags.add("mixed_representations")
    if len(units) != 1:
        flags.add("mixed_units")
    if not eligible:
        flags.add("no_eligible_measurements")
    if eligible and len(kinds) == 1 and len(units) == 1:
        kind = next(iter(kinds))
        values = [record["value"] for record in eligible]
        if kind == "sample":
            result["aggregation"] = "recorded_sample_mean"
            result["value"] = _mean(values)
            result["sample_statistics"] = {
                "count": len(values),
                "mean": result["value"],
                "median": _median(values),
                "minimum": min(values),
                "maximum": max(values),
            }
        elif kind == "daily":
            result["aggregation"] = "reported_daily_value"
            if len(eligible) == 1:
                result["value"] = values[0]
            else:
                flags.add("multiple_daily_summaries")
        elif _overlaps(eligible):
            flags.add("overlapping_intervals" if kind == "interval" else "overlapping_sessions")
        elif metric in _ADDITIVE:
            result["aggregation"] = (
                "recorded_interval_sum" if kind == "interval" else "recorded_session_sum"
            )
            try:
                result["value"] = _finite(math.fsum(values))
            except OverflowError:
                result["value"] = None
            if result["value"] is None:
                flags.add("numeric_overflow")
        elif len(eligible) == 1:
            result["aggregation"] = "reported_" + kind + "_value"
            result["value"] = values[0]
        else:
            # Session averages and interval estimates have no justified additive
            # or time-weighting rule in this schema.
            flags.add("no_aggregation_rule_for_multiple_" + kind + "s")
    if flags.intersection(
        {
            "conflicting_event_revision",
            "overlapping_intervals_across_dates",
            "overlapping_sessions_across_dates",
        }
    ):
        result["value"] = None
    result["quality_flags"] = sorted(flags)
    result["baseline_eligible"] = result["value"] is not None and not flags
    return result


def _sleep_provider_ambiguities(records):
    """Detect overlap across source streams without combining providers."""
    by_subject_metric = defaultdict(list)
    affected = set()
    for record in records:
        if record["metric"] in _SLEEP and record["kind"] == "session":
            by_subject_metric[(record["subject_id"], record["metric"])].append(record)
    for group in by_subject_metric.values():
        active = []
        for record in sorted(group, key=lambda item: item["start"]):
            active = [other for other in active if other["end"] > record["start"]]
            for other in active:
                if (record["source"], record["source_stream"]) != (
                    other["source"],
                    other["source_stream"],
                ):
                    affected.add((_group_key(record), record["metric"]))
                    affected.add((_group_key(other), other["metric"]))
            active.append(record)
    return affected


def _cross_date_overlaps(records):
    by_stream_metric = defaultdict(list)
    affected = defaultdict(set)
    for record in records:
        if record["metric"] in _ADDITIVE and record["kind"] in ("interval", "session"):
            key = (
                record["subject_id"],
                record["source"],
                record["source_stream"],
                record["metric"],
            )
            by_stream_metric[key].append(record)
    for group in by_stream_metric.values():
        active = []
        for record in sorted(group, key=lambda item: item["start"]):
            active = [other for other in active if other["end"] > record["start"]]
            for other in active:
                if record["local_date"] != other["local_date"]:
                    for item in (record, other):
                        code = (
                            "overlapping_"
                            + ("intervals" if item["kind"] == "interval" else "sessions")
                            + "_across_dates"
                        )
                        affected[(_group_key(item), item["metric"])].add(code)
            active.append(record)
    return affected


def _comparison(row, metric, summary, history, required):
    values = [value for _, value in history]
    result = {
        field: row[field] for field in ("subject_id", "source", "source_stream", "local_date")
    }
    result.update(
        metric=metric,
        unit=summary["unit"],
        aggregation=summary["aggregation"],
        current_value=summary["value"],
        current_measurement_count=summary["measurement_count"],
        coverage=dict(summary["coverage"]),
        baseline_status="insufficient_history",
        prior_valid_day_count=len(history),
        required_prior_days=required,
        baseline_start_date=history[0][0] if history else None,
        baseline_end_date=history[-1][0] if history else None,
        baseline_median=None,
        baseline_mad=None,
        delta=None,
        percent_delta=None,
        robust_z=None,
        algorithm_version=ALGORITHM_VERSION,
    )
    if not summary["baseline_eligible"]:
        result["baseline_status"] = "invalid_current_summary"
    elif len(history) >= required:
        center = _median(values)
        deviations = [abs(value - center) for value in values]
        spread = _finite(_median(deviations))
        delta = _finite(summary["value"] - center)
        result.update(
            baseline_median=center,
            baseline_mad=spread,
            delta=delta,
            percent_delta=_finite((delta / center) * 100) if center and delta is not None else None,
        )
        if spread is None or delta is None:
            result["baseline_status"] = "numeric_overflow"
        elif spread == 0:
            result["baseline_status"] = "insufficient_variation"
        else:
            # Divide before applying the scale factor to avoid overflow.
            result["robust_z"] = _finite((delta / spread) / 1.4826)
            result["baseline_status"] = (
                "ready" if result["robust_z"] is not None else "numeric_overflow"
            )
    return result


def build_features(measurements, timezone_name="America/New_York", min_baseline_days=14):
    """Return JSON-compatible descriptive features from canonical measurement dicts.

    No I/O occurs. Each ``daily`` row identifies one subject/source/stream/date and
    contains a ``metrics`` dictionary of daily summaries. ``comparisons`` contain
    only retrospective comparisons to earlier eligible dates in the same stream.
    ``availability`` describes observations, not wear time or sensor availability.
    The caller must select the latest revision of each source resource upstream.
    """
    if (
        isinstance(min_baseline_days, bool)
        or not isinstance(min_baseline_days, int)
        or min_baseline_days < 14
    ):
        raise ValueError("min_baseline_days must be an integer of at least 14")
    zone = ZoneInfo(timezone_name)
    unique = {}
    conflicts = set()
    conflict_groups = set()
    occurrences = defaultdict(int)
    flags = []
    counts = {"input_count": 0, "rejected_count": 0, "duplicate_count": 0}
    for index, raw in enumerate(measurements):
        counts["input_count"] += 1
        try:
            record = _validate(raw)
        except (ValueError, TypeError, OverflowError) as exc:
            counts["rejected_count"] += 1
            flags.append({"code": "invalid_measurement", "input_index": index, "detail": str(exc)})
            continue
        identity = _identity(record)
        if identity in unique:
            if record == unique[identity]:
                counts["duplicate_count"] += 1
            else:
                occurrences[identity] += 1
                conflicts.add(identity)
                conflict_groups.add((_group_key(record), record["metric"]))
                conflict_groups.add((_group_key(unique[identity]), record["metric"]))
                flags.append(
                    {
                        "code": "conflicting_event_revision",
                        "event_id": record["event_id"],
                        "subject_id": record["subject_id"],
                        "source": record["source"],
                        "source_stream": record["source_stream"],
                        "metric": record["metric"],
                    }
                )
        else:
            unique[identity] = record
            occurrences[identity] += 1
    records = [record for identity, record in unique.items() if identity not in conflicts]
    counts["accepted_count"] = len(records)
    counts["conflicting_event_count"] = len(conflicts)
    counts["conflicting_record_count"] = sum(occurrences[identity] for identity in conflicts)
    groups = defaultdict(lambda: defaultdict(list))
    for record in records:
        groups[_group_key(record)][record["metric"]].append(record)
    ambiguous_sleep = _sleep_provider_ambiguities(records)
    cross_date_overlaps = _cross_date_overlaps(records)
    daily = []
    for group_key, metric_groups in sorted(groups.items()):
        row = dict(zip(("subject_id", "source", "source_stream", "local_date"), group_key))
        row["metrics"] = {}
        row_flags = set()
        for metric, group in sorted(metric_groups.items()):
            extra = (
                ["overlapping_sleep_providers"] if (group_key, metric) in ambiguous_sleep else []
            )
            extra.extend(cross_date_overlaps.get((group_key, metric), ()))
            if (group_key, metric) in conflict_groups:
                extra.append("conflicting_event_revision")
            summary = _summary(group, zone, extra)
            row["metrics"][metric] = summary
            row_flags.update(summary["quality_flags"])
            for code in summary["quality_flags"]:
                flags.append(
                    dict(
                        zip(("subject_id", "source", "source_stream", "local_date"), group_key),
                        metric=metric,
                        code=code,
                    )
                )
        row["quality_flags"] = sorted(row_flags)
        daily.append(row)
    histories = defaultdict(list)
    availability_groups = defaultdict(list)
    comparisons = []
    for row in sorted(
        daily,
        key=lambda item: (
            item["local_date"],
            item["subject_id"],
            item["source"],
            item["source_stream"],
        ),
    ):
        for metric, summary in row["metrics"].items():
            stream_key = (row["subject_id"], row["source"], row["source_stream"], metric)
            history_key = stream_key + (summary["unit"], summary["aggregation"])
            history = histories[history_key]
            comparisons.append(_comparison(row, metric, summary, history, min_baseline_days))
            if summary["baseline_eligible"]:
                history.append((row["local_date"], summary["value"]))
            availability_groups[stream_key].append((row["local_date"], summary))
    availability = []
    for key, items in sorted(availability_groups.items()):
        days = sorted({day for day, _ in items})
        span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1
        availability.append(
            dict(
                zip(("subject_id", "source", "source_stream", "metric"), key),
                units=sorted(
                    {summary["unit"] for _, summary in items if summary["unit"] is not None}
                ),
                observed_day_count=len(days),
                comparable_day_count=sum(summary["baseline_eligible"] for _, summary in items),
                measurement_count=sum(summary["measurement_count"] for _, summary in items),
                first_local_date=days[0],
                last_local_date=days[-1],
                calendar_day_span=span,
                missing_day_count=span - len(days),
                wear_coverage="unknown",
                missing_is_zero=False,
            )
        )
    return dict(
        counts,
        schema_version=SCHEMA_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        timezone=timezone_name,
        min_baseline_days=min_baseline_days,
        daily=daily,
        availability=availability,
        comparisons=comparisons,
        quality_flags=flags,
        limitations=list(LIMITATIONS),
    )
