"""SMS contracts, consent and delivery evidence. Every provider call is replaced locally."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.config import Settings, get_settings
from app.db import session_scope, utcnow
from app.integrations import delivery, messaging
from app.integrations.photon import client as photon
from app.integrations.photon.inbound import from_message
from app.models import CareRequestRow, InboundMessageRow, OutboundMessage, Person, SafetyIncidentRow
from app.runtime_models import DeliveryJob
from tests.test_photon_client import AGENT, SPACE, FakeProjects, _load_setup_script
from tests.test_webhook_and_agent import SECRET, photon_event, signed

PROJECT = "pho_prj_test"


@pytest.fixture
def sms(client, monkeypatch):
    settings = get_settings()
    for key, value in {
        "photon_api_key": "offline-test-key",
        "photon_project_id": PROJECT,
        "photon_webhook_secret": SECRET,
        "photon_platform": "sms",
        "photon_agent_user_id": AGENT,
        "messaging_channel": "photon",
    }.items():
        monkeypatch.setattr(settings, key, value)
    return client


def event(mid, text, handle="+15550100003"):
    value = photon_event(mid, handle, text)
    value["source"] = "sms.photon"
    obj = value["data"]["object"]
    for snapshot in (obj, obj["sentBy"], obj["receivedBy"], obj["space"]):
        snapshot.update(platform="sms", projectId=PROJECT)
    obj["receivedBy"]["id"] = AGENT
    obj["space"]["id"] = SPACE + handle[-2:]
    return value


def post(client, value):
    body = json.dumps(value).encode()
    result = client.post("/webhooks/photon", content=body, headers=signed(body, value["id"]))
    assert result.status_code == 204, result.text


def route(person_id="parent_001"):
    with session_scope() as session:
        person = session.get(Person, person_id)
        person.photon_platform = "sms"
        person.photon_space_id = SPACE
        person.photon_agent_user_id = AGENT
        person.reachable_since = utcnow()
        session.add(person)


def queue(key="sms-alert", person_id="parent_001"):
    with session_scope() as session:
        return messaging.send_to_person(
            session, session.get(Person, person_id), "Please review your FarCare check-in.", key
        )


def receipt(status, provider_id="provider-message", code=None, suffix=""):
    value = event("unused", "unused", "+15550100001")
    value["id"] = f"evt_{status}{suffix}"
    value["type"] = f"message.{status}"
    obj = value["data"]["object"]
    obj.update(id=provider_id, status=status)
    obj["sentBy"] = obj.pop("receivedBy")
    obj["space"]["id"] = SPACE
    if code:
        obj["failure"] = {"code": code, "message": "Private provider detail must not be stored"}
    return value


def test_sms_requires_explicit_sender_configuration():
    with pytest.raises(ValidationError, match="PHOTON_AGENT_USER_ID"):
        Settings(
            _env_file=None, photon_platform="sms", photon_api_key="test", photon_project_id=PROJECT
        )


def test_sms_join_learns_valid_route_and_deduplicates(sms):
    value = event("sms-join", "JOIN")
    post(sms, value)
    post(sms, value)
    with session_scope() as session:
        person = session.get(Person, "responder_001")
        assert person.photon_platform == "sms" and person.photon_agent_user_id == AGENT
        assert messaging.photon_routed(person)
        assert session.get(InboundMessageRow, "sms-join").status == "done"
        rows = session.exec(select(OutboundMessage)).all()
        assert len(rows) == 1 and rows[0].status == "queued"


@pytest.mark.parametrize(
    "change", ["other_agent", "other_project", "missing_project", "other_platform", "group", "html"]
)
def test_sms_rejects_wrong_channel_sender_project_and_content(sms, change):
    value = event("rejected-route", "JOIN")
    obj = value["data"]["object"]
    if change == "other_agent":
        obj["receivedBy"]["id"] = "different-agent"
    elif change == "other_project":
        obj["space"]["projectId"] = "another-project"
    elif change == "missing_project":
        del value["data"]["projectId"]
    elif change == "other_platform":
        obj["receivedBy"]["platform"] = "imessage"
    elif change == "group":
        obj["space"]["kind"] = "group"
    else:
        obj["content"]["format"] = "html"
    post(sms, value)
    with session_scope() as session:
        assert session.get(Person, "responder_001").photon_space_id is None
        assert session.exec(select(OutboundMessage)).all() == []


def test_sms_unknown_sender_does_not_create_account_or_reply(sms):
    post(sms, event("unknown", "Tell me about Sita", "+15550109998"))
    with session_scope() as session:
        assert session.get(InboundMessageRow, "unknown").status == "ignored"
        assert session.exec(select(OutboundMessage)).all() == []
        assert session.exec(select(Person).where(Person.handle == "+15550109998")).first() is None


def test_sms_stop_cancels_pending_alerts_and_start_does_not_replay_them(sms):
    route("responder_001")
    queue("pending-before-stop", "responder_001")
    post(sms, event("stop", "STOP"))
    with session_scope() as session:
        person = session.get(Person, "responder_001")
        assert person.messaging_opted_out_at is not None and not messaging.photon_routed(person)
        assert session.get(DeliveryJob, "pending-before-stop").status == "cancelled"
        assert session.get(OutboundMessage, "reply:stop") is None
    post(sms, event("ordinary-after-stop", "hi"))
    with session_scope() as session:
        assert session.get(InboundMessageRow, "ordinary-after-stop").status == "ignored"
    post(sms, event("restart", "START"))
    with session_scope() as session:
        assert messaging.photon_routed(session.get(Person, "responder_001"))
        assert session.get(OutboundMessage, "reply:restart").status == "queued"
        assert session.get(DeliveryJob, "pending-before-stop").status == "cancelled"


def test_sms_help_is_deterministic_and_does_not_call_ai(sms, monkeypatch):
    from app.integrations.gemini import family_agent

    monkeypatch.setattr(family_agent, "respond", lambda *args: pytest.fail("HELP must not use AI"))
    post(sms, event("help", "HELP", "+15550100002"))
    with session_scope() as session:
        msg = session.get(OutboundMessage, "reply:help")
        assert "STOP" in msg.text and "emergency services" in msg.text
        assert msg.status == "queued"


def test_sms_help_during_parent_safety_check_keeps_emergency_meaning(sms, monkeypatch):
    from app.integrations.gemini import agent
    from app.services import safety

    monkeypatch.setattr(agent, "respond", lambda *args: pytest.fail("Safety reply must not use AI"))
    for person_id in ("parent_001", "child_001", "responder_001"):
        route(person_id)
    with session_scope() as session:
        incident = safety.open_family_concern(
            session, session.get(Person, "parent_001"), session.get(Person, "child_001")
        )
        incident_id = incident.id
        assert incident.status == "checking"
    post(sms, event("safety-help", "HELP", "+15550100001"))
    with session_scope() as session:
        incident = session.get(SafetyIncidentRow, incident_id)
        assert incident.status != "checking" and incident.escalation_reason == "help_requested"
        assert session.get(OutboundMessage, "reply:safety-help") is None


def test_sms_dispatch_rechecks_optout_before_provider_call(sms, monkeypatch):
    route()
    queue()
    with session_scope() as session:
        person = session.get(Person, "parent_001")
        person.messaging_opted_out_at = utcnow()
        session.add(person)
    monkeypatch.setattr(
        photon, "send_text", lambda **kw: pytest.fail("Opted-out SMS must not send")
    )
    with session_scope() as session:
        assert delivery.dispatch(session, session.get(DeliveryJob, "sms-alert")) == "cancelled"
        assert session.get(OutboundMessage, "sms-alert").error == "recipient_opted_out"


def test_sms_care_conversation_requires_parent_approval(sms):
    for suffix in ("01", "02", "03", "04"):
        post(sms, event(f"join-{suffix}", "JOIN", "+155501000" + suffix))
    post(sms, event("grocery", "Can someone help me with groceries tomorrow?", "+15550100001"))
    with session_scope() as session:
        request = session.exec(select(CareRequestRow)).one()
        assert request.status == "awaiting_approval" and not request.parent_approved
    post(sms, event("parent-yes", "YES", "+15550100001"))
    with session_scope() as session:
        request = session.exec(select(CareRequestRow)).one()
        assert request.status == "sent" and request.parent_approved
    post(sms, event("neighbor-yes", "YES, 15 min"))
    post(sms, event("neighbor-done", "DONE"))
    with session_scope() as session:
        assert session.exec(select(CareRequestRow)).one().status == "completed"


def test_sms_send_uses_same_idempotent_plaintext_sdk_contract(sms, monkeypatch):
    fake = SimpleNamespace(projects=FakeProjects())
    monkeypatch.setattr(photon, "get_client", lambda: fake)
    photon.send_text(
        space_id=SPACE, sender_id=AGENT, text="Review the check-in", idempotency_key="sms-key"
    )
    assert fake.projects.sent.body.senderId == AGENT
    assert fake.projects.sent.headers.idempotency_key == "sms-key"
    with pytest.raises(ValueError, match="SMS sender"):
        photon.send_text(
            space_id=SPACE, sender_id="wrong-agent", text="blocked", idempotency_key="k"
        )
    photon.set_typing(
        space_id=SPACE, sender_id=AGENT
    )  # Fake has no typing method: must not call it.
    photon.list_received_since(utcnow())
    assert fake.projects.listed.query.model_dump(mode="json", by_alias=True)["platform"] == "sms"


def test_sms_old_imessage_route_cannot_send(sms):
    route()
    with session_scope() as session:
        person = session.get(Person, "parent_001")
        person.photon_platform = None
        session.add(person)
    assert queue().status == "unreachable"


def test_sms_delivery_receipts_are_durable_and_not_fabricated(sms, monkeypatch):
    from app import worker

    route()
    assert queue().status == "queued"
    monkeypatch.setattr(photon, "send_text", lambda **kw: "provider-message")
    with session_scope() as session:
        assert delivery.dispatch(session, session.get(DeliveryJob, "sms-alert")) == "accepted"
        assert session.get(OutboundMessage, "sms-alert").status == "accepted"
    monkeypatch.setattr(get_settings(), "app_env", "production")
    value = receipt("delivered")
    post(sms, value)
    post(sms, value)
    with session_scope() as session:
        assert session.get(OutboundMessage, "sms-alert").status == "accepted"
        assert session.get(InboundMessageRow, "photon_receipt:evt_delivered").status == "pending"
    worker.ensure_lease()
    worker.run_cycle("sms-receipt-worker")
    with session_scope() as session:
        assert session.get(OutboundMessage, "sms-alert").status == "delivered"
        assert session.get(DeliveryJob, "sms-alert").status == "delivered"
    post(sms, receipt("dispatched"))
    worker.run_cycle("sms-receipt-worker")
    with session_scope() as session:
        assert session.get(OutboundMessage, "sms-alert").status == "delivered"


def test_sms_failed_optout_receipt_cancels_other_pending_alerts(sms, monkeypatch):
    route()
    queue("sms-alert")
    monkeypatch.setattr(photon, "send_text", lambda **kw: "provider-message")
    with session_scope() as session:
        delivery.dispatch(session, session.get(DeliveryJob, "sms-alert"))
    queue("another-alert")
    post(sms, receipt("failed", code="recipient_opted_out"))
    with session_scope() as session:
        msg = session.get(OutboundMessage, "sms-alert")
        assert msg.status == "failed" and msg.error == "recipient_opted_out"
        assert session.get(DeliveryJob, "another-alert").status == "cancelled"
        assert session.get(Person, "parent_001").messaging_opted_out_at is not None


def test_sms_receipt_for_different_space_cannot_update_message(sms, monkeypatch):
    route()
    queue()
    monkeypatch.setattr(photon, "send_text", lambda **kw: "provider-message")
    with session_scope() as session:
        delivery.dispatch(session, session.get(DeliveryJob, "sms-alert"))
    value = receipt("delivered")
    value["data"]["object"]["space"]["id"] = "another-space"
    post(sms, value)
    with session_scope() as session:
        assert session.get(OutboundMessage, "sms-alert").status == "accepted"


def test_sms_status_inspects_existing_sender_without_sending(sms, monkeypatch, capsys):
    setup = _load_setup_script()
    projects = SimpleNamespace(
        list_users=lambda inp: SimpleNamespace(
            model_dump=lambda **kw: {
                "items": [
                    {
                        "id": AGENT,
                        "kind": "agent",
                        "platform": "sms",
                        "handle": "+15550109999",
                        "details": {"resourceId": "resource"},
                    }
                ],
                "nextPageToken": None,
            }
        ),
        get_sms_line_campaign_assignment=lambda inp: SimpleNamespace(
            model_dump=lambda **kw: {"eligibility": "blocked"}
        ),
    )
    monkeypatch.setattr(setup, "get_client", lambda: SimpleNamespace(projects=projects))
    setup.sms_status(PROJECT)
    text = capsys.readouterr().out
    assert "Campaign eligibility: blocked" in text and "[configured]" in text
    assert "+15550109999" not in text


def test_sms_rejects_non_phone_identity(sms):
    assert from_message(event("email", "JOIN", "test@example.com")["data"]["object"]) is None
