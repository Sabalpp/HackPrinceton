"""Synthetic endpoint tests for authenticated, consent-bound three-role care.

Uses the existing temporary SQLite TestClient fixture. No real contacts, provider
keys, health exports, network requests, or externally delivered messages are used.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from app import accounts
from app.auth import hash_token
from app.config import get_settings
from app.db import session_scope, utcnow
from app.experience_models import Account, BloodPressureReading, Invitation, LoginSession
from app.models import (
    CareRequestRow,
    CheckInRow,
    FamilyLink,
    HealthObservationRow,
    OutboundMessage,
    Person,
    SafetyIncidentRow,
)

PASSWORD = "Synthetic test password 42!"
ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def isolated_experience_config(monkeypatch):
    for key, value in {
        "REGISTRATION_ENABLED": "false",
        "SEED_HEALTH_DATA": "false",
        "MESSAGING_CHANNEL": "console",
        "PHOTON_DELIVERY": "off",
        "DEMO_PARENT_HANDLE": "+15550100001",
        "DEMO_CHILD_HANDLE": "+15550100002",
        "DEMO_RESPONDER_HANDLE": "+15550100003",
        "DEMO_RESPONDER2_HANDLE": "+15550100004",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def action(**fields):
    return {"action_id": str(uuid4()), **fields}


def actor_from_response(client, response):
    assert response.status_code in (200, 201), response.text
    info = response.json()
    assert info["authenticated"] is True
    return {
        "id": info["person"]["person_id"],
        "csrf": info["csrf_token"],
        "cookie": client.cookies.get(accounts.COOKIE),
        "info": info,
    }


def become(client, actor):
    client.cookies.clear()
    client.cookies.set(accounts.COOKIE, actor["cookie"], domain="testserver.local", path="/")


def demo(client, role="child"):
    client.cookies.clear()
    response = client.post("/api/auth/demo", json={"role": role}, headers={"Origin": ORIGIN})
    return actor_from_response(client, response)


def post(client, actor, path, body):
    become(client, actor)
    return client.post(path, json=body, headers={"X-CSRF-Token": actor["csrf"], "Origin": ORIGIN})


def get(client, actor, path):
    become(client, actor)
    return client.get(path)


def register(client, *, invitation=None, **fields):
    client.cookies.clear()
    body = {
        "email": f"fixture-{uuid4().hex}@example.test",
        "password": PASSWORD,
        "display_name": "Synthetic Member",
        **fields,
    }
    if invitation:
        body["invitation_token"] = invitation["invitation_token"]
    response = client.post("/api/auth/register", json=body, headers={"Origin": ORIGIN})
    return response, body


def new_parent(client, child, **fields):
    response = post(
        client,
        child,
        "/api/onboarding/parents",
        action(
            display_name="Synthetic Parent",
            timezone="America/New_York",
            country_code="OTHER",
            **fields,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def household(client):
    child = demo(client)
    invite = new_parent(client, child)
    response, _ = register(client, invitation=invite, display_name="Synthetic Parent")
    parent = actor_from_response(client, response)
    assert parent["id"] == invite["parent_id"]
    return child, parent


def new_responder(client, inviter, parent_id):
    response = post(
        client,
        inviter,
        "/api/onboarding/invites",
        action(
            parent_id=parent_id,
            role="responder",
            display_name="Synthetic Helper",
            categories=["grocery_assistance"],
            relationship="neighbor",
            emergency_contact=True,
        ),
    )
    assert response.status_code == 201, response.text
    response, _ = register(client, invitation=response.json(), display_name="Synthetic Helper")
    return actor_from_response(client, response)


def approve_responder(client, parent, responder, *, active=True):
    response = post(
        client,
        parent,
        f"/api/parent/responders/{responder['id']}/approve",
        {"active": active, "emergency_contact": False},
    )
    assert response.status_code == 200, response.text
    return response


def request_help(client, parent):
    response = post(
        client,
        parent,
        "/api/parent/requests",
        action(
            request_type="grocery_assistance",
            details="Please bring milk from the shop.",
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def saved_bp(client, parent, **changes):
    body = action(
        systolic=120,
        diastolic=80,
        measured_at=utcnow().isoformat(),
        device_validated=True,
        adult_nonpregnant=True,
        symptoms_checked=True,
        symptoms=[],
    )
    body.update(changes)
    response = post(client, parent, "/api/parent/blood-pressure", body)
    return response, body


def rows(model):
    with session_scope() as session:
        return list(session.exec(select(model)).all())


def test_anonymous_session_is_non_cacheable_and_protected_views_require_identity(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.json()["authenticated"] is False
    assert response.json()["registration_available"] is False
    assert client.get("/api/parent/home").status_code == 401
    assert client.get("/api/responder/tasks").status_code == 401
    assert client.get("/api/parents/parent_001/personal-health").status_code == 401


@pytest.mark.parametrize(
    "role,own_path",
    [
        ("child", "/api/parents/parent_001/overview"),
        ("parent", "/api/parent/home"),
        ("responder", "/api/responder/tasks"),
    ],
)
def test_demo_sessions_have_explicit_roles_and_httponly_cookie(client, role, own_path):
    response = client.post("/api/auth/demo", json={"role": role}, headers={"Origin": ORIGIN})
    actor = actor_from_response(client, response)
    assert actor["info"]["person"]["role"] == role
    assert actor["info"]["demo_session"] is True
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "no-store" in response.headers["cache-control"]
    assert get(client, actor, own_path).status_code == 200
    assert get(client, actor, "/api/auth/session").json()["person"]["role"] == role
    with session_scope() as session:
        stored = session.get(LoginSession, hash_token(actor["cookie"]))
        assert stored and stored.person_id == actor["id"]
        assert stored.token_hash != actor["cookie"]


def test_demo_disabled_and_disallowed_origin_cannot_create_sessions(client, monkeypatch):
    before = len(rows(LoginSession))
    denied = client.post(
        "/api/auth/demo", json={"role": "parent"}, headers={"Origin": "https://evil.example"}
    )
    assert denied.status_code == 403
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()
    assert client.post("/api/auth/demo", json={"role": "parent"}).status_code == 404
    assert len(rows(LoginSession)) == before


def test_cookie_mutations_require_correct_csrf_and_allowed_origin(client):
    parent = demo(client, "parent")
    body = action(response="okay", note="Synthetic check-in")
    for headers in (
        {},
        {"X-CSRF-Token": "wrong"},
        {"X-CSRF-Token": parent["csrf"], "Origin": "https://evil.example"},
    ):
        become(client, parent)
        assert client.post("/api/parent/check-in", json=body, headers=headers).status_code == 403
    assert rows(CheckInRow) == []
    accepted = post(client, parent, "/api/parent/check-in", body)
    assert accepted.status_code == 200
    replayed = post(client, parent, "/api/parent/check-in", body)
    assert replayed.status_code == 200 and replayed.json() == accepted.json()
    assert len(rows(CheckInRow)) == 1
    changed = post(client, parent, "/api/parent/check-in", {**body, "note": "Changed input"})
    assert changed.status_code == 409


def test_logout_requires_csrf_and_revokes_the_cookie_session(client):
    parent = demo(client, "parent")
    assert client.post("/api/auth/logout").status_code == 403
    assert client.get("/api/auth/session").json()["authenticated"] is True
    response = post(client, parent, "/api/auth/logout", {})
    assert response.status_code == 204
    assert client.get("/api/auth/session").json()["authenticated"] is False
    become(client, parent)
    assert client.get("/api/parent/home").status_code == 401
    assert rows(LoginSession) == []


def test_expired_session_fails_protected_reads_and_session_endpoint_clears_cookie(client):
    parent = demo(client, "parent")
    with session_scope() as session:
        row = session.get(LoginSession, hash_token(parent["cookie"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
        session.add(row)
    assert get(client, parent, "/api/parent/home").status_code == 401
    response = get(client, parent, "/api/auth/session")
    assert response.status_code == 200 and response.json()["authenticated"] is False
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_private_pilot_registration_requires_invitation_and_rejects_role_injection(client):
    response, body = register(client)
    assert response.status_code == 403
    assert rows(Account) == []
    assert client.post("/api/auth/register", json={**body, "role": "admin"}).status_code == 422
    assert (
        client.post("/api/auth/register", json={**body, "invitation_token": "x" * 43}).status_code
        == 400
    )


def test_explicit_open_registration_creates_only_child_account_and_password_login_works(
    client, monkeypatch
):
    monkeypatch.setenv("REGISTRATION_ENABLED", "true")
    get_settings.cache_clear()
    response, body = register(client, email="  Fixture.Account@Example.Test  ")
    actor = actor_from_response(client, response)
    assert actor["info"]["person"]["role"] == "child"
    assert actor["info"]["parent_ids"] == [] and actor["info"]["demo_session"] is False
    account = rows(Account)[0]
    assert account.email == "fixture.account@example.test"
    assert account.password_hash.startswith("scrypt-v1$") and PASSWORD not in account.password_hash
    post(client, actor, "/api/auth/logout", {})
    response = client.post(
        "/api/auth/login", json={"email": "FIXTURE.ACCOUNT@example.test", "password": PASSWORD}
    )
    signed_in = actor_from_response(client, response)
    assert signed_in["id"] == actor["id"] and signed_in["cookie"] != actor["cookie"]
    client.cookies.clear()
    wrong = client.post(
        "/api/auth/login", json={"email": account.email, "password": "wrong password"}
    )
    missing = client.post(
        "/api/auth/login", json={"email": "missing@example.test", "password": "wrong password"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    duplicate, _ = register(client, email=body["email"])
    assert duplicate.status_code == 400 and len(rows(Account)) == 1


def test_auth_rate_limit_persists_failed_attempts_and_returns_retry_after(client):
    for _ in range(12):
        response = client.post(
            "/api/auth/login", json={"email": "absent@example.test", "password": "incorrect"}
        )
        assert response.status_code == 401
    limited = client.post(
        "/api/auth/login", json={"email": "absent@example.test", "password": "incorrect"}
    )
    assert limited.status_code == 429 and limited.headers["retry-after"] == "900"


def test_onboarding_is_idempotent_and_sharing_starts_off_before_parent_accepts(client):
    child = demo(client)
    body = action(display_name="Synthetic Parent", timezone="Asia/Kathmandu", country_code="NP")
    first = post(client, child, "/api/onboarding/parents", body)
    assert first.status_code == 201, first.text
    second = post(client, child, "/api/onboarding/parents", body)
    assert second.status_code == 201 and second.json() == first.json()
    invite = first.json()
    assert (
        post(
            client, child, "/api/onboarding/parents", {**body, "display_name": "Different Parent"}
        ).status_code
        == 409
    )
    with session_scope() as session:
        parent = session.get(Person, invite["parent_id"])
        assert parent.timezone == "Asia/Kathmandu" and parent.handle is None
        link = session.exec(select(FamilyLink).where(FamilyLink.parent_id == parent.id)).one()
        assert link.share_health is False and link.share_checkins is False
        stored_invite = session.get(Invitation, hash_token(invite["invitation_token"]))
        assert stored_invite and stored_invite.token_hash != invite["invitation_token"]
    assert get(client, child, f"/api/parents/{invite['parent_id']}/personal-health").json() is None
    overview = get(client, child, f"/api/parents/{invite['parent_id']}/overview").json()
    assert overview["health"] is None and overview["latest_checkin"] is None


def test_invitation_lookup_claim_once_and_explicit_parent_sharing(client):
    child = demo(client)
    invite = new_parent(client, child)
    client.cookies.clear()
    described = client.post("/api/auth/invitation", json={"token": invite["invitation_token"]})
    assert described.status_code == 200
    assert described.json()["role"] == "parent"
    assert set(described.json()) == {"role", "display_name", "invited_by", "expires_at"}
    response, _ = register(client, invitation=invite, share_health=True, share_checkins=False)
    parent = actor_from_response(client, response)
    assert parent["info"]["parent_ids"] == [invite["parent_id"]]
    health = get(client, child, f"/api/parents/{parent['id']}/personal-health")
    assert health.status_code == 200 and health.json()["parent_id"] == parent["id"]
    reused, _ = register(client, invitation=invite)
    assert reused.status_code == 400
    assert (
        client.post("/api/auth/invitation", json={"token": invite["invitation_token"]}).status_code
        == 400
    )


def test_reissued_and_expired_invitations_are_rejected(client):
    child = demo(client)
    old = new_parent(client, child)
    replacement = post(
        client, child, "/api/onboarding/invites", action(parent_id=old["parent_id"], role="parent")
    )
    assert replacement.status_code == 201, replacement.text
    replacement = replacement.json()
    client.cookies.clear()
    assert (
        client.post("/api/auth/invitation", json={"token": old["invitation_token"]}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/auth/invitation", json={"token": replacement["invitation_token"]}
        ).status_code
        == 200
    )
    with session_scope() as session:
        stored = session.get(Invitation, hash_token(replacement["invitation_token"]))
        stored.expires_at = utcnow() - timedelta(seconds=1)
        session.add(stored)
    expired, _ = register(client, invitation=replacement)
    assert expired.status_code == 400


def test_numeric_invites_preserve_zeroes_avoid_old_codes_and_accept_grouping(client, monkeypatch):
    draws = iter([42, 42, 43])
    monkeypatch.setattr("app.routes.experience.secrets.randbelow", lambda _: next(draws))
    child = demo(client)
    first = new_parent(client, child)
    assert first["invitation_token"] == "00000042"
    with session_scope() as session:
        row = session.get(Invitation, hash_token(first["invitation_token"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
        session.add(row)
    second = new_parent(client, child)
    assert second["invitation_token"] == "00000043"
    expiry = datetime.fromisoformat(second["expires_at"].replace("Z", "+00:00"))
    assert timedelta(minutes=29) < expiry - utcnow() <= timedelta(minutes=30)
    client.cookies.clear()
    assert client.post("/api/auth/invitation", json={"token": "0000 0043"}).status_code == 200
    response, _ = register(client, invitation={**second, "invitation_token": "0000-0043"})
    assert response.status_code == 201, response.text
    assert response.json()["person"]["person_id"] == second["parent_id"]


def test_numeric_invite_generation_exhaustion_rolls_back_the_new_profile(client, monkeypatch):
    child = demo(client)
    monkeypatch.setattr("app.routes.experience.secrets.randbelow", lambda _: 42)
    new_parent(client, child)
    before = len(rows(Person))
    response = post(
        client,
        child,
        "/api/onboarding/parents",
        action(display_name="Synthetic Collision", timezone="UTC", country_code="OTHER"),
    )
    assert response.status_code == 503
    assert len(rows(Person)) == before


@pytest.mark.parametrize("token", ["1234567", "123456789", "abcdefgh", "１２３４５６７８"])
def test_malformed_short_invitation_codes_are_rejected(client, token):
    assert client.post("/api/auth/invitation", json={"token": token}).status_code == 422
    response, _ = register(client, invitation={"invitation_token": token})
    assert response.status_code == 422


def test_legacy_invitation_links_remain_redeemable(client):
    child = demo(client)
    invite = new_parent(client, child)
    legacy = "synthetic-legacy-invitation-token-" + "A" * 20
    with session_scope() as session:
        row = session.get(Invitation, hash_token(invite["invitation_token"]))
        row.token_hash = hash_token(legacy)
        session.add(row)
    client.cookies.clear()
    assert client.post("/api/auth/invitation", json={"token": legacy}).status_code == 200
    response, _ = register(client, invitation={**invite, "invitation_token": legacy})
    assert response.status_code == 201, response.text


def test_renewing_expired_code_reuses_profile_and_invalidates_previous_code(client):
    child = demo(client)
    invite = new_parent(client, child)
    with session_scope() as session:
        row = session.get(Invitation, hash_token(invite["invitation_token"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
        session.add(row)
    before = len(rows(Person))
    body = action(invitation_token=invite["invitation_token"])
    renewed = post(client, child, "/api/onboarding/invites/renew", body)
    assert renewed.status_code == 201, renewed.text
    replacement = renewed.json()
    assert replacement["parent_id"] == invite["parent_id"]
    assert replacement["invitation_token"] != invite["invitation_token"]
    assert len(rows(Person)) == before
    replay = post(client, child, "/api/onboarding/invites/renew", body)
    assert replay.status_code == 201 and replay.json() == replacement
    client.cookies.clear()
    assert (
        client.post("/api/auth/invitation", json={"token": invite["invitation_token"]}).status_code
        == 400
    )
    response, _ = register(client, invitation=replacement)
    assert response.status_code == 201
    assert post(client, child, "/api/onboarding/invites/renew", body).status_code == 409


def test_helper_code_renewal_is_creator_only_and_keeps_membership(client, household):
    child, parent = household
    issued = post(
        client,
        child,
        "/api/onboarding/invites",
        action(
            parent_id=parent["id"],
            role="responder",
            display_name="Synthetic Helper",
            categories=["grocery_assistance"],
        ),
    )
    assert issued.status_code == 201, issued.text
    invite = issued.json()
    body = action(invitation_token=invite["invitation_token"])
    assert post(client, parent, "/api/onboarding/invites/renew", body).status_code == 404
    before = len(rows(Person))
    renewed = post(client, child, "/api/onboarding/invites/renew", body)
    assert renewed.status_code == 201, renewed.text
    assert len(rows(Person)) == before
    with session_scope() as session:
        original = session.get(Invitation, hash_token(invite["invitation_token"]))
        replacement = session.get(Invitation, hash_token(renewed.json()["invitation_token"]))
        assert original.person_id == replacement.person_id
    response, _ = register(client, invitation=renewed.json())
    assert response.status_code == 201 and response.json()["person"]["role"] == "responder"


def test_invitation_lookup_and_redemption_share_attempt_limit(client):
    for attempt in range(40):
        if attempt % 2:
            response, _ = register(client, invitation={"invitation_token": "99999999"})
        else:
            response = client.post("/api/auth/invitation", json={"token": "9999 9999"})
        assert response.status_code == 400
    blocked = client.post("/api/auth/invitation", json={"token": "99999999"})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "900"


def test_parent_sharing_is_explicit_revocable_and_independent_for_checkins(client, household):
    child, parent = household
    own = get(client, parent, f"/api/parents/{parent['id']}/personal-health")
    assert own.status_code == 200 and own.json() is not None
    assert get(client, child, f"/api/parents/{parent['id']}/personal-health").json() is None
    checked = post(
        client, parent, "/api/parent/check-in", action(response="okay", note="Synthetic note")
    )
    assert checked.status_code == 200
    shared = post(
        client,
        parent,
        "/api/parent/sharing",
        {"member_id": child["id"], "share_health": True, "share_checkins": False},
    )
    assert shared.status_code == 200 and shared.json()["share_health"] is True
    overview = get(client, child, f"/api/parents/{parent['id']}/overview").json()
    assert overview["latest_checkin"] is None
    assert get(client, child, f"/api/parents/{parent['id']}/personal-health").json() is not None
    reversed_permissions = post(
        client,
        parent,
        "/api/parent/sharing",
        {"member_id": child["id"], "share_health": False, "share_checkins": True},
    )
    assert reversed_permissions.status_code == 200
    assert get(client, child, f"/api/parents/{parent['id']}/personal-health").json() is None
    assert (
        get(client, child, f"/api/parents/{parent['id']}/overview").json()["latest_checkin"]
        is not None
    )
    assert (
        post(
            client,
            child,
            "/api/parent/sharing",
            {"member_id": child["id"], "share_health": True, "share_checkins": True},
        ).status_code
        == 403
    )
    assert (
        post(
            client,
            parent,
            "/api/parent/sharing",
            {"member_id": "unrelated", "share_health": True, "share_checkins": True},
        ).status_code
        == 404
    )


def test_unrelated_accounts_and_responders_cannot_read_health_or_issue_foreign_invites(
    client, household
):
    child, parent = household
    other_parent = demo(client, "parent")
    responder = demo(client, "responder")
    for actor in (other_parent, responder):
        assert get(client, actor, f"/api/parents/{parent['id']}/overview").status_code == 404
        assert get(client, actor, f"/api/parents/{parent['id']}/personal-health").status_code == 404
        assert (
            post(
                client,
                actor,
                "/api/onboarding/invites",
                action(parent_id=parent["id"], role="parent"),
            ).status_code
            == 404
        )
    for actor in (child, responder):
        assert get(client, actor, "/api/parent/home").status_code == 403
        assert (
            post(client, actor, "/api/parent/check-in", action(response="okay")).status_code == 403
        )
        assert (
            post(
                client,
                actor,
                "/api/parents/parent_001/reference",
                {"metric": "steps", "value": 4000},
            ).status_code
            == 403
        )
    assert get(client, parent, "/api/responder/tasks").status_code == 403
    assert get(client, child, "/api/responder/tasks").status_code == 403


def test_child_invited_responder_needs_parent_approval_and_gets_no_health_access(client, household):
    child, parent = household
    responder = new_responder(client, child, parent["id"])
    home = get(client, parent, "/api/parent/home").json()
    member = next(item for item in home["responders"] if item["responder_id"] == responder["id"])
    assert member["active"] is False and member["emergency_contact"] is False
    assert (
        post(
            client,
            child,
            f"/api/parent/responders/{responder['id']}/approve",
            {"active": True, "emergency_contact": True},
        ).status_code
        == 403
    )
    assert request_help(client, parent)["status"] == "failed"
    assert not any(message.person_id == responder["id"] for message in rows(OutboundMessage))
    approve_responder(client, parent, responder)
    assert get(client, responder, f"/api/parents/{parent['id']}/personal-health").status_code == 404
    assert get(client, responder, "/api/responder/tasks").json()["tasks"] == []


def test_account_responder_without_phone_completes_assigned_approved_task_idempotently(
    client, household
):
    child, parent = household
    responder = new_responder(client, child, parent["id"])
    with session_scope() as session:
        assert session.get(Person, responder["id"]).handle is None
    approve_responder(client, parent, responder)
    request = request_help(client, parent)
    assert request["status"] == "awaiting_approval", request
    request_id = request["request_id"]
    assert request["parent_approved"] is False
    assert get(client, responder, "/api/responder/tasks").json()["tasks"] == []
    decision = action(decision="approve")
    approved = post(client, parent, f"/api/parent/requests/{request_id}/decision", decision)
    assert approved.status_code == 200 and approved.json()["status"] == "sent"
    assert (
        post(client, parent, f"/api/parent/requests/{request_id}/decision", decision).json()
        == approved.json()
    )
    task_list = get(client, responder, "/api/responder/tasks").json()["tasks"]
    assert [item["request_id"] for item in task_list] == [request_id]
    assert not {"health", "blood_pressure", "handle", "parent_id"}.intersection(task_list[0])
    stranger = demo(client, "responder")
    assert get(client, stranger, "/api/responder/tasks").json()["tasks"] == []
    assert (
        post(
            client, stranger, f"/api/responder/tasks/{request_id}/reply", action(decision="accept")
        ).status_code
        == 404
    )
    premature = post(
        client, responder, f"/api/responder/tasks/{request_id}/reply", action(decision="complete")
    )
    assert premature.status_code == 409
    accept = action(decision="accept", eta_minutes=12)
    accepted = post(client, responder, f"/api/responder/tasks/{request_id}/reply", accept)
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert accepted.json()["estimated_arrival_minutes"] == 12
    assert (
        post(client, responder, f"/api/responder/tasks/{request_id}/reply", accept).json()
        == accepted.json()
    )
    assert (
        post(
            client,
            responder,
            f"/api/responder/tasks/{request_id}/reply",
            {**accept, "eta_minutes": 30},
        ).status_code
        == 409
    )
    complete = action(decision="complete", note="Synthetic groceries delivered")
    completed = post(client, responder, f"/api/responder/tasks/{request_id}/reply", complete)
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    assert (
        post(client, responder, f"/api/responder/tasks/{request_id}/reply", complete).json()
        == completed.json()
    )
    assert (
        post(
            client, responder, f"/api/responder/tasks/{request_id}/reply", action(decision="accept")
        ).status_code
        == 409
    )
    assert len(rows(CareRequestRow)) == 1
    assert all(message.channel != "photon" for message in rows(OutboundMessage))


def test_revoking_responder_hides_assignment_and_blocks_further_reply(client, household):
    child, parent = household
    responder = new_responder(client, child, parent["id"])
    approve_responder(client, parent, responder)
    request = request_help(client, parent)
    assert request["status"] == "awaiting_approval", request
    request_id = request["request_id"]
    assert (
        post(
            client,
            parent,
            f"/api/parent/requests/{request_id}/decision",
            action(decision="approve"),
        ).status_code
        == 200
    )
    approve_responder(client, parent, responder, active=False)
    assert get(client, responder, "/api/responder/tasks").json()["tasks"] == []
    assert (
        post(
            client, responder, f"/api/responder/tasks/{request_id}/reply", action(decision="accept")
        ).status_code
        == 404
    )
    cancelled = post(
        client, parent, f"/api/parent/requests/{request_id}/decision", action(decision="cancel")
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_bp_manual_provenance_and_idempotency_cannot_be_forged_by_client(client, household):
    child, parent = household
    response, body = saved_bp(client, parent)
    assert response.status_code == 201, response.text
    reading = response.json()
    assert reading["source"] == "manual_cuff" and reading["data_mode"] == "live"
    assert reading["assessment"]["override_allowed"] is False
    assert post(client, parent, "/api/parent/blood-pressure", body).json() == reading
    assert len(rows(BloodPressureReading)) == 1
    assert (
        post(client, parent, "/api/parent/blood-pressure", {**body, "systolic": 121}).status_code
        == 409
    )
    for changed in ({"source": "fitbit"}, {"data_mode": "live"}, {"parent_id": "parent_001"}):
        assert (
            post(client, parent, "/api/parent/blood-pressure", {**body, **changed}).status_code
            == 422
        )
    assert (
        post(
            client, child, "/api/parent/blood-pressure", {**body, "action_id": str(uuid4())}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "change",
    [
        {"systolic": 70, "diastolic": 80},
        {"systolic": 301},
        {"measured_at": "2026-11-14T12:00:00"},
        {"symptoms": ["chest_pain"], "symptoms_checked": False},
        {"symptoms": ["invented_symptom"]},
    ],
)
def test_bp_invalid_inputs_are_rejected_without_persisting_a_reading(client, change):
    parent = demo(client, "parent")
    response, _ = saved_bp(client, parent, **change)
    assert response.status_code == 422
    assert rows(BloodPressureReading) == []


def test_bp_old_or_future_measurements_are_rejected_and_scope_is_explicit(client, household):
    _child, parent = household
    for instant in (utcnow() - timedelta(days=2), utcnow() + timedelta(hours=1)):
        response, _ = saved_bp(client, parent, measured_at=instant.isoformat())
        assert response.status_code == 422
    response, _ = saved_bp(client, parent, adult_nonpregnant=False)
    assert (
        response.status_code == 201
        and response.json()["assessment"]["level"] == "scope_unconfirmed"
    )


def test_declared_normal_for_me_never_overrides_severe_bp_or_creates_dispatch(client, household):
    child, parent = household
    declared = post(
        client,
        parent,
        f"/api/parents/{parent['id']}/reference",
        {"metric": "systolic", "value": 190, "note": "A stated reference, not a care plan"},
    )
    assert declared.status_code == 200 and declared.json()["provisional"] is True
    first, _ = saved_bp(
        client,
        parent,
        systolic=190,
        diastolic=125,
        measured_at=(utcnow() - timedelta(minutes=2)).isoformat(),
    )
    assert first.status_code == 201 and first.json()["assessment"]["level"] == "repeat"
    second, _ = saved_bp(client, parent, systolic=185, diastolic=122)
    assert second.status_code == 201 and second.json()["assessment"]["level"] == "contact_clinician"
    assert second.json()["assessment"]["override_allowed"] is False
    assert rows(SafetyIncidentRow) == [] and rows(CareRequestRow) == []
    assert rows(OutboundMessage) == []  # Parent has not shared health.
    report = get(client, parent, f"/api/parents/{parent['id']}/personal-health").json()
    metric = next(item for item in report["metrics"] if item["metric"] == "systolic")
    assert metric["declared_reference"] == 190 and metric["median"] is None
    assert metric["eligible_days"] == 0
    assert get(client, child, f"/api/parents/{parent['id']}/personal-health").json() is None


def test_urgent_symptoms_share_only_with_authorized_family_and_never_dispatch_responders(
    client, household
):
    child, parent = household
    responder = new_responder(client, child, parent["id"])
    approve_responder(client, parent, responder)
    assert (
        post(
            client,
            parent,
            "/api/parent/sharing",
            {"member_id": child["id"], "share_health": True, "share_checkins": False},
        ).status_code
        == 200
    )
    response, body = saved_bp(client, parent, symptoms=["chest_pain"], adult_nonpregnant=False)
    assert response.status_code == 201 and response.json()["assessment"]["level"] == "emergency"
    assert "local emergency services" in response.json()["assessment"]["message"]
    messages = rows(OutboundMessage)
    assert len(messages) == 1 and messages[0].person_id == child["id"]
    assert messages[0].channel == "console" and messages[0].status == "simulated"
    assert post(client, parent, "/api/parent/blood-pressure", body).status_code == 201
    assert len(rows(OutboundMessage)) == 1
    assert rows(SafetyIncidentRow) == [] and rows(CareRequestRow) == []


@pytest.mark.parametrize(
    "metric,value",
    [
        ("steps", 1.5),
        ("resting_heart_rate", 0),
        ("sleep_minutes", 1441),
        ("systolic", 49),
        ("diastolic", 201),
        ("unknown", 100),
    ],
)
def test_personal_reference_bounds_and_metric_names_are_validated(client, metric, value):
    parent = demo(client, "parent")
    response = post(
        client, parent, "/api/parents/parent_001/reference", {"metric": metric, "value": value}
    )
    assert response.status_code == 422


def test_personal_report_learns_source_civil_dates_and_rejects_mixed_simulation(client, household):
    child, parent = household
    today = utcnow().astimezone(ZoneInfo("America/New_York")).date()
    with session_scope() as session:
        for age in range(1, 8):
            source_day = today - timedelta(days=age)
            session.add(
                HealthObservationRow(
                    observation_id=f"synthetic_day_{age}",
                    parent_id=parent["id"],
                    source="fitbit",
                    data_mode="live",
                    timestamp=datetime.combine(source_day, datetime.min.time(), tzinfo=UTC),
                    steps=age * 1000,
                    resting_heart_rate=60 + age,
                    sleep_minutes=None,
                )
            )
    response = get(client, parent, f"/api/parents/{parent['id']}/personal-health")
    assert response.status_code == 200, response.text
    report = response.json()
    metrics = {item["metric"]: item for item in report["metrics"]}
    assert set(metrics) == {"steps", "resting_heart_rate", "sleep_minutes", "systolic", "diastolic"}
    assert metrics["steps"]["status"] == "observed" and metrics["steps"]["eligible_days"] == 7
    assert metrics["steps"]["median"] == 4000 and metrics["steps"]["mad"] == 2000
    assert (
        metrics["sleep_minutes"]["eligible_days"] == 0
        and metrics["sleep_minutes"]["median"] is None
    )
    assert metrics["systolic"]["declared_reference"] is None
    assert report["evidence"] and all(
        item["url"].startswith("https://") for item in report["evidence"]
    )
    assert "not clinical calibration" in report["explanation"]
    with session_scope() as session:
        session.add(
            HealthObservationRow(
                observation_id="synthetic_mode_conflict",
                parent_id=parent["id"],
                source="fitbit",
                data_mode="simulated",
                timestamp=datetime.combine(
                    today - timedelta(days=8), datetime.min.time(), tzinfo=UTC
                ),
                steps=9999,
            )
        )
    mixed = get(client, parent, f"/api/parents/{parent['id']}/personal-health").json()
    steps = next(item for item in mixed["metrics"] if item["metric"] == "steps")
    assert steps["status"] == "insufficient_data" and steps["median"] is None
    assert get(client, child, f"/api/parents/{parent['id']}/personal-health").json() is None


def test_responder_invitation_replay_does_not_create_extra_people_or_tokens(client, household):
    child, parent = household
    body = action(
        parent_id=parent["id"],
        role="responder",
        display_name="Synthetic Invitation Replay",
        categories=["grocery_assistance"],
    )
    before_people = len(rows(Person))
    before_invites = len(rows(Invitation))
    first = post(client, child, "/api/onboarding/invites", body)
    assert first.status_code == 201, first.text
    repeated = post(client, child, "/api/onboarding/invites", body)
    assert repeated.status_code == 201 and repeated.json() == first.json()
    assert len(rows(Person)) == before_people + 1
    assert len(rows(Invitation)) == before_invites + 1
    changed = post(client, child, "/api/onboarding/invites", {**body, "display_name": "Different"})
    assert changed.status_code == 409


def test_parent_issued_responder_invitation_is_explicit_approval_and_cannot_claim_other_role(
    client, household
):
    _child, parent = household
    responder = new_responder(client, parent, parent["id"])
    assert responder["info"]["person"]["role"] == "responder"
    assert responder["info"]["parent_ids"] == []
    home = get(client, parent, "/api/parent/home").json()
    member = next(item for item in home["responders"] if item["responder_id"] == responder["id"])
    assert member["active"] is True and member["emergency_contact"] is True
    assert get(client, responder, f"/api/parents/{parent['id']}/personal-health").status_code == 404
    assert (
        post(
            client,
            responder,
            "/api/onboarding/parents",
            action(display_name="Unauthorized parent", timezone="UTC", country_code="OTHER"),
        ).status_code
        == 403
    )


def test_personal_cookie_identity_cannot_be_escalated_with_demo_admin_bearer(client):
    child = demo(client, "child")
    become(client, child)
    response = client.get("/api/parent/home", headers={"Authorization": "Bearer dev-admin-token"})
    assert response.status_code == 403
    with session_scope() as session:
        stored = session.get(LoginSession, hash_token(child["cookie"]))
        stored.expires_at = utcnow() - timedelta(seconds=1)
        session.add(stored)
    assert (
        client.get(
            "/api/parents/parent_001/overview", headers={"Authorization": "Bearer dev-admin-token"}
        ).status_code
        == 401
    )


def test_bp_required_adult_confirmation_defaults_to_unconfirmed_and_omitted_symptoms_are_not_clear(
    client,
    household,
):
    _child, parent = household
    body = action(
        systolic=120, diastolic=80, measured_at=utcnow().isoformat(), device_validated=True
    )
    response = post(client, parent, "/api/parent/blood-pressure", body)
    assert response.status_code == 201
    assert response.json()["symptoms_checked"] is False
    assert response.json()["assessment"]["level"] == "scope_unconfirmed"
    checked_scope = post(
        client,
        parent,
        "/api/parent/blood-pressure",
        {
            **body,
            "action_id": str(uuid4()),
            "adult_nonpregnant": True,
        },
    )
    assert (
        checked_scope.status_code == 201 and checked_scope.json()["assessment"]["level"] == "review"
    )


def test_current_bp_guidance_does_not_use_another_parents_previous_reading(client, household):
    child, parent = household
    invite = new_parent(client, child)
    response, _ = register(client, invitation=invite, display_name="Another Synthetic Parent")
    another = actor_from_response(client, response)
    old, _ = saved_bp(
        client,
        another,
        systolic=190,
        diastolic=125,
        measured_at=(utcnow() - timedelta(minutes=2)).isoformat(),
    )
    assert old.status_code == 201
    assert old.json()["data_mode"] == "live"
    assert old.json()["assessment"]["level"] == "repeat"
    response, _ = saved_bp(client, parent, systolic=185, diastolic=122)
    assert response.status_code == 201
    assert response.json()["assessment"]["level"] == "repeat"
    report = get(client, parent, f"/api/parents/{parent['id']}/personal-health").json()
    assert [row["reading_id"] for row in report["blood_pressure"]] == [
        response.json()["reading_id"]
    ]


def test_reference_edits_are_parent_owned_latest_value_remains_provisional_and_missingness_stays_missing(
    client, household
):
    child, parent = household
    path = f"/api/parents/{parent['id']}/reference"
    assert post(client, child, path, {"metric": "steps", "value": 2000}).status_code == 403
    unrelated = demo(client, "parent")
    assert post(client, unrelated, path, {"metric": "steps", "value": 2000}).status_code == 404
    first = post(
        client, parent, path, {"metric": "steps", "value": 2000, "note": "Initial statement"}
    )
    assert first.status_code == 200 and first.json()["provisional"] is True
    second = post(
        client, parent, path, {"metric": "steps", "value": 3000, "note": "Updated statement"}
    )
    assert second.status_code == 200
    report = get(client, parent, f"/api/parents/{parent['id']}/personal-health").json()
    assert len(report["references"]) == 1
    assert report["references"][0]["value"] == 3000
    assert report["references"][0]["provisional"] is True
    metric = next(item for item in report["metrics"] if item["metric"] == "steps")
    assert metric["declared_reference"] == 3000
    assert metric["median"] is None and metric["eligible_days"] == 0


def test_decline_replay_remains_idempotent_after_next_responder_is_proposed(client, household):
    child, parent = household
    first = new_responder(client, child, parent["id"])
    second = new_responder(client, child, parent["id"])
    for responder in (first, second):
        approve_responder(client, parent, responder)
    request = request_help(client, parent)
    assert request["status"] == "awaiting_approval"
    selected = next(actor for actor in (first, second) if actor["id"] == request["responder_id"])
    alternative = next(actor for actor in (first, second) if actor["id"] != selected["id"])
    request_id = request["request_id"]
    assert (
        post(
            client,
            parent,
            f"/api/parent/requests/{request_id}/decision",
            action(decision="approve"),
        ).status_code
        == 200
    )
    decline = action(decision="decline", note="Unavailable for this synthetic request")
    result = post(client, selected, f"/api/responder/tasks/{request_id}/reply", decline)
    assert result.status_code == 200 and result.json()["status"] == "awaiting_approval"
    assert get(client, alternative, "/api/responder/tasks").json()["tasks"] == []
    count_after_decline = len(rows(OutboundMessage))
    replay = post(client, selected, f"/api/responder/tasks/{request_id}/reply", decline)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result.json()
    assert len(rows(OutboundMessage)) == count_after_decline
    assert (
        post(
            client, selected, f"/api/responder/tasks/{request_id}/reply", action(decision="accept")
        ).status_code
        == 404
    )


def test_explicit_demo_parent_bp_is_simulated_and_never_creates_urgent_family_outbox(client):
    parent = demo(client, "parent")
    assert parent["info"]["demo_session"] is True
    with session_scope() as session:
        linked = session.exec(select(FamilyLink).where(FamilyLink.parent_id == parent["id"])).one()
        assert (
            linked.share_health is True
        )  # Proves simulation, not lack of sharing, suppresses alerts.
    response, body = saved_bp(client, parent, systolic=190, diastolic=125, symptoms=["chest_pain"])
    assert response.status_code == 201, response.text
    assert response.json()["source"] == "manual_cuff"
    assert response.json()["data_mode"] == "simulated"
    assert response.json()["assessment"]["level"] == "unverified"
    assert "Simulated" in response.json()["assessment"]["message"]
    saved = rows(BloodPressureReading)
    assert len(saved) == 1 and saved[0].data_mode == "simulated"
    assert rows(OutboundMessage) == []
    assert rows(SafetyIncidentRow) == [] and rows(CareRequestRow) == []
    repeated = post(client, parent, "/api/parent/blood-pressure", body)
    assert repeated.status_code == 201 and repeated.json() == response.json()
    assert rows(OutboundMessage) == []


def test_emergency_policy_task_is_visible_without_routine_approval_and_revocation_removes_access(
    client, household
):
    _child, parent = household
    responder = new_responder(client, parent, parent["id"])
    help_requested = post(
        client,
        parent,
        "/api/parent/check-in",
        action(response="help", note="Synthetic emergency test"),
    )
    assert help_requested.status_code == 200, help_requested.text
    requests = rows(CareRequestRow)
    assert len(requests) == 1
    request = requests[0]
    assert request.origin == "emergency" and request.approval_basis == "emergency_policy"
    assert request.parent_approved is False
    assert request.responder_id == responder["id"] and request.status == "sent"
    tasks = get(client, responder, "/api/responder/tasks")
    assert tasks.status_code == 200
    assert [item["request_id"] for item in tasks.json()["tasks"]] == [request.id]
    accepted = post(
        client,
        responder,
        f"/api/responder/tasks/{request.id}/reply",
        action(decision="accept", eta_minutes=5),
    )
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert get(client, responder, "/api/responder/tasks").json()["tasks"][0]["status"] == "accepted"
    revoked = post(
        client,
        parent,
        f"/api/parent/responders/{responder['id']}/approve",
        {"active": True, "emergency_contact": False},
    )
    assert revoked.status_code == 200
    assert get(client, responder, "/api/responder/tasks").json()["tasks"] == []
    denied = post(
        client, responder, f"/api/responder/tasks/{request.id}/reply", action(decision="complete")
    )
    assert denied.status_code == 404
    assert rows(CareRequestRow)[0].status == "accepted"
    assert all(message.channel != "photon" for message in rows(OutboundMessage))
