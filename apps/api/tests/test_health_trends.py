"""Calendar windows and provenance guards for daily personal-baseline trends."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.health.trends import compute_trends, metric_trend
from app.schemas.contracts import DataMode, HealthObservation

END = datetime(2026, 11, 14, tzinfo=UTC)


def observation(age: int, **changes) -> HealthObservation:
    fields = {
        "observation_id": f"trend_fixture_{age}",
        "parent_id": "parent_fixture",
        "source": "fitbit",
        "data_mode": DataMode.live,
        "timestamp": END - timedelta(days=age),
        "steps": 2000 if age < 5 else 4000,
        "sleep_minutes": 420,
        "resting_heart_rate": 60,
    }
    fields.update(changes)
    return HealthObservation(**fields)


def history(days: int = 35) -> list[HealthObservation]:
    return [observation(age) for age in range(days)]


def assert_insufficient(trend):
    assert trend.direction == "insufficient_data"
    assert trend.notable is False
    assert trend.baseline is None
    assert trend.recent is None
    assert trend.percent_change is None


def test_complete_calendar_window_keeps_existing_comparison_and_contract():
    result = metric_trend(history(), "steps")
    assert result.baseline == 4000
    assert result.recent == 2000
    assert result.percent_change == -50
    assert result.direction == "down" and result.notable
    assert set(result.model_dump()) == {
        "metric",
        "baseline",
        "recent",
        "percent_change",
        "direction",
        "notable",
        "observation",
    }


def test_recent_window_requires_calendar_days_not_last_five_values():
    # There are plenty of readings, but only three dates in the last five days.
    records = [observation(age) for age in range(0, 70, 2)]
    assert_insufficient(metric_trend(records, "steps"))


def test_latest_observed_date_anchors_window_even_if_metric_is_missing():
    records = history()
    records[0] = observation(0, steps=None)
    assert_insufficient(metric_trend(records, "steps"))
    # Other measurements from those same dates remain complete.
    assert metric_trend(records, "sleep_minutes").direction == "stable"


def test_identical_same_day_rows_neither_reweight_nor_inflate_sample_count():
    records = [observation(age, steps=1000 if age == 5 else 4000) for age in range(12)]
    expected = metric_trend(records, "steps")
    duplicated = records + [observation(5, steps=1000)] * 20 + [observation(0, steps=4000)] * 20
    assert metric_trend(duplicated, "steps") == expected
    # Six real baseline dates remain insufficient after many duplicate rows.
    too_few = history(11) + [observation(5)] * 30
    assert_insufficient(metric_trend(too_few, "steps"))


def test_conflicting_recent_date_is_excluded_not_averaged_or_last_write_wins():
    records = history() + [observation(0, observation_id="conflicting_recent", steps=9999)]
    result = metric_trend(records, "steps")
    assert_insufficient(result)
    assert "conflicting" in result.observation.lower()
    assert metric_trend(list(reversed(records)), "steps") == result


def test_conflicting_baseline_date_is_excluded_with_remaining_valid_dates():
    records = history(13) + [observation(5, observation_id="conflicting_baseline", steps=9999)]
    result = metric_trend(records, "steps")
    assert result.baseline == 4000 and result.recent == 2000
    assert result.notable
    assert "conflicting" in result.observation.lower()
    # Removing one of the other seven baseline dates leaves only six.
    records = [record for record in records if record.timestamp != END - timedelta(days=12)]
    assert_insufficient(metric_trend(records, "steps"))


def test_same_day_conflicts_are_specific_to_the_metric():
    records = history() + [observation(0, observation_id="different_sleep", sleep_minutes=300)]
    results = {trend.metric: trend for trend in compute_trends(records)}
    assert results["steps"].percent_change == -50
    assert_insufficient(results["sleep_minutes"])
    assert results["resting_heart_rate"].direction == "stable"


def test_null_duplicate_is_missing_not_a_conflicting_zero():
    records = history() + [observation(0, observation_id="missing_duplicate", steps=None)]
    assert metric_trend(records, "steps") == metric_trend(history(), "steps")


@pytest.mark.parametrize(
    ("baseline_source", "baseline_mode"),
    [
        ("simulated", DataMode.simulated),
        ("fitbit", DataMode.simulated),
        ("simulated", DataMode.live),
    ],
)
def test_mixed_sources_or_modes_never_generate_a_comparison(baseline_source, baseline_mode):
    records = [
        observation(age, source=baseline_source, data_mode=baseline_mode)
        if age >= 5
        else observation(age)
        for age in range(35)
    ]
    for result in compute_trends(records):
        assert_insufficient(result)
        assert "sources or data modes" in result.observation


def test_identical_values_from_different_modes_still_cannot_be_combined():
    records = history() + [observation(0, data_mode=DataMode.simulated)]
    assert_insufficient(metric_trend(records, "steps"))


def test_other_sources_outside_comparison_window_do_not_block_it():
    records = history() + [observation(35, source="simulated", data_mode=DataMode.simulated)]
    assert metric_trend(records, "steps") == metric_trend(history(), "steps")


def test_null_metrics_from_other_sources_do_not_contribute_to_comparison():
    records = history() + [
        observation(0, source="simulated", data_mode=DataMode.simulated, steps=None)
    ]
    assert metric_trend(records, "steps") == metric_trend(history(), "steps")


def test_baseline_is_limited_to_thirty_prior_calendar_days():
    records = history()
    records[-1] = observation(34, steps=7000)
    records += [observation(35, steps=200_000)]
    # Ages 5..34 are the baseline: 29 * 4000 + 7000, divided by 30.
    assert metric_trend(records, "steps").baseline == 4100


def test_sparse_baseline_uses_only_valid_distinct_dates_without_filling_zero():
    records = history(5) + [observation(age) for age in (5, 10, 15, 20, 25, 30, 34)]
    result = metric_trend(records, "steps")
    assert result.baseline == 4000 and result.percent_change == -50
    # Older records cannot replace missing dates inside the allowed baseline.
    records = records[:-1] + [observation(age) for age in range(35, 70)]
    assert_insufficient(metric_trend(records, "steps"))


@pytest.mark.parametrize("recent_value", [0, 1000])
def test_zero_baseline_cannot_produce_percentage_change(recent_value):
    records = [observation(age, steps=recent_value if age < 5 else 0) for age in range(12)]
    result = metric_trend(records, "steps")
    assert_insufficient(result)
    assert "baseline is zero" in result.observation


@pytest.mark.parametrize(
    ("metric", "baseline", "recent"),
    [("steps", 100, 75), ("sleep_minutes", 100, 88), ("resting_heart_rate", 100, 108)],
)
def test_existing_notable_thresholds_are_unchanged(metric, baseline, recent):
    records = [observation(age, **{metric: recent if age < 5 else baseline}) for age in range(12)]
    assert metric_trend(records, metric).notable


def test_existing_stability_threshold_is_unchanged():
    records = [observation(age, steps=1040 if age < 5 else 1000) for age in range(12)]
    result = metric_trend(records, "steps")
    assert result.direction == "stable" and not result.notable


def test_utc_day_semantics_handle_offset_and_naive_sqlite_timestamps():
    records = history(12)
    expected = metric_trend(records, "steps")
    offset = timezone(timedelta(hours=-5))
    converted = [
        record.model_copy(update={"timestamp": record.timestamp.astimezone(offset)})
        if index % 2
        else record.model_copy(update={"timestamp": record.timestamp.replace(tzinfo=None)})
        for index, record in enumerate(records)
    ]
    assert metric_trend(converted, "steps") == expected
    # A differently formatted timestamp for the same instant is still one day.
    converted.append(records[0].model_copy(update={"timestamp": END.astimezone(offset)}))
    assert metric_trend(converted, "steps") == expected


def test_empty_input_and_observation_text_stay_non_diagnostic():
    for result in compute_trends([]):
        assert_insufficient(result)
    examples = [history(), [], history() + [observation(0, steps=9999)]]
    for records in examples:
        before = [record.model_dump() for record in records]
        for trend in compute_trends(records):
            assert not any(
                word in trend.observation.lower()
                for word in ("disease", "diagnos", "depress", "illness")
            )
        assert [record.model_dump() for record in records] == before
