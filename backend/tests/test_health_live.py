"""Synthetic fixtures only: live source identity, freshness, permissions and retry safety."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.db import session_scope
from app.experience_models import AccessAudit, Account, CareProfile
from app.health import live, live_sync, sources, tiger
from app.health.live_models import WatchSourceLink
from app.models import FamilyLink, HealthObservationRow, OutboundMessage, Person, TimelineEventRow
from app.schemas.contracts import HealthObservation
from tests.conftest import CHILD, PARENT

NOW = datetime(2026, 11, 14, 22, tzinfo=UTC)


def sample(**overrides):
    row = dict(
        event_id="event_a",
        subject_id="wearer_a",
        source="ble",
        source_stream="watch_a",
        metric="heart_rate_bpm",
        observed_at=NOW - timedelta(seconds=2),
        ended_at=None,
        local_date=NOW.date(),
        value=78.0,
        unit="bpm",
        kind="sample",
        attributes={"payload_verified": True, "time_semantics": "mac_receipt_time"},
    )
    row.update(overrides)
    return row


def report(rows, *, now=NOW):
    return live.project_sensor_rows(
        rows,
        parent=Person(id="parent_a", role="parent", display_name="Wearer", timezone="UTC"),
        subject_id="wearer_a",
        now=now,
    )


def test_valid_sample_is_live_bpm_not_resting_hr_or_inferred_hrv():
    result = report([sample()])
    assert result.live_heart_rate.value == 78
    assert result.live_heart_rate.source == "ble"
    assert result.live_heart_rate.age_seconds == 2
    assert result.live_heart_rate.freshness == "fresh"
    assert all(
        metric.value is None and metric.freshness == "missing" for metric in result.cloud_metrics
    )
    assert result.recent_heart_rate[0].value == 78
    assert (
        "subject_id" not in result.model_dump_json() and "wearer_a" not in result.model_dump_json()
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"value": None},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": 10**400},
        {"value": True},
        {"value": "78"},
        {"value": 0},
        {"value": -1},
        {"value": 65536},
        {"unit": "ms"},
        {"kind": "daily"},
        {"source_stream": ""},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"local_date": "bad"},
        {"attributes": {"data_mode": "simulated"}},
    ],
)
def test_latest_invalid_sample_never_falls_back_to_a_plausible_number(overrides):
    result = report([sample(observed_at=NOW - timedelta(seconds=10)), sample(**overrides)])
    # A malformed/naive timestamp sorts behind the usable older record; it never
    # produces a point itself. Other invalid latest records remain explicitly null.
    if "observed_at" in overrides and overrides["observed_at"].tzinfo is None:
        assert len(result.recent_heart_rate) == 1
    else:
        assert result.live_heart_rate.value is None
        assert result.live_heart_rate.freshness == "invalid"


def test_subject_isolation_and_unknown_metrics_are_not_returned():
    result = report(
        [
            sample(subject_id="other_wearer", value=130),
            sample(source="unknown"),
            sample(metric="blood_pressure_systolic", value=120),
        ]
    )
    assert result.live_heart_rate.value is None
    assert not result.recent_heart_rate


def test_stale_values_remain_dated_and_graph_does_not_interpolate_gaps():
    result = report(
        [
            sample(observed_at=NOW - timedelta(minutes=2)),
            sample(observed_at=NOW - timedelta(minutes=5), value=70),
            sample(observed_at=NOW - timedelta(hours=1), value=65),
        ]
    )
    assert result.live_heart_rate.freshness == "stale"
    assert result.live_heart_rate.age_seconds == 120
    assert [point.value for point in result.recent_heart_rate] == [70, 78]


def test_duplicate_timestamp_collapses_but_conflicting_values_are_not_chosen():
    assert len(report([sample(), sample(event_id="duplicate")]).recent_heart_rate) == 1
    conflicted = report([sample(), sample(event_id="other", value=90)])
    assert conflicted.live_heart_rate.freshness == "invalid"
    assert not conflicted.recent_heart_rate


def test_two_simultaneous_watch_streams_do_not_become_one_personal_signal():
    result = report(
        [sample(), sample(source_stream="watch_b", observed_at=NOW - timedelta(seconds=3))]
    )
    assert result.live_heart_rate.freshness == "invalid"
    assert not result.recent_heart_rate


def test_latest_stream_history_does_not_mix_a_previous_watch():
    result = report(
        [sample(), sample(source_stream="old_watch", observed_at=NOW - timedelta(minutes=1))]
    )
    assert len(result.recent_heart_rate) == 1


def test_cloud_daily_date_anchor_is_distinct_from_physical_sample_time():
    # In Kathmandu the source day is Nov 15 although UTC has not crossed midnight.
    parent = Person(id="parent_a", role="parent", display_name="Wearer", timezone="Asia/Kathmandu")
    row = sample(
        source="google_health",
        metric="resting_heart_rate_bpm",
        kind="daily",
        observed_at=datetime(2026, 11, 15, tzinfo=UTC),
        local_date="2026-11-15",
        value=61,
    )
    result = live.project_sensor_rows([row], parent=parent, subject_id="wearer_a", now=NOW)
    resting = next(m for m in result.cloud_metrics if m.metric == "resting_heart_rate_bpm")
    assert resting.value == 61 and resting.freshness == "fresh" and resting.age_seconds is None
    assert result.live_heart_rate.value is None
    assert (
        next(
            m for m in report([row]).cloud_metrics if m.metric == "resting_heart_rate_bpm"
        ).freshness
        == "invalid"
    )


def test_cloud_interval_zero_is_valid_but_not_a_daily_total():
    row = sample(
        source="google_health",
        metric="steps",
        unit="count",
        kind="interval",
        value=0,
        observed_at=NOW - timedelta(minutes=2),
        ended_at=NOW - timedelta(minutes=1),
    )
    steps = next(m for m in report([row]).cloud_metrics if m.metric == "steps")
    assert steps.value == 0 and steps.kind == "interval"
    assert "interval" in steps.label
    assert (
        next(
            m
            for m in report([{**row, "ended_at": NOW + timedelta(minutes=1)}]).cloud_metrics
            if m.metric == "steps"
        ).freshness
        == "invalid"
    )


def test_history_is_bounded_even_with_large_fixture():
    rows = [
        sample(observed_at=NOW - timedelta(seconds=offset), event_id=f"event_{offset}")
        for offset in range(600)
    ]
    assert len(report(rows).recent_heart_rate) == 300


def real_profiles(session):
    parent = Person(id="real_parent", role="parent", display_name="Test Wearer", timezone="UTC")
    child = Person(id="real_child", role="child", display_name="Test Family")
    session.add_all([parent, child])
    session.flush()
    session.add(
        Account(person_id=child.id, email="family@example.invalid", password_hash="test-only")
    )
    session.add(CareProfile(parent_id=parent.id, owner_id=child.id))
    session.add(
        FamilyLink(parent_id=parent.id, member_id=child.id, relationship="child", share_health=True)
    )
    session.flush()
    return parent, child


def test_operator_link_requires_real_account_consent_and_source(client, monkeypatch):
    monkeypatch.setattr(
        sources, "source_exists", lambda session, subject_id: subject_id == "wearer_a"
    )
    with session_scope() as session:
        parent, actor = real_profiles(session)
        opts = dict(
            parent_id=parent.id, actor_id=actor.id, subject_id="wearer_a", consent_confirmed=True
        )
        with pytest.raises(sources.SourceLinkError, match="authorized"):
            sources.link_source(session, **{**opts, "consent_confirmed": False})
        with pytest.raises(sources.SourceLinkError, match="No eligible"):
            sources.link_source(session, **{**opts, "subject_id": "missing"})
        link = sources.link_source(session, **opts)
        assert link.subject_id == "wearer_a"
        assert sources.link_source(session, **opts) is link
        assert (
            len(
                session.exec(
                    select(AccessAudit).where(AccessAudit.action == "watch_source.linked")
                ).all()
            )
            == 1
        )
        with pytest.raises(sources.SourceLinkError):
            sources.link_source(session, **{**opts, "parent_id": "parent_001"})
        with pytest.raises(sources.SourceLinkError, match="already has another"):
            sources.link_source(session, **{**opts, "subject_id": "different"})
        sources.disable_source(session, parent_id=parent.id, actor_id=actor.id)
        assert not link.enabled


def test_source_cannot_be_assigned_to_two_parents(client, monkeypatch):
    monkeypatch.setattr(sources, "source_exists", lambda *_: True)
    with session_scope() as session:
        parent, actor = real_profiles(session)
        sources.link_source(
            session,
            parent_id=parent.id,
            actor_id=actor.id,
            subject_id="wearer_a",
            consent_confirmed=True,
        )
        other = Person(id="second_parent", role="parent", display_name="Other")
        session.add(other)
        session.flush()
        session.add(CareProfile(parent_id=other.id, owner_id=actor.id))
        session.flush()
        with pytest.raises(sources.SourceLinkError, match="another parent"):
            sources.link_source(
                session,
                parent_id=other.id,
                actor_id=actor.id,
                subject_id="wearer_a",
                consent_confirmed=True,
            )


def test_sensor_endpoint_requires_auth_and_respects_sharing(client, monkeypatch):
    endpoint = "/api/parents/parent_001/sensor-health"
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=PARENT).json()["connection_status"] == "not_linked"
    assert client.get(endpoint, headers=CHILD).status_code == 200
    with session_scope() as session:
        link = session.exec(
            select(FamilyLink).where(
                FamilyLink.parent_id == "parent_001", FamilyLink.member_id == "child_001"
            )
        ).one()
        link.share_health = False
        session.add(link)
    assert client.get(endpoint, headers=CHILD).json() is None
    assert client.get("/api/parents/nonexistent/sensor-health", headers=CHILD).status_code == 404


def test_neighbor_cannot_get_metrics_even_with_malformed_family_membership(client):
    with session_scope() as session:
        child = session.get(Person, "child_001")
        child.role = "responder"
        session.add(child)
    assert client.get("/api/parents/parent_001/sensor-health", headers=CHILD).status_code == 404


def test_endpoint_uses_only_server_link_not_client_subject(client, monkeypatch):
    calls = []

    def fetch(session, *, subject_id, now):
        calls.append(subject_id)
        return [sample(observed_at=now - timedelta(seconds=1))]

    monkeypatch.setattr(live, "_query_sensor_rows", fetch)
    with session_scope() as session:
        session.add(WatchSourceLink(parent_id="parent_001", subject_id="wearer_a"))
    response = client.get(
        "/api/parents/parent_001/sensor-health?subject_id=other_wearer", headers=CHILD
    )
    assert response.status_code == 200
    assert response.json()["live_heart_rate"]["value"] == 78
    assert calls == ["wearer_a"]


def test_query_failure_rolls_back_savepoint_and_keeps_session_usable(client, monkeypatch):
    def broken(session, **_):
        return session.execute(text("SELECT * FROM missing_collector_schema"))

    monkeypatch.setattr(live, "_query_sensor_rows", broken)
    with session_scope() as session:
        session.add(WatchSourceLink(parent_id="parent_001", subject_id="wearer_a"))
        session.flush()
        result = live.read_sensor_health(session, session.get(Person, "parent_001"), now=NOW)
        assert result.connection_status == "unavailable" and result.live_heart_rate.value is None
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_missing_source_registry_does_not_poison_worker_or_api_transaction(client):
    with session_scope() as session:
        parent = session.get(Person, "parent_001")
        fixture = session.begin_nested()
        try:
            session.execute(
                text("ALTER TABLE watch_source_links RENAME TO unavailable_source_registry")
            )
            result = live.read_sensor_health(session, parent, now=NOW)
            assert result.connection_status == "unavailable"
            assert live_sync.sync_linked_sources(session, now=NOW) == 0
            assert session.execute(text("SELECT 1")).scalar() == 1
        finally:
            fixture.rollback()


def daily(parent_id="real_parent", *, offset=0, steps=3000):
    stamp = (NOW - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    return HealthObservation(
        observation_id=tiger.observation_id(parent_id, "wearer_a", stamp.date()),
        parent_id=parent_id,
        source="fitbit",
        data_mode="live",
        timestamp=stamp,
        steps=steps,
        resting_heart_rate=None,
        sleep_minutes=None,
    )


def test_worker_sync_persists_only_reported_fields_and_throttles(client, monkeypatch):
    values = [daily(offset=1, steps=0), daily(offset=0, steps=None)]
    fetched = []

    def fetch(*_):
        fetched.append(True)
        return values

    monkeypatch.setattr(live_sync, "_fetch", fetch)
    with session_scope() as session:
        parent, _actor = real_profiles(session)
        session.add(WatchSourceLink(parent_id=parent.id, subject_id="wearer_a"))
        assert live_sync.sync_linked_sources(session, now=NOW) == 1
        stored = session.exec(
            select(HealthObservationRow).where(HealthObservationRow.parent_id == parent.id)
        ).all()
        assert len(stored) == 1 and stored[0].steps == 0
        assert stored[0].resting_heart_rate is None
        assert live_sync.sync_linked_sources(session, now=NOW + timedelta(seconds=10)) == 0
        assert len(fetched) == 1
        assert live_sync.sync_linked_sources(session, now=NOW + timedelta(seconds=61)) == 0
        assert len(fetched) == 2
        assert (
            len(
                session.exec(
                    select(TimelineEventRow).where(
                        TimelineEventRow.parent_id == parent.id,
                        TimelineEventRow.kind == "health.synced",
                    )
                ).all()
            )
            == 1
        )
        values[0] = daily(offset=1, steps=None)
        assert live_sync.sync_linked_sources(session, now=NOW + timedelta(seconds=122)) == 1
        assert stored[0].steps is None  # source correction clears stale totals


def test_sync_repairs_a_changed_projection_even_when_source_digest_is_unchanged(
    client, monkeypatch
):
    monkeypatch.setattr(live_sync, "_fetch", lambda *_: [daily(offset=1, steps=2000)])
    with session_scope() as session:
        parent, _ = real_profiles(session)
        session.add(WatchSourceLink(parent_id=parent.id, subject_id="wearer_a"))
        assert live_sync.sync_linked_sources(session, now=NOW) == 1
        stored = session.exec(
            select(HealthObservationRow).where(HealthObservationRow.parent_id == parent.id)
        ).one()
        stored.steps = 123
        session.add(stored)
        session.flush()
        assert live_sync.sync_linked_sources(session, now=NOW + timedelta(seconds=61)) == 1
        assert stored.steps == 2000


def test_worker_missing_schema_schedules_retry_without_aborting_transaction(client, monkeypatch):
    def broken(session, *_):
        session.execute(text("SELECT * FROM missing_collector_schema"))

    monkeypatch.setattr(live_sync, "_fetch", broken)
    with session_scope() as session:
        parent, _ = real_profiles(session)
        link = WatchSourceLink(parent_id=parent.id, subject_id="wearer_a")
        session.add(link)
        assert live_sync.sync_linked_sources(session, now=NOW) == 0
        assert link.sync_status == "unavailable" and link.last_sync_at is None
        assert link.next_sync_at == NOW + timedelta(seconds=60)
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_recent_daily_change_queues_one_notice_with_health_permissions(client):
    with session_scope() as session:
        parent, child = real_profiles(session)
        hidden = Person(id="private_child", role="child", display_name="No health access")
        helper = Person(id="local_helper", role="responder", display_name="Neighbor")
        session.add_all([hidden, helper])
        session.flush()
        session.add_all(
            [
                FamilyLink(
                    parent_id=parent.id,
                    member_id=hidden.id,
                    relationship="child",
                    share_health=False,
                ),
                FamilyLink(
                    parent_id=parent.id,
                    member_id=helper.id,
                    relationship="neighbor",
                    share_health=True,
                ),
            ]
        )
        observations = [daily(offset=i, steps=1000 if i <= 5 else 5000) for i in range(1, 20)]
        assert live_sync.queue_daily_notices(session, parent, observations, now=NOW) == 1
        assert live_sync.queue_daily_notices(session, parent, observations, now=NOW) == 0
        messages = session.exec(
            select(OutboundMessage).where(OutboundMessage.person_id == child.id)
        ).all()
        assert len(messages) == 1 and messages[0].status == "available"
        assert "wellbeing prompt" in messages[0].text
        assert not session.exec(
            select(OutboundMessage).where(OutboundMessage.person_id.in_([hidden.id, helper.id]))
        ).all()


def test_stale_short_or_simulated_history_cannot_queue_live_notice(client):
    with session_scope() as session:
        parent, _ = real_profiles(session)
        assert live_sync.queue_daily_notices(session, parent, [daily(offset=1)], now=NOW) == 0
        stale = [daily(offset=i, steps=1000 if i <= 10 else 5000) for i in range(6, 25)]
        assert live_sync.queue_daily_notices(session, parent, stale, now=NOW) == 0
        simulated = [
            daily(offset=i, steps=1000 if i <= 5 else 5000).model_copy(
                update={"data_mode": "simulated"}
            )
            for i in range(1, 20)
        ]
        assert live_sync.queue_daily_notices(session, parent, simulated, now=NOW) == 0


def test_notice_calendar_uses_parent_day_without_shifting_daily_date_labels(client):
    with session_scope() as session:
        parent, child = real_profiles(session)
        parent.timezone = "America/New_York"
        observations = [daily(offset=i, steps=1000 if i <= 5 else 5000) for i in range(1, 20)]
        # UTC has entered Nov 15; the parent and UTC-encoded date labels still
        # use Nov 14, making Nov 13 the final completed activity day.
        clock = datetime(2026, 11, 15, 2, tzinfo=UTC)
        assert live_sync.queue_daily_notices(session, parent, observations, now=clock) == 1
        message = session.exec(
            select(OutboundMessage).where(OutboundMessage.person_id == child.id)
        ).one()
        assert ":2026-11-14:" in message.id


def test_postgresql_canonical_query_and_revision_order(client):
    """CI runs this against its disposable PG16 service, never the live Tiger DB.

    The version-table fixture lives in a rolled-back savepoint, including all
    schema DDL. It exercises production SQL rather than a mocked SQL result.
    """
    with session_scope() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("Exact LATERAL SQL validation requires the disposable PostgreSQL CI job")
        parent, actor = real_profiles(session)
        fixture = session.begin_nested()
        try:
            session.execute(text("CREATE SCHEMA IF NOT EXISTS air_health"))
            # Deliberately fail if this target already has a collector table:
            # never replace a real table or point TEST_DATABASE_URL at production.
            session.execute(
                text("""
                CREATE TABLE air_health.measurement_versions (
                    observed_at timestamptz NOT NULL, event_id text NOT NULL,
                    version_hash text NOT NULL, subject_id text NOT NULL,
                    source text NOT NULL, source_stream text NOT NULL,
                    metric text NOT NULL, ended_at timestamptz, local_date date,
                    value double precision, unit text, kind text, attributes jsonb,
                    source_revision_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
                    canonical_eligible boolean NOT NULL
                )
            """)
            )
            session.execute(
                text(
                    "CREATE INDEX ON air_health.measurement_versions (subject_id, metric, observed_at DESC)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX ON air_health.measurement_versions (event_id, source_revision_at DESC, received_at DESC)"
                )
            )
            rows = [
                sample(
                    version_hash="old",
                    source_revision_at=NOW - timedelta(minutes=1),
                    received_at=NOW,
                    canonical_eligible=True,
                    attributes="{}",
                ),
                sample(
                    version_hash="new",
                    value=81,
                    source_revision_at=NOW,
                    received_at=NOW,
                    canonical_eligible=True,
                    attributes="{}",
                ),
                sample(
                    version_hash="ineligible",
                    value=999,
                    source_revision_at=NOW + timedelta(seconds=1),
                    received_at=NOW,
                    canonical_eligible=False,
                    attributes="{}",
                ),
                sample(
                    event_id="other_subject",
                    subject_id="other_wearer",
                    value=150,
                    version_hash="one",
                    source_revision_at=NOW,
                    received_at=NOW,
                    canonical_eligible=True,
                    attributes="{}",
                ),
                sample(
                    event_id="daily_rhr",
                    source="google_health",
                    metric="resting_heart_rate_bpm",
                    kind="daily",
                    value=61,
                    version_hash="one",
                    source_revision_at=NOW,
                    received_at=NOW,
                    canonical_eligible=True,
                    attributes="{}",
                ),
            ]
            insert_sample = text("""
                INSERT INTO air_health.measurement_versions
                    (observed_at,event_id,version_hash,subject_id,source,source_stream,metric,
                     ended_at,local_date,value,unit,kind,attributes,source_revision_at,received_at,canonical_eligible)
                VALUES (:observed_at,:event_id,:version_hash,:subject_id,:source,:source_stream,:metric,
                        :ended_at,:local_date,:value,:unit,:kind,CAST(:attributes AS jsonb),:source_revision_at,:received_at,:canonical_eligible)
            """)
            session.execute(insert_sample, rows)
            sources.link_source(
                session,
                parent_id=parent.id,
                actor_id=actor.id,
                subject_id="wearer_a",
                consent_confirmed=True,
            )
            output = live.read_sensor_health(session, parent, now=NOW)
            assert output.connection_status == "available"
            assert output.live_heart_rate.value == 81
            assert [point.value for point in output.recent_heart_rate] == [81]
            assert (
                next(m for m in output.cloud_metrics if m.metric == "resting_heart_rate_bpm").value
                == 61
            )
            # A new collected sample must appear on the next read, without an
            # aggregate refresh or worker cycle. It remains the same wearer.
            next_clock = NOW + timedelta(seconds=2)
            session.execute(
                insert_sample,
                sample(
                    event_id="next_live_sample",
                    version_hash="one",
                    value=84,
                    observed_at=next_clock,
                    source_revision_at=next_clock,
                    received_at=next_clock,
                    canonical_eligible=True,
                    attributes="{}",
                ),
            )
            refreshed = live.read_sensor_health(session, parent, now=next_clock)
            assert refreshed.live_heart_rate.value == 84
            assert refreshed.live_heart_rate.freshness == "fresh"
            assert [point.value for point in refreshed.recent_heart_rate] == [81, 84]
            assert session.execute(text("SELECT 1")).scalar() == 1
        finally:
            fixture.rollback()
