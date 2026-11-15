"""Hosted entrypoint checks with synthetic records and no provider traffic."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import session_scope
from app.health import live
from app.health.live_models import WatchSourceLink
from app.models import FamilyLink
from app.vercel_runtime import configure

CHILD = {"Authorization": "Bearer dev-child-token"}


def test_hosted_defaults_disable_demo_and_preserve_explicit_provider_settings():
    env = {"PHOTON_DELIVERY": "webhook", "VERCEL_GIT_COMMIT_SHA": "synthetic-revision"}
    configure(env)
    assert env["APP_ENV"] == "production"
    assert env["DEMO_MODE"] == "false"
    assert env["SEED_DEMO_DATA"] == "false"
    assert env["BACKGROUND_TASKS_ENABLED"] == "false"
    assert env["PHOTON_DELIVERY"] == "webhook"
    assert env["GIT_COMMIT"] == "synthetic-revision"
    assert "DATABASE_URL" not in env  # A real secret must be provided by the operator.


def test_vercel_production_cannot_enable_development_authentication():
    with pytest.raises(ValueError, match="APP_ENV=production"):
        configure({"VERCEL_ENV": "production", "APP_ENV": "development"})


def test_hosted_health_lifecycle_and_private_auth_responses(client):
    from app.vercel_entry import app

    with TestClient(app) as hosted:
        assert hosted.get("/backend/health").json()["database"] == "ok"
        assert hosted.get("/health").status_code == 404
        denied = hosted.get("/backend/api/parents/parent_001/sensor-health")
        assert denied.status_code == 401
        assert denied.headers["cache-control"] == "private, no-store"
        invalid = hosted.post("/backend/api/auth/login", json={"password": "private-input"})
        assert invalid.status_code == 422
        assert "private-input" not in invalid.text


def test_hosted_sensor_endpoint_refreshes_and_enforces_revoked_sharing(client, monkeypatch):
    from app.vercel_entry import app

    now = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)
    monkeypatch.setattr(live, "utcnow", lambda: now)
    readings = [78, 82]
    queries = []

    def rows(session, *, subject_id, now):
        queries.append(subject_id)
        return [
            {
                "event_id": f"synthetic-{len(queries)}",
                "subject_id": "synthetic-wearer",
                "source": "ble",
                "source_stream": "synthetic-watch",
                "metric": "heart_rate_bpm",
                "observed_at": now - timedelta(seconds=1),
                "ended_at": None,
                "local_date": now.date(),
                "value": readings.pop(0),
                "unit": "bpm",
                "kind": "sample",
                "attributes": {},
            }
        ]

    monkeypatch.setattr(live, "_query_sensor_rows", rows)
    with session_scope() as session:
        session.add(WatchSourceLink(parent_id="parent_001", subject_id="synthetic-wearer"))
    with TestClient(app) as hosted:
        url = "/backend/api/parents/parent_001/sensor-health?subject_id=someone_else"
        first = hosted.get(url, headers=CHILD)
        second = hosted.get(url, headers=CHILD)
        assert first.status_code == second.status_code == 200
        assert first.json()["live_heart_rate"]["value"] == 78
        assert second.json()["live_heart_rate"]["value"] == 82
        assert second.json()["live_heart_rate"]["freshness"] == "fresh"
        assert second.headers["cache-control"] == "private, no-store"
        assert queries == ["synthetic-wearer", "synthetic-wearer"]
        with session_scope() as session:
            link = session.exec(
                select(FamilyLink).where(
                    FamilyLink.parent_id == "parent_001", FamilyLink.member_id == "child_001"
                )
            ).one()
            link.share_health = False
            session.add(link)
        # Existing contract: a family member retains access to their parent's
        # profile but receives no sensor report when health sharing is revoked.
        assert hosted.get(url, headers=CHILD).json() is None
        assert len(queries) == 2  # Revocation is checked before looking up Tiger readings.
