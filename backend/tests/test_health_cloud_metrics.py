"""Cloud readout and descriptive arithmetic preserve dates, units and identity."""

from datetime import UTC, datetime, timedelta

import pytest

from app.health.live import CLOUD_METRICS, project_sensor_rows
from app.models import Person

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
PARENT = Person(id="cloud_parent", role="parent", display_name="Fixture", timezone="UTC")


def row(metric, value, age=1, stream="watch_fixture", **changes):
    spec = next(s for s in CLOUD_METRICS if s.metric == metric)
    stamp = (NOW - timedelta(days=age)).replace(hour=0)
    return {
        "event_id": f"{metric}_{age}_{stream}",
        "subject_id": "fixture",
        "source": "google_health",
        "source_stream": stream,
        "metric": metric,
        "observed_at": stamp,
        "ended_at": stamp + timedelta(minutes=1) if spec.kind in ("session", "interval") else None,
        "local_date": stamp.date(),
        "value": value,
        "unit": spec.unit,
        "kind": spec.kind,
        "attributes": {},
        **changes,
    }


def project(rows):
    return project_sensor_rows(rows, parent=PARENT, subject_id="fixture", now=NOW)


def test_all_scalar_cloud_specs_can_reach_the_authenticated_readout():
    result = project([row(s.metric, 12) for s in CLOUD_METRICS])
    assert len(result.cloud_metrics) == len(CLOUD_METRICS)
    assert all(m.value == 12 for m in result.cloud_metrics)
    names = {m.metric for m in result.cloud_metrics}
    assert {
        "steps",
        "distance_m",
        "active_zone_minutes",
        "skin_temp_nightly_c",
        "exercise_steps",
        "sleep_minutes_awake",
    } <= names
    # Zone duration has multiple dimensions; do not pick an arbitrary zone as a scalar.
    assert "exercise_heart_rate_zone_seconds" not in names


@pytest.mark.parametrize(
    "metric,current,previous,expected",
    [
        ("resting_heart_rate_bpm", 67, 65, "+3.1%"),
        ("hrv_daily_average_ms", 20, 25, "-20.0%"),
        ("spo2_daily_mean_percent", 95, 97, "-2.00 percentage points"),
        ("skin_temp_nightly_c", 34, 33, "+1.00 °C"),
    ],
)
def test_daily_comparison_exposes_inputs_and_correct_units(metric, current, previous, expected):
    result = project([row(metric, current), row(metric, previous, age=2)])
    value = next(m for m in result.cloud_metrics if m.metric == metric)
    assert value.value == current
    assert expected in value.message
    assert "2026-11-13" in value.message
    assert "not a learned personal baseline" in value.message
    if value.unit in ("degC", "%"):
        assert "Relative change" not in value.message


@pytest.mark.parametrize(
    "previous",
    [
        {"stream": "other_watch"},
        {"attributes": {"data_mode": "simulated"}},
        {"value": None},
        {"unit": "wrong"},
    ],
)
def test_daily_comparison_refuses_other_streams_or_invalid_previous_readings(previous):
    result = project(
        [
            row("resting_heart_rate_bpm", 70),
            row("resting_heart_rate_bpm", age=2, **{"value": 65, **previous}),
        ]
    )
    metric = next(m for m in result.cloud_metrics if m.metric == "resting_heart_rate_bpm")
    assert "Change =" not in metric.message


def test_conflicting_previous_day_and_zero_baseline_do_not_produce_false_math():
    records = [
        row("hrv_daily_average_ms", 20),
        row("hrv_daily_average_ms", 25, age=2),
        row("hrv_daily_average_ms", 26, age=2),
    ]
    metric = next(m for m in project(records).cloud_metrics if m.metric == "hrv_daily_average_ms")
    assert "Change =" not in metric.message
    metric = next(
        m
        for m in project(
            [row("hrv_daily_average_ms", 20), row("hrv_daily_average_ms", 0, age=2)]
        ).cloud_metrics
        if m.metric == "hrv_daily_average_ms"
    )
    assert "+20.00 ms" in metric.message
    assert "Relative change" not in metric.message


def test_interval_steps_are_not_presented_as_daily_totals_or_baselines():
    metric = next(m for m in project([row("steps", 0)]).cloud_metrics if m.metric == "steps")
    assert metric.value == 0
    assert "not a complete daily total" in metric.message
    assert "Change =" not in metric.message
