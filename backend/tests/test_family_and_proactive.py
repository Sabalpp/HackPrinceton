"""Family-initiated help (parent consent first), family concern checks, proactive check-ins."""

from datetime import timedelta

from app.config import get_settings
from app.db import session_scope, utcnow
from app.models import FamilyLink, SafetyIncidentRow
from app.services.background import run_tick
from tests.conftest import ADMIN, CHILD, say, texts_to


def requests(client):
    return client.get("/api/parents/parent_001/care-requests", headers=CHILD).json()


def incidents(client):
    return client.get("/api/parents/parent_001/incidents", headers=CHILD).json()


# --------------------------------------------------------------------------- family requests


def test_child_request_needs_parent_consent_then_coordinates(client):
    out = say(client, "child_001", "Can someone help Mom with groceries tomorrow?")
    [consent] = texts_to(out, "parent")
    assert consent.startswith("Hi Sita, Aarav asked me to arrange help with groceries")
    assert "Reply YES or NO" in consent
    assert any("asked Mom first" in t for t in texts_to(out, "child"))
    assert texts_to(out, "responder") == []  # nobody contacted before the parent agrees
    [req] = requests(client)
    assert req["status"] == "awaiting_approval"
    assert req["requested_by_id"] == "child_001" and req["requested_by_name"] == "Aarav"

    out = say(client, "parent_001", "yes")
    assert texts_to(out, "responder")  # Asha is asked now
    assert any("Mom said yes" in t for t in texts_to(out, "child"))

    out = say(client, "responder_001", "Yes, I can go at 4pm")
    assert any("Asha" in t and "accepted" in t for t in texts_to(out, "child"))
    assert requests(client)[0]["status"] == "accepted"


def test_parent_can_refuse_child_request(client):
    say(client, "child_001", "Could someone pick up Mom's medicine today?")
    out = say(client, "parent_001", "No thanks")
    assert requests(client)[0]["status"] == "cancelled"
    assert any("Mom would rather not" in t for t in texts_to(out, "child"))
    assert texts_to(out, "responder") == []


def test_child_status_respects_sharing_permissions(client):
    say(client, "parent_001", "I've been so tired lately")  # creates a check-in
    out = say(client, "child_001", "status")
    [reply] = texts_to(out, "child")
    assert "Latest check-in" in reply and "simulated data" in reply

    with session_scope() as s:
        link = s.query(FamilyLink).one()
        link.share_checkins = False
        link.share_health = False
    out = say(client, "child_001", "status")
    [reply] = texts_to(out, "child")
    assert "check-in" not in reply.lower() and "Activity" not in reply


# --------------------------------------------------------------------------- family concern


def test_family_concern_check_resolves_when_parent_is_ok(client):
    out = say(client, "child_001", "Mom isn't answering her phone, can someone check on her?")
    [check] = texts_to(out, "parent")
    assert "Aarav asked me to check on you" in check and "Reply OK" in check
    [inc] = incidents(client)
    assert inc["trigger"] == "family_concern" and inc["status"] == "checking"

    out = say(client, "parent_001", "I'm fine, my phone was charging")
    assert incidents(client)[0]["resolution"] == "parent_ok"
    assert any("replied that they're okay" in t for t in texts_to(out, "child"))


def test_family_concern_escalates_without_reply(client):
    say(client, "child_001", "I haven't heard from Mom all day, I'm worried")
    with session_scope() as s:
        inc = s.query(SafetyIncidentRow).one()
        inc.check_deadline = utcnow() - timedelta(seconds=1)
    run_tick()
    [inc] = incidents(client)
    assert inc["status"] == "escalated" and inc["escalation_reason"] == "no_response"
    outbox = client.get("/api/demo/outbox", headers=ADMIN).json()
    alert = next(m["text"] for m in outbox if m["to_role"] == "child" and "URGENT" in m["text"])
    assert "did not answer a check-in that family asked for" in alert
    assert any(m["to"] == "Asha" and "URGENT" in m["text"] for m in outbox)


# --------------------------------------------------------------------------- proactive


def enable_proactive(monkeypatch, hours="0-24"):
    s = get_settings()
    monkeypatch.setattr(s, "proactive_checkins_enabled", True)
    monkeypatch.setattr(s, "proactive_checkin_hours", hours)


def outbox_to(client, role):
    return [m for m in client.get("/api/demo/outbox", headers=ADMIN).json() if m["to_role"] == role]


def test_proactive_checkin_once_per_day_and_non_diagnostic(client, monkeypatch):
    enable_proactive(monkeypatch)
    run_tick()
    run_tick()
    [msg] = outbox_to(client, "parent")
    assert "How are you feeling today?" in msg["text"]
    assert "less active than usual" in msg["text"] and "simulated" in msg["text"]
    assert not any(w in msg["text"].lower() for w in ("diagnos", "disease", "depress"))
    kinds = [
        e["kind"] for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
    ]
    assert "checkin.prompted" in kinds


def test_proactive_checkin_respects_quiet_rules(client, monkeypatch):
    enable_proactive(monkeypatch, hours="0-0")  # outside the daytime window
    run_tick()
    assert outbox_to(client, "parent") == []

    enable_proactive(monkeypatch)
    say(client, "parent_001", "hello")  # parent is chatting right now: don't interrupt
    before = len(outbox_to(client, "parent"))
    run_tick()
    assert len(outbox_to(client, "parent")) == before

    monkeypatch.setattr(get_settings(), "proactive_checkins_enabled", False)
    r = client.post("/api/demo/checkin", json={"parent_id": "parent_001"}, headers=ADMIN)
    assert r.json()["sent"] is True  # demo trigger still works on demand


def test_proactive_reply_is_understood_in_context(client, monkeypatch):
    enable_proactive(monkeypatch)
    run_tick()
    out = say(client, "parent_001", "I'm feeling a bit tired honestly")
    assert any("sleeping" in t for t in texts_to(out, "parent"))
    ov = client.get("/api/parents/parent_001/overview", headers=CHILD).json()
    assert ov["latest_checkin"]["energy"] == "low"
