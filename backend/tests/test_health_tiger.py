"""Synthetic-only tests for Tiger projection and FarCare's existing ingest handoff."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.config import get_settings
from app.db import session_scope
from app.health import sync_tiger, tiger
from app.models import FamilyLink, Person
from tests.conftest import ADMIN, CHILD

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
DAY = date(2026, 11, 10)
KEY = "synthetic-ingest-test"


def test_sync_uses_parent_calendar_and_rejects_mismatched_override(client, monkeypatch):
    # At this instant it is Nov 15 in Kathmandu but still Nov 14 in New York.
    now = datetime(2026, 11, 14, 22, tzinfo=UTC)
    with session_scope() as session:
        parent = session.get(Person, "parent_001")
        parent.timezone = "Asia/Kathmandu"
        session.add(parent)
    fetched = Mock(return_value=[])
    monkeypatch.setattr(sync_tiger, "fetch_daily", fetched)
    monkeypatch.setattr(sync_tiger, "utcnow", lambda: now)
    args = SimpleNamespace(subject_id="wearer", parent_id="parent_001", days=8, timezone=None)
    assert sync_tiger.read_projection(args) == []
    assert fetched.call_args.kwargs["timezone"] == "Asia/Kathmandu"
    assert fetched.call_args.kwargs["end"] == date(2026, 11, 15)
    fetched.reset_mock()
    args.timezone = "America/New_York"
    with pytest.raises(sync_tiger.SyncError, match="must match the parent profile"):
        sync_tiger.read_projection(args)
    fetched.assert_not_called()


def record(metric="steps", *, day=DAY, value=100, stream="watch_a", **overrides):
    begin = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=8)
    finish = begin + timedelta(minutes=10)
    kind, unit = "interval", "count"
    attrs = {
        "start_local_date": day.isoformat(),
        "end_local_date": day.isoformat(),
        "coverage_end_local_date": day.isoformat(),
        "crosses_local_midnight": False,
    }
    if metric == "resting_heart_rate_bpm":
        kind, unit, finish = "daily", "bpm", None
    elif metric in ("sleep_minutes_asleep", "sleep_period_minutes"):
        kind, unit = "session", "min"
        begin -= timedelta(hours=10)
        finish = begin + timedelta(hours=8)
        day = begin.date()
        attrs = {"end_local_date": finish.date().isoformat(), "main_sleep": True, "nap": False}
    row = {
        "event_id": f"event_{metric}_{stream}",
        "subject_id": "wearer",
        "source": "google_health",
        "source_stream": stream,
        "metric": metric,
        "observed_at": begin,
        "ended_at": finish,
        "local_date": day,
        "value": value,
        "unit": unit,
        "kind": kind,
        "attributes": attrs,
    }
    row.update(overrides)
    return row


def project(rows, **overrides):
    options = dict(
        subject_id="wearer", parent_id="parent_001", start=DAY, end=DAY, now=NOW, timezone="UTC"
    )
    options.update(overrides)
    return tiger.project_daily(rows, **options)


def test_projects_canonical_three_metrics_without_relabeling_live_hr():
    rows = [
        record(value=100),
        record(
            value=150,
            observed_at=datetime(2026, 11, 10, 8, 10, tzinfo=UTC),
            ended_at=datetime(2026, 11, 10, 8, 20, tzinfo=UTC),
        ),
        record("resting_heart_rate_bpm", value=62.5),
        record("sleep_minutes_asleep", value=420),
        record("heart_rate_bpm", value=110, source="ble"),
        record("sleep_period_minutes", value=480),
        record(value=999, subject_id="other_wearer"),
    ]
    [obs] = project(rows)
    assert (obs.steps, obs.resting_heart_rate, obs.sleep_minutes) == (250, 63, 420)
    assert obs.source == "fitbit" and obs.data_mode == "live"
    assert obs.timestamp == datetime(2026, 11, 10, tzinfo=UTC)
    assert "wearer" not in obs.observation_id
    assert project(list(reversed(rows)))[0] == obs
    assert project(rows, parent_id="parent_002")[0].observation_id != obs.observation_id
    assert project([], subject_id="another_subject")[0].observation_id != obs.observation_id


@pytest.mark.parametrize("alternative", ["stream", "overlap", "duplicate"])
def test_steps_do_not_combine_ambiguous_intervals_or_providers(alternative):
    first = record()
    second = record(stream="watch_b") if alternative == "stream" else dict(first)
    if alternative == "overlap":
        second["observed_at"] += timedelta(minutes=5)
        second["ended_at"] += timedelta(minutes=5)
    assert project([first, second])[0].steps is None


def test_cross_midnight_steps_invalidate_every_affected_requested_day():
    cross = record(
        day=DAY - timedelta(days=1),
        observed_at=datetime(2026, 11, 9, 23, tzinfo=UTC),
        ended_at=datetime(2026, 11, 12, 1, tzinfo=UTC),
        attributes={
            "start_local_date": "2026-11-09",
            "coverage_end_local_date": "2026-11-12",
            "end_local_date": "2026-11-12",
            "crosses_local_midnight": True,
        },
    )
    rows = [record(day=DAY + timedelta(days=i)) for i in range(4)]
    result = project([cross, *rows], end=DAY + timedelta(days=3))
    assert [obs.steps for obs in result] == [None, None, None, 100]


def test_midnight_exclusive_endpoint_is_not_a_second_day():
    row = record(ended_at=datetime(2026, 11, 11, tzinfo=UTC))
    row["attributes"]["end_local_date"] = "2026-11-11"
    assert project([row])[0].steps == 100


def test_today_steps_are_null_using_explicit_timezone_and_missing_days_stay_null():
    today_la = date(2026, 11, 13)
    rows = [record(day=today_la), record(day=today_la - timedelta(days=1))]
    result = project(
        rows,
        start=date(2026, 11, 11),
        end=today_la,
        now=datetime(2026, 11, 14, 0, 30, tzinfo=UTC),
        timezone="America/Los_Angeles",
    )
    assert [o.steps for o in result] == [None, 100, None]
    assert all(o.sleep_minutes is None and o.resting_heart_rate is None for o in result)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -1, 200_001, 10**1000, 1.5, True, "100"]
)
def test_steps_reject_invalid_or_fractional_values(value):
    assert project([record(value=value)])[0].steps is None


def test_sum_range_and_declared_resting_metric_are_enforced():
    first = record(value=150_000)
    second = record(
        value=100_000,
        observed_at=datetime(2026, 11, 10, 9, tzinfo=UTC),
        ended_at=datetime(2026, 11, 10, 9, 10, tzinfo=UTC),
    )
    assert project([first, second])[0].steps is None
    assert project([record("resting_heart_rate_bpm", value=19.9)])[0].resting_heart_rate is None
    assert (
        project([record("resting_heart_rate_bpm", value=80, source="ble")])[0].resting_heart_rate
        is None
    )
    assert (
        project([record("resting_heart_rate_bpm", value=80, kind="sample")])[0].resting_heart_rate
        is None
    )
    assert (
        project(
            [
                record("resting_heart_rate_bpm", value=60),
                record("resting_heart_rate_bpm", value=61, stream="other"),
            ]
        )[0].resting_heart_rate
        is None
    )


def test_sleep_uses_only_one_explicit_main_session_on_wake_day():
    main = record("sleep_minutes_asleep", value=420)
    nap = record(
        "sleep_minutes_asleep",
        value=30,
        attributes={"main_sleep": False, "nap": True, "end_local_date": DAY.isoformat()},
    )
    unmarked = record(
        "sleep_minutes_asleep", value=400, attributes={"end_local_date": DAY.isoformat()}
    )
    result = project([main, nap, unmarked], start=DAY - timedelta(days=1))
    assert [o.sleep_minutes for o in result] == [None, 420]
    other_main = record("sleep_minutes_asleep", value=425, stream="other_provider")
    assert project([main, other_main])[0].sleep_minutes is None
    main["attributes"]["nap"] = True
    assert project([main])[0].sleep_minutes is None


@pytest.mark.parametrize("value", [float("nan"), 1441, -1, 420.5])
def test_sleep_missing_or_invalid_does_not_become_zero(value):
    assert project([record("sleep_minutes_asleep", value=value)])[0].sleep_minutes is None


def test_projection_requires_explicit_subject_and_aware_now():
    with pytest.raises(ValueError, match="subject_id"):
        project([], subject_id=" ")
    with pytest.raises(ValueError, match="timezone-aware"):
        project([], now=datetime(2026, 11, 14))


def test_query_is_subject_scoped_parameterized_and_uses_current_view():
    session = Mock()
    session.execute.return_value.mappings.return_value = [record()]
    subject = "wearer' OR TRUE --"
    result = tiger.fetch_daily(
        session,
        subject_id=subject,
        parent_id="parent_001",
        start=DAY,
        end=DAY,
        now=NOW,
        timezone="UTC",
    )
    query, params = session.execute.call_args.args
    assert "air_health.measurements_current" in str(query)
    assert "subject_id = :subject_id" in str(query)
    assert subject not in str(query) and params["subject_id"] == subject
    assert "source = 'google_health'" in str(query)
    assert "coverage_end_local_date" in str(query)
    assert result[0].steps is None  # cross-subject row is rejected even after the SQL filter


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://localhost:8000",
        "https://api.example.test/base",
    ],
)
def test_api_url_allows_loopback_or_https(url):
    assert sync_tiger.validate_api_url(url + "/") == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test",
        "http://192.168.1.2",
        "ftp://localhost",
        "https://name:secret@example.test",
        "https://example.test?token=secret",
        "http://localhost.evil.test",
        "https://example.test:99999",
    ],
)
def test_api_url_rejects_insecure_or_credential_bearing_destinations(url):
    with pytest.raises(sync_tiger.SyncError):
        sync_tiger.validate_api_url(url)


def test_success_digest_skips_unchanged_payload_and_reposts_changed_null():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["X-FarCare-Ingest-Key"] == KEY
        assert request.extensions["timeout"]["connect"] == 10
        body = json.loads(request.content)
        return httpx.Response(200, json={"accepted": len(body), "changed": len(body)})

    state = sync_tiger.SyncState()
    observations = project([record()])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = sync_tiger.publish_if_changed(
            client, "http://localhost", observations, ingest_key=KEY, state=state
        )
        repeat = sync_tiger.publish_if_changed(
            client, "http://localhost", observations, ingest_key=KEY, state=state
        )
        corrected = sync_tiger.publish_if_changed(
            client, "http://localhost", project([]), ingest_key=KEY, state=state
        )
    assert first.published and not repeat.published and corrected.published
    assert len(requests) == 2
    assert json.loads(requests[-1].content)[0]["steps"] is None


def test_partial_batch_failure_does_not_advance_digest_and_replay_is_bounded():
    sizes = []
    fail = True

    def handler(request):
        nonlocal fail
        size = len(json.loads(request.content))
        sizes.append(size)
        if size == 1 and fail:
            fail = False
            return httpx.Response(500, text="sensitive response must never be logged")
        return httpx.Response(200, json={"accepted": size, "changed": size})

    obs = project([record()])[0]
    observations = [obs.model_copy(update={"observation_id": f"batch_{i}"}) for i in range(1001)]
    state = sync_tiger.SyncState()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(sync_tiger.SyncError, match="HTTP 500") as error:
            sync_tiger.publish_if_changed(
                client, "http://localhost", observations, ingest_key=KEY, state=state
            )
        assert "sensitive" not in str(error.value) and state.successful_digest is None
        result = sync_tiger.publish_if_changed(
            client, "http://localhost", observations, ingest_key=KEY, state=state
        )
    assert sizes == [1000, 1, 1000, 1] and result.accepted == 1001
    assert state.successful_digest is not None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(307, headers={"Location": "https://other.test"}),
        httpx.Response(200, json={"accepted": 0}),
        httpx.Response(200, text="private malformed body"),
    ],
)
def test_redirects_and_invalid_receipts_never_advance_cursor(response):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return response

    state = sync_tiger.SyncState()
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(sync_tiger.SyncError):
            sync_tiger.publish_if_changed(
                client, "http://localhost", project([]), ingest_key=KEY, state=state
            )
    assert count == 1 and state.successful_digest is None


def test_missing_ingest_key_fails_before_reading_database(monkeypatch, capsys):
    monkeypatch.setattr(sync_tiger, "get_settings", lambda: SimpleNamespace(ingest_api_key=""))
    read = Mock(side_effect=AssertionError("must not read database"))
    monkeypatch.setattr(sync_tiger, "read_projection", read)
    assert (
        sync_tiger.main(
            ["--subject-id", "wearer", "--parent-id", "parent_001", "--api-url", "http://localhost"]
        )
        == 1
    )
    assert "INGEST_API_KEY" in capsys.readouterr().err
    read.assert_not_called()


def test_watch_requeries_before_digest_check_and_publishes_corrections(monkeypatch):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json={"accepted": len(body), "changed": len(body)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(sync_tiger.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(sync_tiger, "get_settings", lambda: SimpleNamespace(ingest_api_key=KEY))
    original = project([record()])
    read = Mock(side_effect=[original, original, project([])])
    monkeypatch.setattr(sync_tiger, "read_projection", read)
    monkeypatch.setattr(sync_tiger.time, "sleep", Mock(side_effect=[None, None, KeyboardInterrupt]))
    result = sync_tiger.main(
        [
            "--subject-id",
            "wearer",
            "--parent-id",
            "parent_001",
            "--api-url",
            "http://localhost",
            "--watch-seconds",
            "60",
        ]
    )
    assert result == 0 and read.call_count == 3
    assert [body[0]["steps"] for body in bodies] == [100, None]


def test_no_payload_does_not_advance_unacknowledged_state():
    state = sync_tiger.SyncState()
    client = Mock()
    result = sync_tiger.publish_if_changed(
        client, "http://localhost", [], ingest_key=KEY, state=state
    )
    assert not result.published and state.successful_digest is None
    client.post.assert_not_called()


def test_connection_errors_are_sanitized():
    def handler(request):
        raise httpx.ConnectError("private connection information")

    state = sync_tiger.SyncState()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(sync_tiger.SyncError) as error:
            sync_tiger.publish_if_changed(
                client, "http://localhost", project([]), ingest_key=KEY, state=state
            )
    assert "private" not in str(error.value) and state.successful_digest is None


def test_real_ingest_handoff_idempotency_and_share_health_privacy(client, monkeypatch):
    assert client.post("/api/demo/reset?include_health=true", headers=ADMIN).status_code == 200
    monkeypatch.setattr(get_settings(), "ingest_api_key", KEY)
    observations = project([record(), record("resting_heart_rate_bpm", value=62)])
    first = sync_tiger.publish_if_changed(
        client, "http://localhost", observations, ingest_key=KEY, state=sync_tiger.SyncState()
    )
    replay = sync_tiger.publish_if_changed(
        client, "http://localhost", observations, ingest_key=KEY, state=sync_tiger.SyncState()
    )
    assert first.changed == 1 and replay.changed == 0
    health = client.get("/api/parents/parent_001/health", headers=CHILD).json()
    assert health["data_mode"] == "live"
    assert health["observations"][0]["sleep_minutes"] is None
    timeline = client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
    assert len([e for e in timeline if e["kind"] == "health.synced"]) == 1
    with session_scope() as session:
        session.query(FamilyLink).one().share_health = False
    assert client.get("/api/parents/parent_001/health", headers=CHILD).json() is None
    assert not any(
        e["kind"] == "health.synced"
        for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
    )
