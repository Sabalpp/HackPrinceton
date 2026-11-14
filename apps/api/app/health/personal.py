"""Pure adult, nonpregnant BP guidance and descriptive personal-history learning.

No database, messaging, diagnoses, medication changes, or emergency dispatch.
Clinical guidance and engineering learning rules are documented separately in
docs/health-evidence.md. A declared reference never changes safety guidance.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

EVIDENCE = {
    "severe_bp": "https://www.heart.org/en/health-topics/high-blood-pressure/understanding-blood-pressure-readings/when-to-call-911-for-high-blood-pressure",
    "home_bp": "https://www.heart.org/en/health-topics/high-blood-pressure/understanding-blood-pressure-readings/monitoring-your-blood-pressure-at-home",
    "nice": "https://www.nice.org.uk/guidance/ng136/chapter/recommendations",
    "low_bp": "https://www.heart.org/en/health-topics/high-blood-pressure/the-facts-about-high-blood-pressure/low-blood-pressure-when-blood-pressure-is-too-low",
    "stroke": "https://www.cdc.gov/stroke/signs-symptoms/",
    "heart_attack": "https://www.heart.org/health-topics/heart-attack/warning-signs-of-a-heart-attack",
    "device": "https://www.fda.gov/medical-devices/safety-communications/do-not-use-unauthorized-devices-measuring-blood-pressure-fda-safety-communication",
}
SYMPTOMS = frozenset(
    {
        "chest_pain",
        "shortness_of_breath",
        "new_weakness",
        "new_numbness",
        "vision_change",
        "difficulty_speaking",
        "sudden_severe_headache",
    }
)
METRICS = frozenset({"steps", "resting_heart_rate", "sleep_minutes", "systolic", "diastolic"})
BP_METRICS = frozenset({"systolic", "diastolic"})


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return result


def _instant(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("A valid measurement timestamp is required") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Measurement timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _flag(record: Mapping[str, Any], name: str, *, nullable: bool = False) -> bool | None:
    value = record.get(name, None if nullable else False)
    if nullable and value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{name} must be boolean")
    return value


def _symptoms(record: Mapping[str, Any]) -> set[str]:
    value = record.get("symptoms", [])
    if not isinstance(value, (list, tuple, set, frozenset)) or any(
        not isinstance(item, str) or item not in SYMPTOMS for item in value
    ):
        raise ValueError("Symptoms must use the documented acute-symptom codes")
    return set(value)


def _bp_pair(record: Mapping[str, Any]) -> tuple[float, float]:
    systolic = _number(record.get("systolic"), "systolic")
    diastolic = _number(record.get("diastolic"), "diastolic")
    if diastolic <= 0 or systolic <= diastolic:
        raise ValueError("BP requires positive diastolic and systolic greater than diastolic")
    return systolic, diastolic


def _severe(systolic: float, diastolic: float) -> bool:
    # Equality is a conservative app boundary; see the AHA/NICE distinction in docs.
    return systolic >= 180 or diastolic >= 120


def assess_bp(
    reading: Mapping[str, Any],
    *,
    previous_reading: Mapping[str, Any] | None = None,
    reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return guidance for a reported cuff reading, never a diagnosis.

    `symptoms` means new/current concerning symptoms, not historical diagnoses.
    Adult/nonpregnant scope must be explicitly confirmed. The caller must enforce
    same-person authorization for previous_reading. No reading/reference is changed. Unknown symptom status
    never means absent symptoms. `reference` is intentionally not used in rules.
    """
    if not isinstance(reading, Mapping):
        raise ValueError("reading must be a mapping")
    symptoms = _symptoms(reading)
    checked = _flag(reading, "symptoms_checked")
    mode = reading.get("data_mode")
    if mode not in ("live", "simulated"):
        raise ValueError("data_mode must be live or simulated")
    validated = _flag(reading, "device_validated", nullable=True)
    adult_nonpregnant = _flag(reading, "adult_nonpregnant")
    acute = _flag(reading, "acute")
    verified = reading.get("source") == "manual_cuff" and validated is True

    def result(level, code, message, *, eligible=False, repeat=False, evidence=()):
        title, action = {
            "emergency": ("Urgent symptoms", "contact_local_emergency_services"),
            "contact_clinician": ("Repeat BP remains very high", "contact_clinician_promptly"),
            "repeat": ("BP needs a recheck", "check_symptoms_then_repeat_after_one_minute"),
            "review": ("Reading needs context", "review_symptoms_and_care_plan"),
            "recorded": ("Reading recorded", "follow_clinician_agreed_plan"),
            "unverified": ("Measurement not verified", "confirm_measurement_source"),
            "scope_unconfirmed": ("BP guidance unavailable", "confirm_adult_nonpregnant_scope"),
        }[level]
        return {
            "level": level,
            "title": title,
            "action": action,
            "source": reading.get("source", "unknown"),
            "override_allowed": False,
            "emergency": level == "emergency",
            "message": message,
            "reason_codes": [code],
            "data_mode": mode,
            "validated_cuff_attested": verified,
            "adult_nonpregnant_confirmed": adult_nonpregnant,
            "baseline_eligible": eligible,
            "repeat_confirmed": repeat,
            "reference_present": reference is not None,
            "reference_used_for_safety": False,
            "evidence_urls": [EVIDENCE[key] for key in evidence],
        }

    if mode == "simulated":
        return result(
            "unverified",
            "simulated_input",
            "Simulated BP cannot establish a real safety assessment or baseline.",
        )
    # Symptoms take priority even if the BP is missing, unreliable, or not elevated.
    if symptoms:
        return result(
            "emergency",
            "acute_warning_symptoms",
            "These new symptoms need urgent assessment. Contact local emergency services now. "
            "Do not wait for another BP reading or for the app to contact someone.",
            evidence=("heart_attack", "stroke", "severe_bp"),
        )
    systolic, diastolic = _bp_pair(reading)
    measured = _instant(reading.get("measured_at"))
    if not adult_nonpregnant:
        return result(
            "scope_unconfirmed",
            "adult_nonpregnant_scope_unconfirmed",
            "This adult BP guidance is unavailable without confirmation that the person is an "
            "adult and is not pregnant. Ask the appropriate clinician about these readings. "
            "New concerning symptoms require local emergency help now.",
            evidence=("home_bp",),
        )
    if reading.get("source") != "manual_cuff":
        return result(
            "unverified",
            "unverified_measurement_source",
            "This is not a confirmed reading from a validated cuff. Confirm with a validated "
            "upper-arm cuff. If a repeat remains at least 180 systolic or 120 diastolic, contact "
            "a clinician promptly. New concerning symptoms require local emergency help now.",
            evidence=("device", "home_bp", "severe_bp"),
        )
    repeated = False
    if previous_reading is not None:
        try:
            previous_s, previous_d = _bp_pair(previous_reading)
            seconds = (measured - _instant(previous_reading.get("measured_at"))).total_seconds()
            repeated = (
                60 <= seconds <= 3600
                and _severe(previous_s, previous_d)
                and previous_reading.get("source") == "manual_cuff"
                and previous_reading.get("adult_nonpregnant") is True
                and previous_reading.get("data_mode") == "live"
                and previous_reading.get("parent_id") == reading.get("parent_id")
                and previous_reading.get("source_stream") == reading.get("source_stream")
            )
        except (ValueError, TypeError, AttributeError):
            repeated = False
    if _severe(systolic, diastolic):
        if repeated:
            return result(
                "contact_clinician",
                "repeated_severe_bp",
                "The repeat BP remains very high. Contact a clinician promptly. If any new "
                "concerning symptoms are present, contact local emergency services now.",
                repeat=True,
                evidence=("severe_bp", "nice"),
            )
        return result(
            "repeat",
            "severe_bp_needs_repeat" if checked else "severe_bp_symptoms_not_checked",
            "Check for new concerning symptoms now; if present, contact local emergency services. "
            "If there are no symptoms, wait at least 1 minute and recheck BP. If it remains this "
            "high, contact a clinician promptly. A stated usual value does not change this guidance.",
            evidence=("severe_bp", "nice"),
        )
    if not verified:
        return result(
            "unverified",
            "cuff_validation_not_attested",
            "Reading recorded, but use of an independently validated upper-arm cuff has not "
            "been confirmed. Confirm the model and cuff fit with a clinician or trusted "
            "validation list. This reading cannot establish an observed baseline.",
            evidence=("home_bp", "device"),
        )
    if not checked or acute:
        return result(
            "review",
            "symptoms_not_checked" if not checked else "acute_period",
            "The reading is recorded, but symptom or acute-period context is incomplete. "
            "Check symptoms and follow the person's care plan before interpreting it as usual.",
            evidence=("home_bp",),
        )
    if systolic < 90 or diastolic < 60:
        return result(
            "review",
            "low_bp_reading",
            "This reading is below a commonly used low-BP boundary. Discuss repeated low readings "
            "or symptoms with a clinician; a personal reference does not establish that it is safe.",
            eligible=True,
            evidence=("low_bp",),
        )
    return result(
        "recorded",
        "reading_recorded",
        "Reading recorded. One reading does not establish a diagnosis or a safe personal range. "
        "Follow the person's clinician-agreed plan.",
        eligible=True,
        evidence=("home_bp",),
    )


