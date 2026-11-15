"""Sabal -> Sumit handoff: the mock Fitbit sender's payloads are contract-valid and the
backend acknowledges them, records a timeline event, and respects sharing permissions."""

import importlib.util
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import session_scope
from app.models import FamilyLink
from app.schemas.contracts import HealthObservation
from tests.conftest import ADMIN, CHILD


def load_sender():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mock_fitbit_sender.py"
    spec = importlib.util.spec_from_file_location("mock_fitbit_sender", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scenario", ["decline", "normal", "recovering"])
def test_sender_payload_matches_contract(scenario):
    sender = load_sender()
    payload = sender.backfill("parent_001", 35, scenario)
    assert len(payload) == 35 and len({p["observation_id"] for p in payload}) == 35
    for item in payload:
        obs = HealthObservation.model_validate(item)  # extra fields would be rejected
        assert obs.data_mode == "simulated" and obs.source == "fitbit"
    # deterministic per day, so re-sending upserts instead of duplicating
    assert sender.backfill("parent_001", 35, scenario) == payload


def fresh_health(client):
    r = client.post("/api/demo/reset?include_health=true", headers=ADMIN)
    assert r.status_code == 200
    assert client.get("/api/parents/parent_001/health", headers=CHILD).json()["observations"] == []


def test_backend_receives_decline_and_flags_checkin(client):
    fresh_health(client)
    sender = load_sender()
    r = client.post("/api/health/observations", json=sender.backfill("parent_001", 35, "decline"))
    receipt = r.json()
    assert r.status_code == 200 and receipt["accepted"] == 35 and receipt["days_received"] == 35
    assert receipt["data_mode"] == "simulated" and receipt["suggest_checkin"] is True
    assert any("steps" in c and "lower" in c for c in receipt["notable_changes"])

    health = client.get("/api/parents/parent_001/health", headers=CHILD).json()
    assert len(health["observations"]) == 35 and health["data_mode"] == "simulated"
    [event] = [
        e
        for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
        if e["kind"] == "health.synced"
    ]
    assert event["summary"].startswith("Received 35 days of activity data (simulated)")

    # Re-sending the same days is an upsert: no duplicates, no new timeline noise.
    again = client.post(
        "/api/health/observations", json=sender.backfill("parent_001", 35, "decline")
    ).json()
    assert again["accepted"] == 35 and again["changed"] == 0
    health = client.get("/api/parents/parent_001/health", headers=CHILD).json()
    assert len(health["observations"]) == 35
    synced = [
        e
        for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
        if e["kind"] == "health.synced"
    ]
    assert len(synced) == 1


def test_normal_week_is_not_flagged(client):
    fresh_health(client)
    sender = load_sender()
    receipt = client.post(
        "/api/health/observations", json=sender.backfill("parent_001", 35, "normal")
    ).json()
    assert receipt["suggest_checkin"] is False and receipt["notable_changes"] == []


def test_health_sync_event_hidden_without_permission(client):
    with session_scope() as s:
        s.query(FamilyLink).one().share_health = False
    sender = load_sender()
    client.post("/api/health/observations", json=sender.backfill("parent_001", 3, "normal"))
    kinds = [
        e["kind"] for e in client.get("/api/parents/parent_001/timeline", headers=CHILD).json()
    ]
    assert "health.synced" not in kinds


def test_console_override_keeps_messaging_simulated(client, monkeypatch):
    from app.integrations.messaging import channel_name

    s = get_settings()
    monkeypatch.setattr(s, "photon_api_key", "pho_sk_test")
    monkeypatch.setattr(s, "photon_project_id", "pho_prj_test")
    assert channel_name() == "photon"
    monkeypatch.setattr(s, "messaging_channel", "console")
    assert channel_name() == "console"
    assert client.get("/health").json()["messaging"] == "console"


def test_console_page_and_feeds(client, monkeypatch):
    page = client.get("/console")
    assert page.status_code == 200 and "FarCare Demo Console" in page.text
    assert '"dev-admin-token"' in page.text  # pre-filled only for dev tokens outside production

    from tests.conftest import say

    say(client, "parent_001", "hello")
    msgs = client.get("/api/demo/messages", headers=ADMIN).json()
    assert [m["direction"] for m in msgs] == ["in", "out"]
    assert msgs[0]["person_id"] == "parent_001" and msgs[0]["text"] == "hello"

    receipt = client.post("/api/demo/health-sync", json={"scenario": "decline"}, headers=ADMIN)
    assert receipt.status_code == 200 and receipt.json()["accepted"] == 35
    assert client.post("/api/demo/health-sync", json={}, headers=CHILD).status_code == 403

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert client.get("/console").status_code == 404
