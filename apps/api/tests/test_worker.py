"""Durability checks against isolated SQLite or CI TEST_DATABASE_URL, never live providers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import worker
from app.config import get_settings
from app.db import get_engine, session_scope, utcnow
from app.experience_models import Account
from app.integrations import delivery, messaging
from app.integrations.photon import client as photon
from app.integrations.photon.inbound import InboundText
from app.models import (
    CareCircleMember,
    CareRequestRow,
    ConversationTurn,
    FamilyLink,
    InboundMessageRow,
    OutboundMessage,
    Person,
    SafetyIncidentRow,
)
from app.runtime_models import AgentLease, DeliveryJob, InboundRetry
from app.schemas.contracts import AssistanceCategory
from app.services import care_requests, conversation


@pytest.fixture
def routed(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "photon_api_key", "test-only-key")
    monkeypatch.setattr(settings, "photon_project_id", "test-only-project")
    monkeypatch.setattr(settings, "messaging_channel", "photon")
    with session_scope() as session:
        person = session.get(Person, "parent_001")
        person.photon_space_id = "test-space"
        person.photon_agent_user_id = "test-agent"
        person.reachable_since = utcnow()
        session.add(person)
    worker.ensure_lease()
    return client


def queue(key="job-1", text="A synthetic test message"):
    with session_scope() as session:
        return messaging.send_to_person(session, session.get(Person, "parent_001"), text, key)


def make_due(key="job-1"):
    with session_scope() as session:
        job = session.get(DeliveryJob, key)
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
        session.add(job)


def test_enqueue_waits_for_commit_and_rollback_discards_intent(routed, monkeypatch):
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(RuntimeError, match="rollback"):
        with session_scope() as session:
            msg = messaging.send_to_person(
                session, session.get(Person, "parent_001"), "Synthetic", "rollback-job"
            )
            assert msg.status == "queued"
            assert messaging.delivered(msg)  # coordination acceptance, not device receipt
            session.flush()
            assert calls == []
            raise RuntimeError("rollback")
    with session_scope() as session:
        assert session.get(OutboundMessage, "rollback-job") is None
        assert session.get(DeliveryJob, "rollback-job") is None


def test_duplicate_intent_and_repeated_cycles_send_one_request(routed, monkeypatch):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        return "test-provider-message"

    monkeypatch.setattr(photon, "send_text", send)
    queue()
    queue(text="Must not replace the original intent")
    assert calls == []
    worker.run_cycle("first-process")
    worker.run_cycle("second-process")
    assert len(calls) == 1
    assert calls[0]["text"] == "A synthetic test message"
    with session_scope() as session:
        assert session.get(OutboundMessage, "job-1").status == "accepted"
        job = session.get(DeliveryJob, "job-1")
        assert job.attempts == 1 and job.status == "accepted"
        assert job.accepted_at is not None


def test_provider_acceptance_before_crash_replays_identical_request(routed, monkeypatch):
    calls = []
    accepted = {}

    def provider(**kwargs):
        calls.append(kwargs)
        return accepted.setdefault(kwargs["idempotency_key"], "same-provider-message")

    monkeypatch.setattr(photon, "send_text", provider)
    queue()
    with pytest.raises(RuntimeError, match="crash after provider acceptance"):
        with worker.locked_session("crashed-worker") as (session, _):
            delivery.dispatch(session, session.get(DeliveryJob, "job-1"))
            raise RuntimeError("crash after provider acceptance")
    with session_scope() as session:
        assert session.get(DeliveryJob, "job-1").attempts == 0
        assert session.get(DeliveryJob, "job-1").status == "pending"
    worker.delivery_unit("restarted-worker")
    assert len(calls) == 2 and len(accepted) == 1
    assert calls[0] == calls[1]


def test_transient_failure_backoff_then_acceptance_is_sanitized(routed, monkeypatch):
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("secret-token-and-private-message-must-not-be-persisted")
        return "accepted-on-retry"

    monkeypatch.setattr(photon, "send_text", send)
    queue()
    worker.delivery_unit("worker")
    with session_scope() as session:
        job = session.get(DeliveryJob, "job-1")
        assert job.next_attempt_at > utcnow()
        assert job.status == "pending" and job.attempts == 1
        assert session.get(OutboundMessage, "job-1").error == "provider_transport_or_response_error"
    assert worker.delivery_unit("worker") == "empty"
    make_due()
    assert worker.delivery_unit("worker") == "accepted"
    assert calls[0] == calls[1]


def test_permanent_failure_stops_retrying(routed, monkeypatch):
    class BadRequest(Exception):
        status = 400

    def fail(**_):
        raise BadRequest("sensitive provider body")

    monkeypatch.setattr(photon, "send_text", fail)
    queue()
    assert worker.delivery_unit("worker") == "failed"
    assert worker.delivery_unit("worker") == "empty"
    with session_scope() as session:
        assert session.get(DeliveryJob, "job-1").last_error_code == "provider_http_400"
        assert session.get(OutboundMessage, "job-1").status == "failed"


@pytest.mark.parametrize("seconds,expected", [(60, "pending"), (600, "failed")])
def test_retry_after_is_respected_or_stopped_for_operator(routed, monkeypatch, seconds, expected):
    class RateLimited(Exception):
        status = 429
        headers = {"Retry-After": str(seconds)}

    def fail(**_):
        raise RateLimited

    monkeypatch.setattr(photon, "send_text", fail)
    queue()
    before = utcnow()
    assert worker.delivery_unit("worker") == expected
    with session_scope() as session:
        job = session.get(DeliveryJob, "job-1")
        if expected == "pending":
            assert job.next_attempt_at >= before + timedelta(seconds=seconds)
        else:
            assert job.last_error_code == "provider_retry_after_exceeds_limit"


def test_transient_failures_stop_at_attempt_limit(routed, monkeypatch):
    monkeypatch.setattr(get_settings(), "worker_max_delivery_attempts", 2)

    def fail(**_):
        raise TimeoutError("test")

    monkeypatch.setattr(photon, "send_text", fail)
    queue()
    assert worker.delivery_unit("worker") == "pending"
    make_due()
    assert worker.delivery_unit("worker") == "failed"
    with session_scope() as session:
        assert session.get(DeliveryJob, "job-1").attempts == 2


def test_missing_credentials_never_simulate_real_delivery(routed, monkeypatch):
    queue()
    monkeypatch.setattr(get_settings(), "photon_api_key", "")
    assert worker.delivery_unit("worker") == "pending"
    with session_scope() as session:
        job = session.get(DeliveryJob, "job-1")
        assert job.attempts == 0 and job.last_error_code == "photon_not_configured"
        assert session.get(OutboundMessage, "job-1").status == "queued"


def test_changed_recipient_cannot_retarget_old_request(routed, monkeypatch):
    queue()
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    with session_scope() as session:
        person = session.get(Person, "parent_001")
        person.photon_space_id = "new-space"
        session.add(person)
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []
    with session_scope() as session:
        assert session.get(OutboundMessage, "job-1").status == "cancelled"


def test_inbound_failure_rolls_back_business_effects_and_limits_retries(client, monkeypatch):
    worker.ensure_lease()
    conversation.enqueue(InboundText("inbound-failure", "+15550100001", "hello"))

    def fail(session, _message):
        session.add(ConversationTurn(person_id="parent_001", role="assistant", text="rollback"))
        session.flush()
        raise RuntimeError("sensitive response body")

    monkeypatch.setattr(conversation, "handle", fail)
    for attempt in range(1, 4):
        worker.inbound_unit("worker")
        with session_scope() as session:
            assert not session.exec(
                select(ConversationTurn).where(ConversationTurn.text == "rollback")
            ).all()
            row = session.get(InboundMessageRow, "inbound-failure")
            assert row.attempts == attempt and row.error == "handler_failed"
            assert row.status == ("failed" if attempt == 3 else "pending")
            retry = session.get(InboundRetry, row.id)
            retry.next_attempt_at = utcnow() - timedelta(seconds=1)
            session.add(retry)
    assert worker.inbound_unit("worker") == "empty"


def test_inbound_crash_keeps_work_pending_for_restart(client, monkeypatch):
    class ProcessStopped(BaseException):
        pass

    worker.ensure_lease()
    conversation.enqueue(InboundText("inbound-crash", "+15550100001", "hello"))

    def crash(session, _message):
        session.add(ConversationTurn(person_id="parent_001", role="assistant", text="uncommitted"))
        session.flush()
        raise ProcessStopped

    monkeypatch.setattr(conversation, "handle", crash)
    with pytest.raises(ProcessStopped):
        worker.inbound_unit("old-process")
    with session_scope() as session:
        row = session.get(InboundMessageRow, "inbound-crash")
        assert row.status == "pending" and row.attempts == 0
        assert not session.exec(
            select(ConversationTurn).where(ConversationTurn.text == "uncommitted")
        ).all()
    monkeypatch.setattr(conversation, "handle", lambda *_: "done")
    assert worker.inbound_unit("new-process") == "processed"
    with session_scope() as session:
        assert session.get(InboundMessageRow, "inbound-crash").status == "done"


def test_arrived_parent_ok_is_processed_before_deadline(client):
    with session_scope() as session:
        session.add(
            SafetyIncidentRow(
                id="deadline-race",
                parent_id="parent_001",
                trigger="possible_fall",
                source="simulated",
                data_mode="simulated",
                status="checking",
                check_deadline=utcnow() - timedelta(seconds=1),
            )
        )
    conversation.enqueue(InboundText("parent-ok", "+15550100001", "OK"))
    worker.run_cycle("worker")
    with session_scope() as session:
        incident = session.get(SafetyIncidentRow, "deadline-race")
        assert incident.status == "resolved" and incident.escalated_at is None


def test_backlog_defers_deadlines_and_reports_age(client, monkeypatch):
    worker.ensure_lease()
    conversation.enqueue(InboundText("waiting", "+15550100001", "OK"))
    calls = []
    monkeypatch.setattr(worker.safety, "check_overdue", lambda *_: calls.append("timeout"))
    assert worker.maintenance_unit("worker") == "deferred"
    assert calls == []
    with session_scope() as session:
        state = worker.runtime_status(session)
        assert state["inbound_pending"] == 1
        assert state["oldest_inbound_age_seconds"] >= 0
        assert state["worker_healthy"]
        assert state["delivery_receipts_verified"] is False
        assert "OK" not in str(state) and "+1555" not in str(state)


def test_postgres_mutex_excludes_competing_worker_and_releases_on_rollback(client):
    if get_engine().dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL row-lock verification runs under TEST_DATABASE_URL in CI")
    worker.ensure_lease()

    def competing():
        with pytest.raises(worker.WorkerBusy):
            with worker.locked_session("standby"):
                pytest.fail("Both workers entered the critical section")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="rolled back unit"):
            with worker.locked_session("active"):
                pool.submit(competing).result(timeout=5)
                raise RuntimeError("rolled back unit")
    with worker.locked_session("replacement") as (_, lease):
        assert lease.owner_id == "replacement"


def test_production_webhook_only_persists_and_deduplicates(client, monkeypatch):
    import json

    from tests.test_webhook_and_agent import SECRET, photon_event, signed

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "photon_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "photon_project_id", "pho_prj_test")
    calls = []
    monkeypatch.setattr(conversation, "process", lambda *_: calls.append("unsafe-inline"))
    body = json.dumps(photon_event("production-intake", "+15550100001", "hello")).encode()
    for _ in range(2):
        assert (
            client.post(
                "/webhooks/photon", content=body, headers=signed(body, "evt-production")
            ).status_code
            == 204
        )
    assert calls == []
    with session_scope() as session:
        assert session.get(InboundMessageRow, "production-intake").status == "pending"


def test_signed_invalid_json_is_bad_request(client, monkeypatch):
    from tests.test_webhook_and_agent import SECRET, signed

    monkeypatch.setattr(get_settings(), "photon_webhook_secret", SECRET)
    body = b"not valid json"
    assert (
        client.post("/webhooks/photon", content=body, headers=signed(body, "evt-bad")).status_code
        == 400
    )


def test_health_check_never_processes_jobs(client, monkeypatch, capsys):
    worker.ensure_lease()
    calls = []
    monkeypatch.setattr(worker, "run_cycle", lambda *_: calls.append("must not run"))
    assert worker.main(["--check"]) == 1
    assert calls == [] and '"worker_healthy": false' in capsys.readouterr().out
    with session_scope() as session:
        lease = session.get(AgentLease, worker.LEASE_ID)
        lease.last_success_at = utcnow()
        session.add(lease)
    assert worker.main(["--check"]) == 0


def test_api_lock_does_not_refresh_worker_liveness(client):
    worker.ensure_lease()
    stale = utcnow() - timedelta(hours=1)
    with session_scope() as session:
        lease = session.get(AgentLease, worker.LEASE_ID)
        lease.owner_id = "old-worker"
        lease.heartbeat_at = stale
        lease.last_success_at = stale
        lease.last_error_code = "prior-worker-error"
        session.add(lease)
    with worker.locked_session("api", track_heartbeat=False) as (session, _):
        session.add(ConversationTurn(person_id="parent_001", role="user", text="API operation"))
    with session_scope() as session:
        lease = session.get(AgentLease, worker.LEASE_ID)
        assert lease.owner_id == "old-worker"
        assert lease.heartbeat_at == lease.last_success_at == stale
        assert lease.last_error_code == "prior-worker-error"
        assert worker.runtime_status(session)["worker_healthy"] is False


def route_person(session, person_id):
    person = session.get(Person, person_id)
    person.photon_space_id = "test-space-" + person_id
    person.photon_agent_user_id = "test-agent"
    person.reachable_since = utcnow()
    session.add(person)
    return person


@pytest.mark.parametrize(
    "visibility,revoked",
    [
        ("health", "share_health"),
        ("checkin", "share_checkins"),
        ("emergency", "receives_emergency_alerts"),
        ("family_context", "share_health"),
    ],
)
def test_queued_family_content_rechecks_current_consent(routed, monkeypatch, visibility, revoked):
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    with session_scope() as session:
        child = route_person(session, "child_001")
        link = session.exec(select(FamilyLink).where(FamilyLink.member_id == child.id)).one()
        link.share_health = link.share_checkins = link.receives_emergency_alerts = True
        session.add(link)
        message = messaging.send_to_person(
            session,
            child,
            "Private family update",
            "consent-job",
            parent_id="parent_001",
            visibility=visibility,
        )
        assert message.status == "queued"
    with session_scope() as session:
        link = session.exec(select(FamilyLink).where(FamilyLink.member_id == "child_001")).one()
        setattr(link, revoked, False)
        session.add(link)
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []
    with session_scope() as session:
        assert session.get(DeliveryJob, "consent-job").last_error_code == "family_consent_revoked"
        assert session.get(OutboundMessage, "consent-job").status == "cancelled"


def test_deleted_family_link_cancels_generic_notification(routed, monkeypatch):
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    with session_scope() as session:
        child = route_person(session, "child_001")
        messaging.send_to_person(
            session, child, "Care update", "family-job", parent_id="parent_001", visibility="family"
        )
    with session_scope() as session:
        link = session.exec(select(FamilyLink).where(FamilyLink.member_id == "child_001")).one()
        session.delete(link)
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []


def test_deleted_recipient_account_cancels_queued_message(routed, monkeypatch):
    with session_scope() as session:
        session.add(
            Account(
                person_id="parent_001",
                email="synthetic@example.invalid",
                password_hash="synthetic-unused-hash",
            )
        )
    queue()
    with session_scope() as session:
        session.delete(session.get(Account, "parent_001"))
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []


@pytest.mark.parametrize("change", ["revoked", "reassigned", "cancelled", "category_removed"])
def test_queued_task_offer_rechecks_membership_and_assignment(routed, monkeypatch, change):
    with session_scope() as session:
        responder = route_person(session, "responder_001")
        session.add(
            CareRequestRow(
                id="queued-task",
                parent_id="parent_001",
                request_type="grocery_assistance",
                status="sent",
                responder_id=responder.id,
                parent_approved=True,
            )
        )
        session.flush()
        message = messaging.send_to_person(
            session,
            responder,
            "Please help with groceries",
            "task-job",
            parent_id="parent_001",
            visibility="responder",
            request_id="queued-task",
            offer_only=True,
        )
        assert message.status == "queued"
    with session_scope() as session:
        task = session.get(CareRequestRow, "queued-task")
        member = session.exec(
            select(CareCircleMember).where(
                CareCircleMember.parent_id == "parent_001",
                CareCircleMember.responder_id == "responder_001",
            )
        ).one()
        if change == "revoked":
            member.active = False
        elif change == "reassigned":
            task.responder_id = "responder_002"
        elif change == "category_removed":
            member.categories = []
        else:
            task.status = "cancelled"
        session.add(task)
        session.add(member)
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []


def test_registered_responder_without_handle_receives_real_in_app_task(client, monkeypatch):
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    with session_scope() as session:
        responder = Person(id="account-only", role="responder", display_name="App responder")
        session.add(responder)
        session.flush()
        session.add(
            Account(
                person_id=responder.id,
                email="app@example.invalid",
                password_hash="synthetic-unused-hash",
            )
        )
        session.add(
            CareCircleMember(
                parent_id="parent_001",
                responder_id=responder.id,
                relationship="friend",
                categories=["grocery_assistance"],
                priority=0,
            )
        )
        session.flush()
        request = care_requests.create_request(
            session,
            session.get(Person, "parent_001"),
            AssistanceCategory.grocery_assistance,
        )
        assert request.responder_id == responder.id and request.status == "awaiting_approval"
        assert care_requests.approve(session, request)
        assert request.status == "sent" and request.parent_approved
        msg = session.get(OutboundMessage, f"care:{request.id}:ask:{responder.id}")
        assert msg.channel == "in_app" and msg.status == "available"
        assert msg.provider_message_id is None
        assert responder.handle is None and responder.photon_space_id is None
        assert messaging.is_reachable(responder, session)
        assert session.get(DeliveryJob, msg.id) is None
    assert calls == []


def test_unscoped_legacy_intent_fails_closed(routed, monkeypatch):
    queue()
    with session_scope() as session:
        job = session.get(DeliveryJob, "job-1")
        job.visibility = "unscoped"
        session.add(job)
    calls = []
    monkeypatch.setattr(photon, "send_text", lambda **kwargs: calls.append(kwargs))
    assert worker.delivery_unit("worker") == "cancelled"
    assert calls == []


@pytest.mark.parametrize("health,checkins", [(False, True), (True, False)])
def test_revoked_family_permissions_exclude_old_model_history(
    client, monkeypatch, health, checkins
):
    from app.integrations.gemini import family_agent

    calls = []

    def generate(**kwargs):
        calls.append(kwargs["contents"])
        return SimpleNamespace(function_calls=[], text="Current reply", candidates=[])

    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-only")
    monkeypatch.setattr(
        family_agent,
        "_get_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=generate),
        ),
    )
    with session_scope() as session:
        child = session.get(Person, "child_001")
        parent = session.get(Person, "parent_001")
        link = session.exec(select(FamilyLink).where(FamilyLink.member_id == child.id)).one()
        link.share_health, link.share_checkins = health, checkins
        session.add(link)
        session.add(
            ConversationTurn(
                person_id=child.id, role="assistant", text="old-sensitive-health-history"
            )
        )
        session.flush()
        reply, mode = family_agent.respond(session, child, link, parent, "Current request")
        assert reply == "Current reply" and mode == "gemini"
    assert len(calls) == 1
    assert "old-sensitive-health-history" not in str(calls)
    assert "Current request" in str(calls)


@pytest.mark.parametrize("emergency", [False, True])
def test_responder_request_uses_only_parent_first_name(client, emergency):
    with session_scope() as session:
        parent = session.get(Person, "parent_001")
        parent.display_name = "Sita PrivateSurname"
        session.add(parent)
        if emergency:
            request = care_requests.create_emergency_request(
                session,
                parent,
                "synthetic-incident",
                "may need a check-in",
            )
        else:
            request = care_requests.create_request(
                session, parent, AssistanceCategory.grocery_assistance
            )
            assert care_requests.approve(session, request)
        message = session.get(OutboundMessage, f"care:{request.id}:ask:{request.responder_id}")
        assert "Sita" in message.text and "PrivateSurname" not in message.text


def test_next_ordinary_responder_requires_fresh_parent_approval(client):
    with session_scope() as session:
        parent = session.get(Person, "parent_001")
        request = care_requests.create_request(
            session, parent, AssistanceCategory.grocery_assistance
        )
        assert care_requests.approve(session, request)
        previous = request.responder_id
        care_requests.apply_responder_reply(
            session,
            request,
            session.get(Person, previous),
            "declined",
            "No",
        )
        assert request.status == "awaiting_approval" and request.responder_id != previous
        assert not request.parent_approved and request.approval_basis is None
        assert session.get(OutboundMessage, f"care:{request.id}:ask:{request.responder_id}") is None
