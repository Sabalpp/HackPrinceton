"""Strict, local adapters for the saved Google Health and BLE record formats.

Only source-reported measurements and explicitly named interval durations are
normalized. Raw records belong in the caller's revision store. No HRV, clinical
classification, midnight allocation, or source identity is inferred.
"""

import calendar
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
PHYSICAL_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$")
DURATION_RE = re.compile(r"^([+-]?\d+(?:\.\d{1,9})?)s$")
PAYLOAD_KEYS = {
    "heart-rate": "heartRate",
    "daily-resting-heart-rate": "dailyRestingHeartRate",
    "heart-rate-variability": "heartRateVariability",
    "daily-heart-rate-variability": "dailyHeartRateVariability",
    "oxygen-saturation": "oxygenSaturation",
    "daily-oxygen-saturation": "dailyOxygenSaturation",
    "daily-respiratory-rate": "dailyRespiratoryRate",
    "daily-sleep-temperature-derivations": "dailySleepTemperatureDerivations",
    "steps": "steps",
    "distance": "distance",
    "active-zone-minutes": "activeZoneMinutes",
    "sleep": "sleep",
    "exercise": "exercise",
}
SAMPLE_FIELDS = {
    "heart-rate": {"beatsPerMinute": ("heart_rate_bpm", "bpm", 1)},
    "heart-rate-variability": {
        "rootMeanSquareOfSuccessiveDifferencesMilliseconds": ("hrv_rmssd_ms", "ms", 1)
    },
    "oxygen-saturation": {"percentage": ("spo2_percent", "%", 1)},
}
DAILY_FIELDS = {
    "daily-resting-heart-rate": {"beatsPerMinute": ("resting_heart_rate_bpm", "bpm", 1)},
    "daily-heart-rate-variability": {
        "averageHeartRateVariabilityMilliseconds": ("hrv_daily_average_ms", "ms", 1),
        "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": (
            "hrv_deep_sleep_rmssd_ms",
            "ms",
            1,
        ),
        "nonRemHeartRateBeatsPerMinute": ("non_rem_heart_rate_bpm", "bpm", 1),
    },
    "daily-oxygen-saturation": {
        "averagePercentage": ("spo2_daily_mean_percent", "%", 1),
        "lowerBoundPercentage": ("spo2_daily_lower_bound_percent", "%", 1),
        "upperBoundPercentage": ("spo2_daily_upper_bound_percent", "%", 1),
        "standardDeviationPercentage": ("spo2_daily_stddev_percent", "%", 1),
    },
    "daily-respiratory-rate": {"breathsPerMinute": ("respiratory_rate_bpm", "bpm", 1)},
    "daily-sleep-temperature-derivations": {
        "nightlyTemperatureCelsius": ("skin_temp_nightly_c", "degC", 1),
        "baselineTemperatureCelsius": ("skin_temp_baseline_c", "degC", 1),
        "relativeNightlyStddev30dCelsius": ("skin_temp_relative_stddev30d_c", "degC", 1),
    },
}
INTERVAL_FIELDS = {
    "steps": {"count": ("steps", "count", 1)},
    "distance": {"millimeters": ("distance_m", "m", 0.001)},
    "active-zone-minutes": {"activeZoneMinutes": ("active_zone_minutes", "min", 1)},
}
SLEEP_FIELDS = {
    "minutesAsleep": ("sleep_minutes_asleep", "min", 1),
    "minutesAwake": ("sleep_minutes_awake", "min", 1),
    "minutesInSleepPeriod": ("sleep_period_minutes", "min", 1),
    "minutesAfterWakeUp": ("sleep_minutes_after_wake", "min", 1),
    "minutesToFallAsleep": ("sleep_minutes_to_fall_asleep", "min", 1),
}
EXERCISE_FIELDS = {
    "caloriesKcal": ("exercise_calories_kcal", "kcal", 1),
    "distanceMillimeters": ("exercise_distance_m", "m", 0.001),
    "steps": ("exercise_steps", "count", 1),
    "averagePaceSecondsPerMeter": ("exercise_average_pace_s_per_m", "s/m", 1),
    "averageHeartRateBeatsPerMinute": ("exercise_average_heart_rate_bpm", "bpm", 1),
    "activeZoneMinutes": ("exercise_active_zone_minutes", "min", 1),
}


