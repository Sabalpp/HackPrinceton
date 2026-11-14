"""Calendar windows and provenance guards for daily personal-baseline trends."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.health import service
from app.health.tiger import project_daily
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


def test_explicit_completed_day_cutoff_excludes_only_current_steps_day():
    records = [
        observation(age, steps=None if age == 0 else (2000 if age <= 5 else 4000))
        for age in range(13)
    ]
    cutoff = END.date() - timedelta(days=1)
    result = metric_trend(records, "steps", completed_steps_through=cutoff)
    assert result.baseline == 4000
    assert result.recent == 2000
    assert result.percent_change == -50 and result.notable
    assert "5 completed days ending 2026-11-13" in result.observation
    # An unmarked missing latest date still fails; it is never automatically skipped.
    assert_insufficient(metric_trend(records, "steps"))


@pytest.mark.parametrize("missing_age", [1, 3, 5])
def test_completed_day_cutoff_does_not_slide_over_missing_recent_dates(missing_age):
    records = history()
    records[0] = observation(0, steps=None)
    records[missing_age] = observation(missing_age, steps=None)
    result = metric_trend(records, "steps", completed_steps_through=END.date() - timedelta(days=1))
    assert_insufficient(result)


def test_completed_day_cutoff_does_not_slide_back_to_stale_history():
    # Plenty of historical days, but none reaches the caller's completed-day cutoff.
    records = [observation(age) for age in range(2, 37)]
    assert_insufficient(
        metric_trend(records, "steps", completed_steps_through=END.date() - timedelta(days=1))
    )


def test_completed_day_cutoff_does_not_hide_conflicting_or_mixed_history():
    cutoff = END.date() - timedelta(days=1)
    records = history() + [observation(1, observation_id="conflicting_yesterday", steps=9999)]
    assert_insufficient(metric_trend(records, "steps", completed_steps_through=cutoff))
    records = history()
    records[6] = observation(6, source="simulated", data_mode=DataMode.simulated)
    result = metric_trend(records, "steps", completed_steps_through=cutoff)
    assert_insufficient(result)
    assert "sources or data modes" in result.observation


def test_completed_day_cutoff_does_not_change_sleep_or_resting_hr_windows():
    records = history()
    records[0] = observation(0, steps=None, sleep_minutes=None, resting_heart_rate=None)
    results = {
        trend.metric: trend
        for trend in compute_trends(records, completed_steps_through=END.date() - timedelta(days=1))
    }
    assert results["steps"].notable
    assert_insufficient(results["sleep_minutes"])
    assert_insufficient(results["resting_heart_rate"])


def test_tiger_projection_and_cutoff_share_parent_local_calendar_dates():
    # UTC has advanced to Nov 14, but the parent's Nov 13 is still incomplete.
    now = datetime(2026, 11, 14, 2, tzinfo=UTC)
    zone = ZoneInfo("America/Los_Angeles")
    today = now.astimezone(zone).date()
    rows = []
    for age in range(13):
        day = today - timedelta(days=age)
        begin = datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=9)
        rows.append(
            {
                "event_id": f"step_fixture_{age}",
                "subject_id": "wearer_fixture",
                "source": "google_health",
                "source_stream": "watch_fixture",
                "metric": "steps",
                "observed_at": begin,
                "ended_at": begin + timedelta(minutes=1),
                "local_date": day,
                "value": 2000 if age <= 5 else 4000,
                "unit": "count",
                "kind": "interval",
                "attributes": {
                    "start_local_date": day.isoformat(),
                    "coverage_end_local_date": day.isoformat(),
                },
            }
        )
    records = project_daily(
        rows,
        subject_id="wearer_fixture",
        parent_id="parent_fixture",
        start=today - timedelta(days=12),
        end=today,
        now=now,
        timezone="America/Los_Angeles",
    )
    assert records[-1].steps is None
    assert records[-1].timestamp == datetime(2026, 11, 13, tzinfo=UTC)
    result = metric_trend(records, "steps", completed_steps_through=today - timedelta(days=1))
    assert result.baseline == 4000 and result.recent == 2000
    assert result.notable and result.percent_change == -50
    # A UTC-derived cutoff would retain the incomplete local day and must fail.
    assert_insufficient(
        metric_trend(records, "steps", completed_steps_through=now.date() - timedelta(days=1))
    )


@pytest.mark.parametrize("parent_timezone", ["America/Los_Angeles", "Asia/Kathmandu"])
def test_summary_wires_completed_steps_cutoff_from_parent_timezone(monkeypatch, parent_timezone):
    now = datetime(2026, 11, 14, 2, tzinfo=UTC)
    today = now.astimezone(ZoneInfo(parent_timezone)).date()
    local_day_label = datetime.combine(today, datetime.min.time(), UTC)
    records = [
        observation(
            age,
            timestamp=local_day_label - timedelta(days=age),
            steps=None if age == 0 else (2000 if age <= 5 else 4000),
        )
        for age in range(13)
    ]
    monkeypatch.setattr(service, "recent", lambda *_: records)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    parent = SimpleNamespace(role="parent", timezone=parent_timezone)
    session = SimpleNamespace(get=lambda *_: parent)
    result = service.summary(session, "parent_fixture")
    steps = next(trend for trend in result.trends if trend.metric == "steps")
    assert result.data_mode == "live" and result.suggest_checkin
    assert steps.baseline == 4000 and steps.recent == 2000
    assert (today - timedelta(days=1)).isoformat() in steps.observation
    assert result.observations == records


@pytest.mark.parametrize("parent_timezone", [None, "", "Not/AZone"])
def test_summary_invalid_timezone_leaves_steps_unavailable(monkeypatch, parent_timezone):
    monkeypatch.setattr(service, "recent", lambda *_: history())
    parent = SimpleNamespace(role="parent", timezone=parent_timezone)
    session = SimpleNamespace(get=lambda *_: parent)
    result = service.summary(session, "parent_fixture")
    by_metric = {trend.metric: trend for trend in result.trends}
    assert_insufficient(by_metric["steps"])
    assert "time zone is missing or invalid" in by_metric["steps"].observation
    assert by_metric["sleep_minutes"].direction == "stable"
    assert by_metric["resting_heart_rate"].direction == "stable"
    assert not result.suggest_checkin


def test_summary_missing_parent_cannot_guess_completed_steps_date(monkeypatch):
    monkeypatch.setattr(service, "recent", lambda *_: history())
    result = service.summary(SimpleNamespace(get=lambda *_: None), "parent_fixture")
    assert_insufficient(next(trend for trend in result.trends if trend.metric == "steps"))


def test_summary_simulated_history_keeps_existing_calendar_comparison(monkeypatch):
    records = [row.model_copy(update={"data_mode": DataMode.simulated}) for row in history()]
    monkeypatch.setattr(service, "recent", lambda *_: records)
    session = SimpleNamespace(get=lambda *_: pytest.fail("Simulated history needs no live cutoff"))
    result = service.summary(session, "parent_fixture")
    assert result.data_mode == "simulated"
    assert result.trends == compute_trends(records)
    assert result.suggest_checkin