def _sample(record: Mapping[str, Any], metric: str, zone: ZoneInfo) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("sample must be a mapping")
    instant = _instant(record.get("observed_at"))
    value = _number(record.get("value"), "value")
    if metric in ("resting_heart_rate", "systolic", "diastolic") and value == 0:
        raise ValueError("This metric must be positive")
    if metric == "steps" and not value.is_integer():
        raise ValueError("Daily steps must be an integer")
    if metric == "sleep_minutes" and value > 1440:
        raise ValueError("Daily sleep minutes cannot exceed a day")
    mode = record.get("data_mode")
    source, stream = record.get("source"), record.get("source_stream")
    if mode not in ("live", "simulated") or not all(
        isinstance(v, str) and v for v in (source, stream)
    ):
        raise ValueError("Explicit source, source_stream, and data_mode are required")
    symptoms = _symptoms(record)
    acute = _flag(record, "acute") or bool(symptoms)
    day = instant.astimezone(zone).date()
    if "local_date" in record:
        if metric not in BP_METRICS and record.get("kind") != "daily":
            raise ValueError(
                "A source local_date is only authoritative for a declared daily summary"
            )
        raw_day = record["local_date"]
        source_day = date.fromisoformat(raw_day) if isinstance(raw_day, str) else raw_day
        if type(source_day) is not date or (metric in BP_METRICS and source_day != day):
            raise ValueError(
                "BP local_date must match its physical timestamp in the selected timezone"
            )
        day = source_day
    if metric in BP_METRICS:
        pair = _bp_pair(record)
        if value != pair[0 if metric == "systolic" else 1]:
            raise ValueError("BP sample value does not match its paired reading")
        guidance = assess_bp({**record, "measured_at": instant})
        eligible = guidance["baseline_eligible"]
        acute = acute or _severe(*pair)
        fingerprint = pair
    else:
        eligible = source in ("fitbit", "google_health") and not acute and mode == "live"
        fingerprint = (value,)
    return {
        "instant": instant,
        "day": day,
        "value": value,
        "identity": (source, stream, mode),
        "eligible": eligible and mode == "live" and not acute,
        "acute": acute,
        "fingerprint": fingerprint,
    }