def record_hash(record):
    """SHA256 of deterministic UTF-8 JSON; non-JSON/nonfinite values are rejected."""
    try:
        text = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("record must be finite, JSON-serializable data") from exc
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _object(value, label):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with text keys")
    return value


def _keys(value, allowed, label):
    _object(value, label)
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")


def _text(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be nonempty text")
    return value


def _number(value, label, nonnegative=True, integer=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label} must be numeric, not boolean or null")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if (
        not math.isfinite(number)
        or (nonnegative and number < 0)
        or (integer and not number.is_integer())
    ):
        raise ValueError(f"{label} is not a valid finite numeric value")
    return number


@lru_cache(maxsize=16)
def _zone(name):
    try:
        return ZoneInfo(_text(name, "timezone_name"))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone_name must be an installed IANA timezone") from exc


def _physical(value, label):
    """Return exact UTC text, nanoseconds since epoch, and a datetime for dates."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an explicitly zoned RFC3339 timestamp")
    match = PHYSICAL_RE.fullmatch(value)
    if not match:
        raise ValueError(f"{label} must be an explicitly zoned RFC3339 timestamp")
    base, fraction, offset = match.groups()
    if offset != "Z" and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise ValueError(f"{label} has an invalid UTC offset")
    try:
        parsed = datetime.fromisoformat(base + ("+00:00" if offset == "Z" else offset)).astimezone(
            timezone.utc
        )
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} is not a valid physical timestamp") from exc
    # strftime %Y is not guaranteed four digits on every supported platform.
    prefix = f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}T{parsed.hour:02d}:{parsed.minute:02d}:{parsed.second:02d}"
    nanos = int((fraction or "").ljust(9, "0"))
    normalized_fraction = (fraction or "").rstrip("0")
    text = prefix + ("." + normalized_fraction if normalized_fraction else "") + "Z"
    ticks = calendar.timegm(parsed.utctimetuple()) * 1000000000 + nanos
    return {"text": text, "ticks": ticks, "dt": parsed.replace(microsecond=nanos // 1000)}


def _date(value, label):
    _keys(value, {"year", "month", "day"}, label)
    if any(type(value.get(field)) is not int for field in ("year", "month", "day")):
        raise ValueError(f"{label} requires integer year, month and day")
    try:
        return date(value["year"], value["month"], value["day"])
    except ValueError as exc:
        raise ValueError(f"{label} is not a calendar date") from exc


def _civil(value, label):
    _keys(value, {"date", "time"}, label)
    day = _date(value.get("date"), label + ".date")
    time = value.get("time", {})
    _keys(time, {"hours", "minutes", "seconds", "nanos"}, label + ".time")
    limits = {"hours": 23, "minutes": 59, "seconds": 59, "nanos": 999999999}
    for key, maximum in limits.items():
        field = time.get(key, 0)
        if type(field) is not int or not 0 <= field <= maximum:
            raise ValueError(f"{label}.{key} is invalid")
    at_midnight = all(time.get(key, 0) == 0 for key in limits)
    return day, at_midnight


def _offset(value, label):
    if not isinstance(value, str) or not DURATION_RE.fullmatch(value):
        raise ValueError(f"{label} must be a protobuf duration such as -18000s")
    seconds = _number(value[:-1], label, nonnegative=False, integer=True)
    if abs(seconds) >= 86400:
        raise ValueError(f"{label} must be less than 24 hours in magnitude")
    return timezone(timedelta(seconds=seconds))


def _local_time(physical, zone, civil=None, offset=None, end_exclusive=False):
    if civil is not None:
        day, midnight = _civil(civil, "civil time")
        if end_exclusive and midnight:
            day -= timedelta(days=1)
        return day.isoformat(), "source_civil_date"
    dt = physical["dt"]
    if end_exclusive:
        seconds, nanos = divmod(physical["ticks"] - 1, 1000000000)
        dt = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=nanos // 1000)
    effective_zone = _offset(offset, "UTC offset") if offset is not None else zone
    return dt.astimezone(
        effective_zone
    ).date().isoformat(), "source_utc_offset" if offset is not None else "configured_timezone"


def _sample_time(sample, zone):
    _keys(sample, {"physicalTime", "civilTime", "utcOffset"}, "sampleTime")
    physical = _physical(sample.get("physicalTime"), "sampleTime.physicalTime")
    day, basis = _local_time(physical, zone, sample.get("civilTime"), sample.get("utcOffset"))
    attrs = {
        "time_semantics": "source_physical_sample_time",
        "observed_at_is_measurement_time": True,
        "local_date_basis": basis,
    }
    if "civilTime" in sample:
        attrs["reported_local_date"] = day
    if "utcOffset" in sample:
        _offset(sample["utcOffset"], "sampleTime.utcOffset")
        attrs["source_utc_offset"] = sample["utcOffset"]
    return {
        "observed_at": physical["text"],
        "ended_at": None,
        "local_date": day,
        "logical_time": physical["text"],
        "attributes": attrs,
    }


def _interval_time(interval, zone):
    _keys(
        interval,
        {
            "startTime",
            "endTime",
            "startUtcOffset",
            "endUtcOffset",
            "civilStartTime",
            "civilEndTime",
        },
        "interval",
    )
    start, end = (
        _physical(interval.get("startTime"), "interval.startTime"),
        _physical(interval.get("endTime"), "interval.endTime"),
    )
    if end["ticks"] <= start["ticks"]:
        raise ValueError("interval.endTime must be after interval.startTime")
    start_day, basis = _local_time(
        start, zone, interval.get("civilStartTime"), interval.get("startUtcOffset")
    )
    end_day, _ = _local_time(end, zone, interval.get("civilEndTime"), interval.get("endUtcOffset"))
    covered_end_day, _ = _local_time(
        end, zone, interval.get("civilEndTime"), interval.get("endUtcOffset"), end_exclusive=True
    )
    attrs = {
        "time_semantics": "source_physical_interval",
        "observed_at_is_measurement_time": True,
        "interval_end_exclusive": True,
        "local_date_basis": basis,
        "start_local_date": start_day,
        "end_local_date": end_day,
        "coverage_end_local_date": covered_end_day,
        "crosses_local_midnight": start_day != covered_end_day,
    }
    if "civilStartTime" in interval:
        attrs["reported_local_date"] = start_day
    for key in ("startUtcOffset", "endUtcOffset"):
        if key in interval:
            _offset(interval[key], key)
            attrs[
                "source_start_utc_offset" if key == "startUtcOffset" else "source_end_utc_offset"
            ] = interval[key]
    return {
        "observed_at": start["text"],
        "ended_at": end["text"],
        "local_date": start_day,
        "logical_time": start["text"],
        "duration_seconds": (end["ticks"] - start["ticks"]) / 1000000000,
        "attributes": attrs,
    }


def _daily_time(value, zone, timezone_name):
    day = _date(value, "date")
    anchor = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    return {
        "observed_at": anchor.isoformat().replace("+00:00", "Z"),
        "ended_at": None,
        "local_date": day.isoformat(),
        "logical_time": "date:" + day.isoformat(),
        "attributes": {
            "time_semantics": "date_only_storage_anchor",
            "timestamp_is_storage_anchor": True,
            "observed_at_is_measurement_time": False,
            "reported_local_date": day.isoformat(),
            "local_date_basis": "reported_date",
            "anchor_timezone": timezone_name,
        },
    }


def _events(fields, payload, emit, **attributes):
    result = []
    for field, (metric, unit, scale) in fields.items():
        if field not in payload:
            continue
        signed_temperature = metric in ("skin_temp_nightly_c", "skin_temp_baseline_c")
        value = (
            _number(
                payload[field], field, nonnegative=not signed_temperature, integer=unit == "count"
            )
            * scale
        )
        if not math.isfinite(value):
            raise ValueError(f"{field} overflows its normalized unit")
        result.append(emit(metric, value, unit, attributes={"source_field": field, **attributes}))
    return result


def normalize_api_record(data_type, record, subject_id="demo", timezone_name="America/New_York"):
    """Normalize one API point; unknown shapes raise ValueError for quarantine."""
    subject_id = _text(subject_id, "subject_id")
    zone = _zone(timezone_name)
    data_type = _text(data_type, "data_type")
    if data_type not in PAYLOAD_KEYS:
        raise ValueError(f"Unsupported Google Health data type: {data_type}")
    payload_key = PAYLOAD_KEYS[data_type]
    _keys(record, {payload_key, "dataSource", "name"}, "API record")
    body = _object(record.get(payload_key), payload_key)
    source = record.get("dataSource")
    if not isinstance(source, (str, dict)) or not source:
        raise ValueError("API record requires an attributable dataSource")
    raw_hash = record_hash(record)
    stream = record_hash(source)
    named_id = record_hash(_text(record["name"], "name")) if "name" in record else None
    attrs = {"raw_record_hash": raw_hash, "data_type": data_type, "data_source_hash": stream}
    if named_id:
        attrs["source_record_id_hash"] = named_id
    for source_field, attribute in (
        ("updateTime", "source_update_time"),
        ("createTime", "source_create_time"),
    ):
        if source_field in body:
            attrs[attribute] = _physical(body[source_field], source_field)["text"]

    def emitter(temporal, kind, representation, label="", shared=None):
        shared_attrs = {
            **attrs,
            **temporal["attributes"],
            "representation": representation,
            **(shared or {}),
        }

        def emit(metric, value, unit, attributes=None, event_label=None):
            actual_label = label if event_label is None else event_label
            identity = {
                "source": "google_health",
                "subject_id": subject_id,
                "metric": metric,
                "label": actual_label,
            }
            if named_id:
                identity["named_record"] = named_id
            else:
                identity.update(
                    source_stream=stream,
                    logical_time=temporal["logical_time"],
                    ended_at=temporal["ended_at"],
                )
            return {
                "event_id": record_hash(identity),
                "subject_id": subject_id,
                "source": "google_health",
                "source_stream": stream,
                "metric": metric,
                "observed_at": temporal["observed_at"],
                "ended_at": temporal["ended_at"],
                "local_date": temporal["local_date"],
                "value": _number(value, metric, nonnegative=False),
                "unit": unit,
                "kind": kind,
                "attributes": {**shared_attrs, **(attributes or {})},
            }

        return emit

    if data_type in SAMPLE_FIELDS:
        fields = SAMPLE_FIELDS[data_type]
        _keys(body, set(fields) | {"sampleTime", "metadata"}, payload_key)
        shared = {}
        if "metadata" in body:
            shared["source_metadata"] = _object(body["metadata"], "metadata")
        return _events(
            fields,
            body,
            emitter(_sample_time(body.get("sampleTime"), zone), "sample", "sample", shared=shared),
        )

    if data_type in DAILY_FIELDS:
        fields = DAILY_FIELDS[data_type]
        _keys(body, set(fields) | {"date", "entropy", "dailyRestingHeartRateMetadata"}, payload_key)
        shared = {}
        if "entropy" in body:
            shared["reported_entropy"] = _number(body["entropy"], "entropy")
        if "dailyRestingHeartRateMetadata" in body:
            shared["source_metadata"] = _object(
                body["dailyRestingHeartRateMetadata"], "dailyRestingHeartRateMetadata"
            )
        return _events(
            fields,
            body,
            emitter(
                _daily_time(body.get("date"), zone, timezone_name),
                "daily",
                "daily_summary",
                shared=shared,
            ),
        )

    if data_type in INTERVAL_FIELDS:
        fields = INTERVAL_FIELDS[data_type]
        _keys(body, set(fields) | {"interval", "heartRateZone"}, payload_key)
        shared = {}
        label = ""
        if "heartRateZone" in body:
            label = _text(body["heartRateZone"], "heartRateZone")
            shared["heart_rate_zone"] = label
        return _events(
            fields,
            body,
            emitter(
                _interval_time(body.get("interval"), zone),
                "interval",
                "reported_interval",
                label,
                shared,
            ),
        )

    if data_type == "sleep":
        _keys(
            body,
            {
                "interval",
                "type",
                "stages",
                "shortAwakenings",
                "metadata",
                "summary",
                "createTime",
                "updateTime",
            },
            "sleep",
        )
        temporal = _interval_time(body.get("interval"), zone)
        metadata = _object(body.get("metadata", {}), "sleep.metadata")
        for key in ("nap", "mainSleep", "processed"):
            if key in metadata and type(metadata[key]) is not bool:
                raise ValueError(f"sleep.metadata.{key} must be boolean")
        shared = {
            "sleep_type": _text(body.get("type"), "sleep.type"),
            "source_metadata": metadata,
            "normalization_scope": "reported_session_summary",
            "stage_intervals_normalized": False,
        }
        for key in ("nap", "mainSleep"):
            if key in metadata:
                shared[key] = metadata[key]
        if "mainSleep" in metadata:
            shared["main_sleep"] = metadata["mainSleep"]
        for collection in ("stages", "shortAwakenings"):
            if collection not in body:
                continue
            if not isinstance(body[collection], list):
                raise ValueError(f"sleep.{collection} must be a list")
            for stage in body[collection]:
                _keys(
                    stage,
                    {
                        "startTime",
                        "endTime",
                        "startUtcOffset",
                        "endUtcOffset",
                        "civilStartTime",
                        "civilEndTime",
                        "type",
                        "createTime",
                        "updateTime",
                    },
                    "sleep stage",
                )
                stage_time = _interval_time(
                    {
                        key: value
                        for key, value in stage.items()
                        if key not in ("type", "createTime", "updateTime")
                    },
                    zone,
                )
                _text(stage.get("type"), "sleep stage type")
                for field in ("createTime", "updateTime"):
                    if field in stage:
                        _physical(stage[field], "sleep stage." + field)
                if (
                    _physical(stage_time["observed_at"], "stage.start")["ticks"]
                    < _physical(temporal["observed_at"], "sleep.start")["ticks"]
                    or _physical(stage_time["ended_at"], "stage.end")["ticks"]
                    > _physical(temporal["ended_at"], "sleep.end")["ticks"]
                ):
                    raise ValueError("Sleep stage lies outside its session")
            shared[collection + "_count"] = len(body[collection])
        summary = _object(body.get("summary", {}), "sleep.summary")
        _keys(summary, set(SLEEP_FIELDS) | {"stagesSummary"}, "sleep.summary")
        if "stagesSummary" in summary:
            if not isinstance(summary["stagesSummary"], list):
                raise ValueError("sleep.summary.stagesSummary must be a list")
            for stage in summary["stagesSummary"]:
                _keys(stage, {"type", "minutes", "count"}, "sleep stagesSummary")
                _text(stage.get("type"), "sleep stagesSummary.type")
                for field in ("minutes", "count"):
                    if field in stage:
                        _number(stage[field], "sleep stagesSummary." + field, integer=True)
            shared["reported_stages_summary"] = summary["stagesSummary"]
        return _events(
            SLEEP_FIELDS,
            summary,
            emitter(temporal, "session", "sleep_session_summary", shared=shared),
        )

    if data_type == "exercise":
        _keys(
            body,
            {
                "interval",
                "exerciseType",
                "metricsSummary",
                "exerciseMetadata",
                "exerciseEvents",
                "displayName",
                "activeDuration",
                "createTime",
                "updateTime",
            },
            "exercise",
        )
        temporal = _interval_time(body.get("interval"), zone)
        shared = {"exercise_type": _text(body.get("exerciseType"), "exerciseType")}
        if "exerciseMetadata" in body:
            shared["source_metadata"] = _object(body["exerciseMetadata"], "exerciseMetadata")
        if "exerciseEvents" in body:
            events = body["exerciseEvents"]
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise ValueError("exercise.exerciseEvents must be a list of objects")
            # Preserve newly observed event metadata without inventing numeric
            # measurements or recomputing Google's reported session summary.
            shared["source_exercise_events"] = events
        emit = emitter(temporal, "session", "exercise_summary", shared=shared)
        result = [
            emit(
                "exercise_duration_seconds",
                temporal["duration_seconds"],
                "s",
                attributes={"duration_basis": "physical_interval_elapsed"},
            )
        ]
        if "activeDuration" in body:
            if not isinstance(body["activeDuration"], str) or not DURATION_RE.fullmatch(
                body["activeDuration"]
            ):
                raise ValueError("exercise.activeDuration must be a protobuf duration")
            result.append(
                emit(
                    "exercise_active_duration_seconds",
                    _number(body["activeDuration"][:-1], "activeDuration"),
                    "s",
                    attributes={"duration_basis": "source_reported_active_duration"},
                )
            )
        summary = _object(body.get("metricsSummary", {}), "metricsSummary")
        _keys(summary, set(EXERCISE_FIELDS) | {"heartRateZoneDurations"}, "metricsSummary")
        result += _events(EXERCISE_FIELDS, summary, emit)
        if "heartRateZoneDurations" in summary:
            zones = summary["heartRateZoneDurations"]
            _keys(
                zones,
                {"lightTime", "moderateTime", "vigorousTime", "peakTime"},
                "heartRateZoneDurations",
            )
            for label, value in zones.items():
                if not isinstance(value, str) or not DURATION_RE.fullmatch(value):
                    raise ValueError(f"heartRateZoneDurations.{label} must be a protobuf duration")
                result.append(
                    emit(
                        "exercise_heart_rate_zone_seconds",
                        _number(value[:-1], label),
                        "s",
                        attributes={
                            "heart_rate_zone": label,
                            "source_field": "heartRateZoneDurations." + label,
                        },
                        event_label=label,
                    )
                )
        return result
    raise ValueError(f"Unsupported schema for {data_type}")


def normalize_ble_record(record, subject_id="demo", timezone_name="America/New_York"):
    """Normalize a verified standard HR notification using its Mac receipt time.

    A device_identifier may be supplied explicitly in the record. Legacy records
    omit it; those remain an explicitly unidentified BLE stream.
    """
    subject_id = _text(subject_id, "subject_id")
    zone = _zone(timezone_name)
    _keys(
        record,
        {
            "receipt_timestamp",
            "characteristic_uuid",
            "payload_hex",
            "bpm",
            "decode_error",
            "device_identifier",
        },
        "BLE record",
    )
    characteristic = _text(record.get("characteristic_uuid"), "characteristic_uuid").lower()
    if characteristic != HR_MEASUREMENT:
        raise ValueError("BLE record is not from Heart Rate Measurement 2A37")
    physical = _physical(record.get("receipt_timestamp"), "receipt_timestamp")
    if record.get("decode_error"):
        raise ValueError("BLE recorder reported a decode error")
    value = _number(record.get("bpm"), "bpm", integer=True)
    payload_hex = record.get("payload_hex")
    if not isinstance(payload_hex, str):
        raise ValueError("payload_hex must be text")
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError as exc:
        raise ValueError("payload_hex is malformed") from exc
    if not payload:
        raise ValueError("BLE payload is empty")
    if payload[0] & 0xE0:
        raise ValueError("BLE payload sets reserved Heart Rate Measurement flags")
    width = 2 if payload[0] & 1 else 1
    required = 1 + width + (2 if payload[0] & 8 else 0)
    if len(payload) < required or (
        payload[0] & 16 and (len(payload) - required < 2 or (len(payload) - required) % 2)
    ):
        raise ValueError("BLE measurement fields are truncated")
    if not payload[0] & 16 and len(payload) != required:
        raise ValueError("BLE measurement has unexplained trailing bytes")
    if int.from_bytes(payload[1 : 1 + width], "little") != value:
        raise ValueError("BLE payload BPM differs from recorded BPM")
    if "device_identifier" in record:
        device = _text(record["device_identifier"], "device_identifier")
        # macOS UUIDs and conventional addresses are case-insensitive identifiers.
        stream = record_hash(
            {"device_identifier": device.lower(), "characteristic_uuid": characteristic}
        )
        known = True
    else:
        stream = record_hash({"device_identifier": None, "characteristic_uuid": characteristic})
        known = False
    identity = {
        "source": "ble",
        "subject_id": subject_id,
        "source_stream": stream,
        "metric": "heart_rate_bpm",
        "logical_time": physical["text"],
        "ended_at": None,
        "label": "",
    }
    return [
        {
            "event_id": record_hash(identity),
            "subject_id": subject_id,
            "source": "ble",
            "source_stream": stream,
            "metric": "heart_rate_bpm",
            "observed_at": physical["text"],
            "ended_at": None,
            "local_date": physical["dt"].astimezone(zone).date().isoformat(),
            "value": value,
            "unit": "bpm",
            "kind": "sample",
            "attributes": {
                "raw_record_hash": record_hash(record),
                "representation": "sample",
                "time_semantics": "mac_receipt_time",
                "observed_at_is_measurement_time": False,
                "local_date_basis": "configured_timezone",
                "device_identity_available": known,
                "source_characteristic": characteristic,
                "payload_verified": True,
                "payload_flags": payload[0],
                "rr_intervals_used": False,
            },
        }
    ]
