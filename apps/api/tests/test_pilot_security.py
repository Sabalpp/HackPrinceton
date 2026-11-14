"""Security boundaries that must hold independently of the browser UI."""

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import select
from starlette.requests import Request

from app import accounts
from app.auth import _person_for_token
from app.config import Settings, get_settings
from app.db import session_scope, utcnow
from app.experience_models import Account, LoginSession
from app.manage_accounts import create_owner, reset_password
from app.models import FamilyLink, Person, TimelineEventRow
from app.routes.parents import stream
from app.services import timeline


def test_operator_recovery_revokes_every_session(client):
    original = "synthetic long password"
    replacement = "another synthetic passphrase"
    person_id = create_owner("pilot@example.test", "Pilot", original)
    response = client.post(
        "/api/auth/login", json={"email": "pilot@example.test", "password": original}
    )
    assert response.status_code == 200
    assert response.json()["person"]["role"] == "child"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert response.headers["cache-control"] == "private, no-store"
    with session_scope() as session:
        stored = session.get(Account, person_id)
        assert original not in stored.password_hash
        assert accounts.verify_password(original, stored.password_hash)
    reset_password("pilot@example.test", replacement)
    assert client.get("/api/me").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": "pilot@example.test", "password": original}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "pilot@example.test", "password": replacement}
        ).status_code
        == 200
    )


def test_sse_rechecks_live_sharing_and_session(client):
    response = client.post("/api/auth/demo", json={"role": "child"})
    assert response.status_code == 200
    token = client.cookies.get(accounts.COOKIE)
    with session_scope() as session:
        session.exec(delete(TimelineEventRow))
        link = session.exec(select(FamilyLink).where(FamilyLink.member_id == "child_001")).first()
        link.share_health = True
        session.add(link)
        viewer = session.get(Person, "child_001")
        timeline.record(
            session, "parent_001", "health.test", "Initially shared", visibility=timeline.HEALTH
        )

    async def exercise():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/parents/parent_001/stream",
                "headers": [(b"cookie", f"{accounts.COOKIE}={token}".encode())],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
            },
            receive=receive,
        )
        response = await stream("parent_001", request, after=0, viewer=viewer)
        events = response.body_iterator
        assert "retry:" in await anext(events)
        assert "Initially shared" in await anext(events)
        with session_scope() as session:
            link = session.exec(
                select(FamilyLink).where(FamilyLink.member_id == "child_001")
            ).first()
            link.share_health = False
            session.add(link)
            timeline.record(
                session,
                "parent_001",
                "health.test",
                "Private after revocation",
                visibility=timeline.HEALTH,
            )
            timeline.record(
                session,
                "parent_001",
                "family.test",
                "Still shared coordination",
                visibility=timeline.FAMILY,
            )
        event = await anext(events)
        assert "Still shared coordination" in event
        assert "Private after revocation" not in event
        with session_scope() as session:
            row = session.get(LoginSession, accounts.hash_token(token))
            row.expires_at = utcnow() - timedelta(seconds=1)
            session.add(row)
        assert "access-revoked" in await anext(events)
        with pytest.raises(StopAsyncIteration):
            await anext(events)

    asyncio.run(exercise())


def test_production_has_no_legacy_bearer_backdoor(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with session_scope() as session, pytest.raises(HTTPException) as denied:
        _person_for_token(session, "dev-child-token")
    assert denied.value.status_code == 401
    assert client.post("/api/auth/demo", json={"role": "child"}).status_code == 404


def test_production_requires_safe_runtime_configuration(monkeypatch):
    args = dict(
        _env_file=None,
        app_env="production",
        database_url="postgresql://test@localhost/test",
        demo_mode=False,
        seed_demo_data=False,
        seed_health_data=False,
        background_tasks_enabled=False,
        ingest_api_key="test-ingest-key",
        cors_origins="https://care.example.test",
        messaging_channel="console",
    )
    assert Settings(**args).app_env == "production"
    for overrides in (
        {"demo_mode": True},
        {"seed_health_data": True},
        {"background_tasks_enabled": True},
        {"database_url": "sqlite:///test.db"},
        {"cors_origins": "http://care.example.test"},
        {"messaging_channel": "photon"},
    ):
        with pytest.raises(ValueError):
            Settings(**{**args, **overrides})


def test_login_wrong_origin_and_persistent_rate_limit(client):
    body = {"email": "unknown@example.test", "password": "synthetic password"}
    assert (
        client.post(
            "/api/auth/login", json=body, headers={"Origin": "https://unrelated.example.test"}
        ).status_code
        == 403
    )
    for _ in range(12):
        assert client.post("/api/auth/login", json=body).status_code == 401
    limited = client.post("/api/auth/login", json=body)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"
