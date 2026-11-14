"""Possible-fall / emergency workflow. Deterministic; the LLM must never be consulted."""

from datetime import timedelta

import pytest

from app.db import session_scope, utcnow
from app.models import SafetyIncidentRow
from app.services.background import run_tick
from tests.conftest import ADMIN, CHILD, say, texts_to


def fall_event(event_id="fall_001", confidence=0.91):
    return {
        "event_id": event_id,
        "parent_id": "parent_001",
        "event_type": "fall.suspected",
        "source": "simulated_imu",
        "timestamp": "2026-11-14T15:30:00Z",
        "confidence": confidence,
        "data_mode": "simulated",
    }


def incidents(client):
    return client.get("/api/parents/parent_001/incidents", headers=CHILD).json()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Any call into the companion agent during a safety flow is a bug."""
    from app.integrations.gemini import agent

    def boom(*a, **k):
        raise AssertionError("LLM consulted during a safety workflow")

    monkeypatch.setattr(agent, "respond", boom)


def test_fall_event_dedupe_and_parent_ok(client):
    r = client.post("/api/events/fall", json=fall_event())
    assert r.status_code == 200 and r.json()["duplicate"] is False
    incident_id = r.json()["incident_id"]
    [inc] = incidents(client)
    assert inc["status"] == "checking" and inc["data_mode"] == "simulated"

    again = client.post("/api/events/fall", json=fall_event()).json()
    assert again["duplicate"] is True and again["incident_id"] == incident_id
    merged = client.post("/api/events/fall", json=fall_event("fall_002")).json()
    assert merged["duplicate"] is True and merged["incident_id"] == incident_id
    assert len(incidents(client)) == 1

    out = say(client, "parent_001", "I'm okay, just dropped my phone")
    assert incidents(client)[0]["status"] == "resolved"
    assert incidents(client)[0]["resolution"] == "parent_ok"
    assert any("okay" in t for t in texts_to(out, "child"))
    assert texts_to(out, "responder") == []


def test_no_response_escalates_to_family_and_responder(client):
    client.post("/api/events/fall", json=fall_event())
    with session_scope() as s:  # fast-forward past the safety-check deadline
        inc = s.query(SafetyIncidentRow).one()
        inc.check_deadline = utcnow() - timedelta(seconds=1)
    run_tick()
    [inc] = incidents(client)
    assert inc["status"] == "escalated" and inc["escalation_reason"] == "no_response"

    outbox = client.get("/api/demo/outbox", headers=ADMIN).json()
    child_alert = next(m["text"] for m in outbox if m["to_role"] == "child")
    assert child_alert.startswith("URGENT") and "102" in child_alert and "Mom" in child_alert
    assert "simulated" in child_alert.lower()
    responder_ask = next(m for m in outbox if m["to_role"] == "responder")
    assert responder_ask["to"] == "Asha" and "URGENT" in responder_ask["text"]

    say(client, "responder_001", "YES 10")
    assert incidents(client)[0]["status"] == "responder_en_route"
    say(client, "responder_001", "Done, she's okay")
    [inc] = incidents(client)
    assert inc["status"] == "resolved" and inc["resolution"] == "responder_confirmed"


def test_help_reply_escalates_and_decline_moves_to_next_responder(client):
    client.post("/api/events/fall", json=fall_event())
    say(client, "parent_001", "HELP")
    assert incidents(client)[0]["escalation_reason"] == "help_requested"
    out = say(client, "responder_001", "No, I'm out of town")
    assert any(m["to"] == "Ram" and "URGENT" in m["text"] for m in out)


def test_emergency_phrase_escalates_without_llm(client):
    out = say(client, "parent_001", "I fell in the bathroom and can't get up")
    [inc] = incidents(client)
    assert inc["status"] == "escalated"
    assert inc["escalation_reason"] == "parent_reported_emergency"
    assert any("can't get up" in t for t in texts_to(out, "child"))


def test_family_can_resolve_and_responder_is_stood_down(client):
    client.post("/api/events/fall", json=fall_event())
    say(client, "parent_001", "help")
    inc_id = incidents(client)[0]["incident_id"]
    r = client.post(f"/api/parents/parent_001/incidents/{inc_id}/resolve", headers=CHILD)
    assert r.status_code == 200 and r.json()["resolution"] == "family_resolved"
    outbox = client.get("/api/demo/outbox", headers=ADMIN).json()
    assert any(m["to"] == "Asha" and "No need to go" in m["text"] for m in outbox)


def test_demo_fall_replay_runs_detector(client):
    walk = client.post(
        "/api/demo/fall", json={"parent_id": "parent_001", "trace": "walking"}, headers=ADMIN
    ).json()
    assert walk["detection"]["label"] == "walking" and walk["ingest"] is None
    fall = client.post(
        "/api/demo/fall", json={"parent_id": "parent_001", "trace": "fall"}, headers=ADMIN
    ).json()
    assert fall["detection"]["label"] == "possible_fall"
    assert fall["ingest"]["incident_id"]
