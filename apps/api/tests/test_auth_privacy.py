from app.auth import hash_token
from app.db import session_scope
from app.models import FamilyLink, Person
from tests.conftest import ADMIN, CHILD, PARENT


def test_requires_token(client):
    assert client.get("/api/parents/parent_001/overview").status_code == 401
    assert (
        client.get(
            "/api/parents/parent_001/overview", headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )


def test_me_lists_only_linked_parents(client):
    me = client.get("/api/me", headers=CHILD).json()
    assert me["person"]["role"] == "child" and me["parent_ids"] == ["parent_001"]


def test_child_cannot_read_unlinked_parent(client):
    with session_scope() as s:
        s.add(Person(id="parent_999", role="parent", display_name="Other"))
    r = client.get("/api/parents/parent_999/overview", headers=CHILD)
    assert r.status_code == 404  # no existence leak


def test_parent_permissions_hide_health_and_checkins(client):
    with session_scope() as s:
        link = s.query(FamilyLink).one()
        link.share_health = False
        link.share_checkins = False
    ov = client.get("/api/parents/parent_001/overview", headers=CHILD).json()
    assert ov["health"] is None and ov["latest_checkin"] is None
    assert ov["permissions"] == {"share_health": False, "share_checkins": False}
    assert client.get("/api/parents/parent_001/health", headers=CHILD).json() is None
    # The parent always sees their own data.
    assert client.get("/api/parents/parent_001/overview", headers=PARENT).json()["health"]


def test_demo_routes_require_admin(client):
    body = {"person_id": "parent_001", "text": "hi"}
    assert client.post("/api/demo/inbound", json=body, headers=CHILD).status_code == 403
    assert client.post("/api/demo/inbound", json=body, headers=ADMIN).status_code == 200


def test_ingest_key_enforced_when_configured(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ingest_api_key", "secret-key")
    obs = [
        {
            "observation_id": "h1",
            "parent_id": "parent_001",
            "source": "fitbit",
            "data_mode": "live",
            "timestamp": "2026-11-14T00:00:00Z",
            "steps": 1200,
        }
    ]
    assert client.post("/api/health/observations", json=obs).status_code == 401
    ok = client.post(
        "/api/health/observations", json=obs, headers={"X-FarCare-Ingest-Key": "secret-key"}
    )
    assert ok.status_code == 200 and ok.json() == {"accepted": 1}


def test_ingest_rejects_unknown_parent_and_bad_payload(client):
    bad = {
        "event_id": "x",
        "parent_id": "nobody",
        "event_type": "fall.suspected",
        "source": "simulated_imu",
        "timestamp": "2026-11-14T15:30:00Z",
        "confidence": 0.9,
        "data_mode": "simulated",
    }
    assert client.post("/api/events/fall", json=bad).status_code == 404
    bad.update(parent_id="parent_001", confidence=1.7)
    assert client.post("/api/events/fall", json=bad).status_code == 422


def test_tokens_are_stored_hashed(client):
    with session_scope() as s:
        child = s.get(Person, "child_001")
        assert child.api_token_hash == hash_token("dev-child-token")
        assert "dev-child-token" not in child.api_token_hash


def test_system_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["database"] == "ok"
    assert h["messaging"] == "console" and h["ai"] == "fallback"
