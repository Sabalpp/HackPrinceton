"""Read-only family health status, using synthetic data in temporary SQLite."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.health import service as health
from app.models import FamilyLink, Person
from app.schemas.contracts import DataMode, HealthObservation
from app.services import family

NOW = datetime(2026, 11, 14, 20, tzinfo=UTC)
TODAY = NOW.replace(hour=0)


def observation(age=0, **updates):
    values = {
        "observation_id": f"family_fixture_{age}",
        "parent_id": "parent_fixture",
        "source": "fitbit",
        "data_mode": DataMode.live,
        "timestamp": TODAY - timedelta(days=age),
        "steps": None if age == 0 else (4321 if age == 1 else 4900),
        "resting_heart_rate": 62 if age == 0 else 61,
        "sleep_minutes": None if age == 0 else 420,
    }
    values.update(updates)
    return HealthObservation(**values)


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'family-health.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(health, "utcnow", lambda: NOW)
    monkeypatch.setattr(family, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        family,
        "send_to_person",
        lambda *_args, **_kwargs: pytest.fail("Status must not message anyone"),
    )
    with Session(engine) as session:
        parent = Person(
            id="parent_fixture",
            role="parent",
            display_name="Fixture Parent",
            timezone="America/New_York",
        )
        child = Person(id="child_fixture", role="child", display_name="Fixture Child")
        link = FamilyLink(
            parent_id=parent.id,
            member_id=child.id,
            relationship="son",
            parent_label="Mom",
            share_health=True,
            share_checkins=False,
        )
        session.add_all([parent, child, link])
        session.commit()
        yield SimpleNamespace(session=session, parent=parent, link=link)
    engine.dispose()


def put(context, records):
    health.ingest(context.session, records)
    context.session.commit()


def lines(context):
    return family.status_lines(context.session, context.link, context.parent)


def metric_line(result, metric_label):
    return next(line for line in result if metric_label.lower() in line.lower())


def test_eight_live_days_show_dated_readings_and_exact_insufficient_baseline(context):
    put(context, [observation(age) for age in range(8)])
    summary = health.summary(context.session, context.parent.id)
    result = lines(context)
    assert (
        "Latest recorded steps: 4,321 on 2026-11-13 (live Fitbit data). "
        "No value recorded for 2026-11-14."
    ) in result
    assert "Latest reported resting heart rate: 62 bpm on 2026-11-14 (live Fitbit data)." in result
    assert (
        "Latest recorded main sleep: 420 min on 2026-11-13 (live Fitbit data). "
        "No value recorded for 2026-11-14."
    ) in result
    text = "\n".join(result)
    assert any(trend.observation in text for trend in summary.trends)
    assert "Not enough daily steps data yet to compare with a baseline." in text
    assert "Mom: no recorded alerts or open help requests." in result
    assert "today" not in text.lower() and "current" not in text.lower()


def test_health_permission_prevents_even_loading_or_disclosing_health(context, monkeypatch):
    put(context, [observation(age) for age in range(8)])
    context.link.share_health = False
    context.session.commit()
    monkeypatch.setattr(
        health, "summary", lambda *_: pytest.fail("Unshared health must not be loaded")
    )
    result = lines(context)
    assert result == ["Mom: no recorded alerts or open help requests."]
    assert not any(word in " ".join(result).lower() for word in ("steps", "sleep", "bpm", "health"))


def test_status_is_read_only_and_does_not_request_contact_or_consent(context):
    put(context, [observation(age) for age in range(8)])
    connection = context.session.connection().connection.driver_connection
    changes_before = connection.total_changes
    result = lines(context)
    assert connection.total_changes == changes_before
    text = " ".join(result).lower()
    assert not any(word in text for word in ("reply yes", "contacted", "asked mom", "consent"))


def test_simulated_values_remain_labeled_simulated(context):
    put(context, [observation(age, data_mode=DataMode.simulated) for age in range(8)])
    result = lines(context)
    assert all("simulated data" in line for line in result if line.startswith("Latest "))
    assert "live Fitbit data" not in " ".join(result)
    assert "Health trend (simulated data):" in " ".join(result)


def test_mixed_history_labels_each_latest_reading_and_history_truthfully(context):
    put(context, [observation(age) for age in range(5)])
    put(context, [observation(age, data_mode=DataMode.simulated) for age in range(5, 13)])
    result = lines(context)
    assert "(live Fitbit data)" in metric_line(result, "Latest recorded steps")
    assert "Health trend (mixed live/simulated data):" in " ".join(result)
    assert "Cannot compare daily steps across different sources or data modes." in " ".join(result)


def test_same_day_same_values_from_both_modes_are_marked_mixed(context):
    put(context, [observation()])
    put(context, [observation(observation_id="simulated_duplicate", data_mode=DataMode.simulated)])
    assert (
        "Latest reported resting heart rate: 62 bpm on 2026-11-14 (mixed live/simulated data)."
    ) in lines(context)


def test_identical_duplicate_does_not_duplicate_the_metric_line(context):
    put(context, [observation(age) for age in range(8)])
    expected = lines(context)
    put(context, [observation(1, observation_id="identical_duplicate")])
    assert lines(context) == expected


def test_conflicting_latest_date_does_not_choose_a_value_or_older_clean_date(context):
    put(context, [observation(age) for age in range(8)])
    put(context, [observation(1, observation_id="conflicting_steps", steps=9999)])
    step = metric_line(lines(context), "Recorded steps")
    assert step == (
        "Recorded steps: conflicting values for 2026-11-13 (live Fitbit data). "
        "No value recorded for 2026-11-14."
    )
    assert not any(value in step for value in ("4,321", "9,999", "4,900"))


def test_missing_values_are_not_rendered_as_zero(context):
    put(context, [observation(steps=None, resting_heart_rate=None, sleep_minutes=None)])
    result = lines(context)
    assert "Recorded steps: no value recorded through 2026-11-14." in result
    assert "Reported resting heart rate: no value recorded through 2026-11-14." in result
    assert "Recorded main sleep: no value recorded through 2026-11-14." in result
    assert not any(value in " ".join(result) for value in ("0 bpm", "0 min", "steps: 0"))


def test_actually_recorded_zero_is_preserved_and_dated(context):
    put(context, [observation(1, steps=0, sleep_minutes=0)])
    result = lines(context)
    assert "Latest recorded steps: 0 on 2026-11-13 (live Fitbit data)." in result
    assert "Latest recorded main sleep: 0 min on 2026-11-13 (live Fitbit data)." in result


def test_stale_reading_is_shown_with_its_actual_date(context):
    put(context, [observation(7)])
    result = lines(context)
    assert "Latest recorded steps: 4,900 on 2026-11-07 (live Fitbit data)." in result
    assert "today" not in " ".join(result).lower()


def test_empty_history_explains_no_records_and_insufficient_baseline(context):
    result = lines(context)
    assert "No health readings recorded yet." in result
    assert "Not enough daily steps data yet to compare with a baseline." in " ".join(result)