def _declared(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("declared_reference must contain an explicit value")
    return {
        "value": _number(value.get("value"), "declared reference"),
        "status": "provisional",
        "label": "User-declared reference; not medically verified",
        "used_for_safety": False,
    }


def build_baseline(
    records: Iterable[Mapping[str, Any]],
    metric: str,
    *,
    now: datetime,
    timezone_name: str,
    declared_reference: Mapping[str, Any] | None = None,
    current: Mapping[str, Any] | None = None,
    unstable_period: bool = False,
) -> dict[str, Any]:
    """Learn from seven qualified completed dates in the preceding 28 local dates.

    Inputs are generic scalar samples. BP samples additionally carry the original
    systolic/diastolic pair, cuff-validation and symptom fields. Daily Fitbit
    records should include their authoritative local_date (UTC midnight is often
    only a storage anchor). The seven-day/two-reading rule is engineering coverage,
    not the NICE diagnostic HBPM protocol. No missing value is imputed.
    """
    if metric not in METRICS:
        raise ValueError(
            "Unsupported personal metric; instantaneous HR cannot establish resting HR or HRV"
        )
    if type(unstable_period) is not bool:
        raise ValueError("unstable_period must be boolean")
    clock, zone = _instant(now), ZoneInfo(timezone_name)
    end = clock.astimezone(zone).date() - timedelta(days=1)
    start = end - timedelta(days=27)
    required_per_day = 2 if metric in BP_METRICS else 1
    excluded: Counter[str] = Counter()
    samples = []
    for record in records:
        try:
            sample = _sample(record, metric, zone)
        except (ValueError, TypeError, KeyError, OverflowError):
            excluded["invalid"] += 1
            continue
        if not start <= sample["day"] <= end or sample["instant"] > clock:
            excluded["outside_completed_window"] += 1
            continue
        samples.append(sample)
    identities = {sample["identity"] for sample in samples}
    acute_days = {sample["day"] for sample in samples if sample["acute"]}
    by_instant = defaultdict(list)
    for sample in samples:
        if sample["day"] in acute_days:
            excluded["acute_day"] += 1
        elif not sample["eligible"]:
            excluded["unverified_or_ineligible"] += 1
        else:
            by_instant[sample["instant"]].append(sample)
    by_day = defaultdict(list)
    for coincident in by_instant.values():
        if len({sample["fingerprint"] for sample in coincident}) != 1:
            excluded["conflicting_timestamp"] += len(coincident)
            continue
        excluded["duplicate"] += len(coincident) - 1
        by_day[coincident[0]["day"]].append(coincident[0])
    daily = {}
    eligible_readings = 0
    for day, values in by_day.items():
        values.sort(key=lambda sample: sample["instant"])
        if metric not in BP_METRICS:
            if len({sample["value"] for sample in values}) != 1:
                excluded["conflicting_daily_value"] += len(values)
                continue
            selected = values[:1]
            excluded["duplicate_daily_value"] += len(values) - 1
        else:
            selected = []
            for sample in values:
                if (
                    not selected
                    or (sample["instant"] - selected[-1]["instant"]).total_seconds() >= 60
                ):
                    selected.append(sample)
                else:
                    excluded["too_close_in_time"] += 1
        if len(selected) < required_per_day:
            excluded["insufficient_daily_coverage"] += len(selected)
            continue
        daily[day] = float(median(sample["value"] for sample in selected))
        eligible_readings += len(selected)
    identity = next(iter(identities)) if len(identities) == 1 else (None, None, None)
    result = {
        "metric": metric,
        "status": "learning",
        "eligible_days": len(daily),
        "required_days": 7,
        "window_days": 28,
        "min_readings_per_day": required_per_day,
        "eligible_readings": eligible_readings,
        "median": None,
        "mad": None,
        "declared_reference": _declared(declared_reference),
        "explanation": f"Learning: {len(daily)} of 7 qualified days. This is descriptive app learning, not clinical calibration.",
        "excluded_counts": dict(excluded),
        "source": identity[0],
        "source_stream": identity[1],
        "data_mode": identity[2],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "comparison": None,
    }
    if unstable_period:
        result.update(
            status="paused",
            explanation="Learning and comparison are paused during the reported unstable or acute period.",
        )
    elif len(identities) > 1:
        result.update(
            status="insufficient_data",
            explanation="Different sources, devices, or live/simulated modes cannot form one baseline.",
        )
    elif identity[2] == "simulated":
        result.update(
            status="insufficient_data",
            explanation="Simulated readings cannot establish an observed personal baseline.",
        )
    elif len(daily) >= 7:
        center = float(median(daily.values()))
        spread = float(median(abs(value - center) for value in daily.values()))
        result.update(
            status="observed",
            median=center,
            mad=spread,
            explanation="Observed median and variation from qualified days; this is not a safe range or a diagnosis.",
        )
    if current is not None:
        result["comparison"] = _comparison(current, metric, zone, clock, result)
    return result


def _comparison(current, metric, zone, clock, baseline):
    unavailable = {
        "status": "unavailable",
        "basis": None,
        "delta": None,
        "band_low": None,
        "band_high": None,
        "message": "No reliable personal comparison is available for this reading.",
    }
    try:
        sample = _sample(current, metric, zone)
    except (ValueError, TypeError, KeyError, OverflowError):
        return unavailable
    if (
        baseline["status"] in ("paused", "insufficient_data")
        or not sample["eligible"]
        or sample["acute"]
        or sample["instant"] > clock
        or sample["day"].isoformat() <= baseline["window_end"]
        or sample["day"] > clock.astimezone(zone).date()
    ):
        return unavailable
    identity = (baseline["source"], baseline["source_stream"], baseline["data_mode"])
    if identity[0] is not None and sample["identity"] != identity:
        return unavailable
    if baseline["status"] == "observed":
        if baseline["mad"] == 0:
            return {
                **unavailable,
                "message": "Observed variation is zero; no reliable deviation band can be estimated.",
            }
        center, distance = baseline["median"], 3 * baseline["mad"]
        delta = sample["value"] - center
        return {
            "status": "outside_observed_band" if abs(delta) > distance else "within_observed_band",
            "basis": "observed_median_3_mad_engineering_rule",
            "delta": delta,
            "band_low": max(0.0, center - distance),
            "band_high": center + distance,
            "message": "Comparison uses a descriptive three-MAD engineering band, not a medical threshold or assurance of safety.",
        }
    reference = baseline["declared_reference"]
    if reference is not None:
        return {
            "status": "provisional_difference",
            "basis": "user_declared_reference",
            "delta": sample["value"] - reference["value"],
            "band_low": None,
            "band_high": None,
            "message": "Difference from the person's stated reference only; observed learning is incomplete and safety guidance is unchanged.",
        }
    return unavailable
