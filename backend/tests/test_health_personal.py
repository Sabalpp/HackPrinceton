"""Safety guidance and honest learning coverage, using synthetic inputs only."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.health.personal import SYMPTOMS, assess_bp, build_baseline
from app.schemas.experience import BPAssessment, PersonalMetric

NOW = datetime(2026, 11, 14, 20, tzinfo=UTC)


def bp(**changes):
    return {
        "systolic": 120,
        "diastolic": 80,
        "measured_at": NOW,
        "source": "manual_cuff",
        "source_stream": "fixture_cuff",
        "parent_id": "fixture_parent",
        "data_mode": "live",
        "device_validated": True,
        "adult_nonpregnant": True,
        "symptoms_checked": True,
        "symptoms": [],
        **changes,
    }


def wearable(age, value=4000, **changes):
    instant = NOW - timedelta(days=age)
    return {
        "value": value,
        "observed_at": instant,
        "local_date": instant.date().isoformat(),
        "source": "fitbit",
        "source_stream": "fixture_fitbit",
        "data_mode": "live",
        "kind": "daily",
        **changes,
    }


def bp_sample(age, value=120, *, offset=0, metric="systolic", **changes):
    instant = NOW - timedelta(days=age) + timedelta(seconds=offset)
    record = bp(measured_at=instant, **changes)
    record[metric] = value
    return {**record, "value": value, "observed_at": instant}


def bp_history(days=7, metric="systolic"):
    value = 120 if metric == "systolic" else 80
    return [
        bp_sample(age, value, offset=offset, metric=metric)
        for age in range(1, days + 1)
        for offset in (0, 60)
    ]


def learn(records, metric="steps", **kwargs):
    return build_baseline(records, metric, now=NOW, timezone_name="America/New_York", **kwargs)


@pytest.mark.parametrize("systolic,diastolic", [(180, 80), (140, 120), (181, 121), (250, 150)])
def test_either_severe_component_including_boundary_requires_action(systolic, diastolic):
    result = assess_bp(bp(systolic=systolic, diastolic=diastolic))
    assert result["level"] == "repeat"
    assert result["baseline_eligible"] is False
    assert "1 minute" in result["message"]
    assert result["override_allowed"] is False


def test_below_severe_boundary_is_recorded_without_claiming_normal_or_safe():
    result = assess_bp(bp(systolic=179, diastolic=119))
    assert result["level"] == "recorded"
    assert "does not establish" in result["message"]


@pytest.mark.parametrize("symptom", sorted(SYMPTOMS))
def test_new_warning_symptoms_take_priority_even_without_trustworthy_bp_or_adult_scope(symptom):
    record = bp(
        systolic=None,
        diastolic=None,
        source="unknown_device",
        device_validated=False,
        measured_at=None,
        adult_nonpregnant=False,
        symptoms=[symptom],
        symptoms_checked=False,
    )
    result = assess_bp(record, reference={"value": 200})
    assert result["emergency"] is True
    assert result["action"] == "contact_local_emergency_services"
    assert "Do not wait" in result["message"]
    assert result["baseline_eligible"] is False


@pytest.mark.parametrize("scope", [False, None])
def test_unconfirmed_adult_nonpregnant_scope_never_produces_adult_numeric_assessment(scope):
    record = bp(systolic=190, diastolic=125)
    if scope is None:
        del record["adult_nonpregnant"]
    else:
        record["adult_nonpregnant"] = scope
    result = assess_bp(record)
    assert result["level"] == "scope_unconfirmed"
    assert result["baseline_eligible"] is False


def test_personal_reference_cannot_suppress_severe_guidance_and_inputs_are_not_mutated():
    record = bp(systolic=190, diastolic=125)
    original = deepcopy(record)
    unreferenced = assess_bp(record)
    declared = assess_bp(record, reference={"value": 200, "override": True})
    assert declared["message"] == unreferenced["message"]
    assert declared["level"] == unreferenced["level"] == "repeat"
    assert declared["reference_used_for_safety"] is False
    assert declared["override_allowed"] is False
    assert record == original


@pytest.mark.parametrize(
    "seconds,repeated", [(59, False), (60, True), (3600, True), (3601, False), (-60, False)]
)
def test_repeat_requires_correct_time_order_and_at_least_one_minute(seconds, repeated):
    record = bp(systolic=185)
    previous = bp(systolic=190, measured_at=NOW - timedelta(seconds=seconds))
    result = assess_bp(record, previous_reading=previous)
    assert result["repeat_confirmed"] is repeated
    assert result["level"] == ("contact_clinician" if repeated else "repeat")


@pytest.mark.parametrize(
    "changes",
    [
        {"data_mode": "simulated"},
        {"source": "fitbit"},
        {"parent_id": "someone_else"},
        {"source_stream": "another_cuff"},
        {"adult_nonpregnant": False},
        {"systolic": 120},
        {"measured_at": "2026-11-14T19:58:00"},
    ],
)
def test_incompatible_previous_reading_does_not_confirm_repeat(changes):
    previous = bp(systolic=190, measured_at=NOW - timedelta(minutes=2))
    previous.update(changes)
    result = assess_bp(bp(systolic=185), previous_reading=previous)
    assert result["repeat_confirmed"] is False


def test_unvalidated_cuff_cannot_dismiss_severe_numeric_reading_or_qualify_learning():
    result = assess_bp(bp(systolic=185, device_validated=False))
    assert result["level"] == "repeat"
    assert result["validated_cuff_attested"] is False
    assert result["baseline_eligible"] is False
    assert assess_bp(bp(device_validated=None))["level"] == "unverified"


def test_simulation_never_creates_real_emergency_or_observed_baseline():
    result = assess_bp(bp(systolic=190, symptoms=["chest_pain"], data_mode="simulated"))
    assert result["reason_codes"] == ["simulated_input"]
    assert result["emergency"] is False and result["baseline_eligible"] is False


def test_unchecked_symptoms_are_not_assumed_absent():
    result = assess_bp(bp(symptoms_checked=False))
    assert result["reason_codes"] == ["symptoms_not_checked"]
    assert result["baseline_eligible"] is False
    severe = assess_bp(bp(systolic=185, symptoms_checked=False))
    assert severe["reason_codes"] == ["severe_bp_symptoms_not_checked"]
    assert "Check for new" in severe["message"]


def test_low_reading_has_cautious_review_not_automatic_emergency_or_safe_label():
    result = assess_bp(bp(systolic=88, diastolic=58), reference={"value": 88})
    assert result["level"] == "review"
    assert result["emergency"] is False
    assert "does not establish" in result["message"]


@pytest.mark.parametrize(
    "changes",
    [
        {"systolic": True},
        {"diastolic": float("nan")},
        {"systolic": float("inf")},
        {"systolic": "120"},
        {"systolic": 70, "diastolic": 80},
        {"diastolic": 0},
        {"measured_at": "2026-11-14T12:00:00"},
        {"device_validated": "yes"},
        {"symptoms_checked": 1},
        {"adult_nonpregnant": "true"},
        {"data_mode": "mixed"},
        {"symptoms": ["unknown_symptom"]},
        {"symptoms": "chest_pain"},
    ],
)
def test_invalid_bp_inputs_fail_explicitly(changes):
    with pytest.raises(ValueError):
        assess_bp(bp(**changes))


def test_assessment_satisfies_shared_dto_without_calling_network_or_storage():
    model = BPAssessment.model_validate(assess_bp(bp()))
    assert model.title and model.action and model.override_allowed is False


def test_learning_reports_missing_dates_without_inventing_a_default_reference():
    result = learn([])
    assert result["eligible_days"] == result["eligible_readings"] == 0
    assert result["status"] == "learning"
    assert result["median"] is None and result["mad"] is None
    assert result["declared_reference"] is None
    assert result["window_start"] == "2026-10-17"
    assert result["window_end"] == "2026-11-13"


def test_seven_distinct_completed_dates_learn_and_six_keep_learning():
    records = [wearable(age, age * 1000) for age in range(1, 8)]
    assert learn(records[:-1])["status"] == "learning"
    result = learn(records)
    assert result["status"] == "observed"
    assert result["eligible_days"] == result["eligible_readings"] == 7
    assert result["median"] == 4000 and result["mad"] == 2000


@pytest.mark.parametrize("metric", ["systolic", "diastolic"])
def test_bp_requires_at_least_two_qualified_readings_per_completed_date(metric):
    result = learn(bp_history(metric=metric), metric)
    assert result["status"] == "observed"
    assert result["eligible_days"] == 7 and result["eligible_readings"] == 14
    assert result["min_readings_per_day"] == 2
    one_per_day = bp_history(metric=metric)[::2]
    incomplete = learn(one_per_day, metric)
    assert incomplete["eligible_days"] == 0
    assert incomplete["excluded_counts"]["insufficient_daily_coverage"] == 7


def test_duplicates_and_near_simultaneous_bp_do_not_inflate_coverage():
    records = [bp_sample(1), bp_sample(1), bp_sample(1, offset=59)]
    result = learn(records, "systolic")
    assert result["eligible_days"] == 0
    assert result["excluded_counts"]["duplicate"] == 1
    assert result["excluded_counts"]["too_close_in_time"] == 1


def test_same_timestamp_conflicts_and_same_day_daily_conflicts_are_excluded():
    conflicting_bp = [bp_sample(1), bp_sample(1, 130), bp_sample(1, offset=60)]
    result = learn(conflicting_bp, "systolic")
    assert result["eligible_days"] == 0
    assert result["excluded_counts"]["conflicting_timestamp"] == 2
    conflicting_daily = [
        wearable(1, 4000),
        wearable(1, 5000, observed_at=NOW - timedelta(days=1, hours=1)),
    ]
    result = learn(conflicting_daily)
    assert result["eligible_days"] == 0
    assert result["excluded_counts"]["conflicting_daily_value"] == 2


def test_repeated_identical_daily_values_count_once_and_equal_weight_per_day():
    records = [wearable(age, age * 1000) for age in range(1, 8)]
    repeated = records + [
        wearable(1, 1000, observed_at=NOW - timedelta(days=1, seconds=index + 1))
        for index in range(50)
    ]
    result = learn(repeated)
    assert result["eligible_days"] == result["eligible_readings"] == 7
    assert result["median"] == 4000


@pytest.mark.parametrize(
    "changes",
    [
        {"device_validated": False},
        {"device_validated": None},
        {"adult_nonpregnant": False},
        {"symptoms_checked": False},
    ],
)
def test_bp_uncertain_context_never_enters_baseline(changes):
    result = learn([bp_sample(1, **changes), bp_sample(1, offset=60, **changes)], "systolic")
    assert result["eligible_days"] == 0
    assert result["excluded_counts"]["unverified_or_ineligible"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"acute": True},
        {"symptoms": ["chest_pain"]},
        {"systolic": 185, "value": 185},
    ],
)
def test_one_acute_reading_excludes_the_entire_day(changes):
    records = bp_history()
    records.append({**bp_sample(1, offset=120), **changes})
    result = learn(records, "systolic")
    assert result["eligible_days"] == 6
    assert result["excluded_counts"]["acute_day"] == 3
    assert result["status"] == "learning"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_stream": "different_device"},
        {"source": "other_provider"},
        {"data_mode": "simulated"},
    ],
)
def test_mixed_provenance_blocks_comparison_even_with_seven_real_days(changes):
    records = [wearable(age) for age in range(1, 8)] + [wearable(8, **changes)]
    result = learn(records, current=wearable(0), declared_reference={"value": 4000})
    assert result["status"] == "insufficient_data"
    assert result["median"] is None
    assert result["comparison"]["status"] == "unavailable"


def test_pure_simulated_days_and_instantaneous_ble_hr_cannot_be_learned():
    result = learn([wearable(age, data_mode="simulated") for age in range(1, 8)])
    assert result["status"] == "insufficient_data" and result["eligible_days"] == 0
    ble = learn(
        [wearable(age, value=70, source="ble") for age in range(1, 8)], "resting_heart_rate"
    )
    assert ble["eligible_days"] == 0
    with pytest.raises(ValueError, match="Unsupported"):
        learn([], "hrv")


def test_today_future_and_old_dates_do_not_fill_missing_completed_days():
    records = [wearable(age) for age in (0, -1, 29)] + [wearable(1), wearable(1)]
    result = learn(records)
    assert result["eligible_days"] == 1
    assert result["excluded_counts"]["outside_completed_window"] == 3


def test_explicit_daily_date_preserves_source_day_despite_utc_midnight_anchor():
    records = [
        wearable(age, source="google_health", observed_at=f"2026-11-{14 - age:02d}T00:00:00Z")
        for age in range(1, 8)
    ]
    result = learn(records)
    assert result["eligible_days"] == 7
    assert result["window_end"] == "2026-11-13"
    # A physical BP measurement cannot carry a conflicting civil date.
    bad_bp = bp_sample(1, local_date="2026-11-12")
    invalid = learn([bad_bp], "systolic")
    assert invalid["excluded_counts"]["invalid"] == 1
    not_daily = learn([wearable(1, kind="sample")])
    assert not_daily["excluded_counts"]["invalid"] == 1


def test_completed_date_cutoff_uses_selected_timezone_instead_of_utc_date():
    clock = datetime(2026, 11, 14, 1, tzinfo=UTC)  # November 13 evening in New York.
    result = build_baseline([wearable(1)], "steps", now=clock, timezone_name="America/New_York")
    assert result["window_end"] == "2026-11-12"
    assert result["eligible_days"] == 0


def test_invalid_missing_values_are_excluded_and_real_zero_steps_is_kept():
    records = [
        wearable(1, 0),
        wearable(2, None),
        wearable(3, True),
        wearable(4, float("nan")),
        wearable(5, -1),
        wearable(6, 1.5),
        wearable(7, 3000, observed_at="2026-11-07T12:00:00"),
    ]
    result = learn(records)
    assert result["eligible_days"] == 1
    assert result["excluded_counts"]["invalid"] == 6


def test_bp_value_must_match_original_pair_and_missing_pair_is_not_reconstructed():
    records = [{**bp_sample(1), "value": 121}, wearable(1, value=120, source="manual_cuff")]
    result = learn(records, "systolic")
    assert result["excluded_counts"]["invalid"] == 2


def test_observed_band_is_raw_mad_engineering_rule_and_exact_boundary_is_included():
    records = [
        wearable(age, value)
        for age, value in enumerate([3000, 3500, 4000, 4500, 5000, 5500, 6000], start=1)
    ]
    inside = learn(records, current=wearable(0, 7500))
    assert inside["median"] == 4500 and inside["mad"] == 1000
    assert inside["comparison"]["status"] == "within_observed_band"
    outside = learn(records, current=wearable(0, 7501))
    assert outside["comparison"]["status"] == "outside_observed_band"
    assert outside["comparison"]["band_low"] == 1500
    assert "not a medical threshold" in outside["comparison"]["message"]


def test_zero_mad_does_not_invent_precision_or_raise_every_change_as_anomaly():
    result = learn([wearable(age) for age in range(1, 8)], current=wearable(0, 4001))
    assert result["status"] == "observed" and result["mad"] == 0
    assert result["comparison"]["status"] == "unavailable"
    assert "zero" in result["comparison"]["message"]


@pytest.mark.parametrize(
    "current",
    [
        wearable(1),
        wearable(0, source_stream="different"),
        wearable(0, acute=True),
        wearable(-1),
        wearable(0, local_date="2026-11-15"),
        wearable(0, data_mode="simulated"),
    ],
)
def test_current_comparison_rejects_training_points_mixed_devices_acute_and_future(current):
    records = [wearable(age, age * 1000) for age in range(1, 8)]
    assert learn(records, current=current)["comparison"]["status"] == "unavailable"


def test_provisional_reference_is_only_a_difference_while_learning():
    result = learn([wearable(1)], declared_reference={"value": 5000}, current=wearable(0, 4500))
    assert result["median"] is None
    assert result["declared_reference"]["status"] == "provisional"
    assert result["comparison"]["status"] == "provisional_difference"
    assert result["comparison"]["delta"] == -500
    assert result["comparison"]["band_low"] is None


def test_unstable_period_pauses_observed_and_provisional_comparisons():
    result = learn(
        [wearable(age) for age in range(1, 8)],
        current=wearable(0),
        declared_reference={"value": 4000},
        unstable_period=True,
    )
    assert result["status"] == "paused" and result["median"] is None
    assert result["comparison"]["status"] == "unavailable"


def test_severe_bp_cannot_compare_against_a_declared_reference_during_calibration():
    result = learn([], "systolic", declared_reference={"value": 185}, current=bp_sample(0, 185))
    assert result["comparison"]["status"] == "unavailable"


def test_dto_mapping_keeps_user_reference_provisional_and_learning_counts_exact():
    result = learn([wearable(1)], declared_reference={"value": 4000})
    model = PersonalMetric.model_validate(
        {**result, "declared_reference": result["declared_reference"]["value"]}
    )
    assert (
        model.eligible_days == 1 and model.required_days == 7 and model.declared_reference == 4000
    )


def test_naive_clock_and_bad_timezone_fail_instead_of_guessing_local_dates():
    with pytest.raises(ValueError):
        build_baseline([], "steps", now=datetime(2026, 11, 14), timezone_name="UTC")
    with pytest.raises(KeyError):
        build_baseline([], "steps", now=NOW, timezone_name="No/Such_Zone")
