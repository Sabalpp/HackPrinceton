"""Operator-managed usernames use the existing account authentication path."""

import pytest

from app.config import get_settings
from app.manage_accounts import create_owner
from app.schemas.experience import LoginIn, RegisterIn


def test_configured_alias_requires_password_and_grants_only_existing_role(client, monkeypatch):
    email, password = "alias-owner@example.test", "Synthetic password for alias 42"
    person_id = create_owner(email, "Fixture caregiver", password)
    monkeypatch.setattr(get_settings(), "login_aliases", {"caregiver": email})
    wrong = client.post("/api/auth/login", json={"email": "caregiver", "password": "incorrect"})
    assert wrong.status_code == 401
    response = client.post("/api/auth/login", json={"email": " CareGiver ", "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["person"]["person_id"] == person_id
    assert body["person"]["role"] == "child"
    assert body["parent_ids"] == []
    assert "HttpOnly" in response.headers["set-cookie"]
    assert body["csrf_token"]


def test_alias_is_not_an_email_override_or_registration_shortcut(client, monkeypatch):
    monkeypatch.setattr(
        get_settings(),
        "login_aliases",
        {
            "caregiver": "alias-owner@example.test",
            "person@example.test": "different@example.test",
        },
    )
    assert LoginIn(email="person@example.test", password="fixture").email == "person@example.test"
    with pytest.raises(ValueError):
        RegisterIn(email="caregiver", password="Long synthetic password", display_name="Fixture")
    with pytest.raises(ValueError):
        RegisterIn(email="owner@example.test", password="short", display_name="Fixture")


def test_unconfigured_alias_is_rejected_and_aliases_share_rate_limits(client, monkeypatch):
    assert (
        client.post(
            "/api/auth/login", json={"email": "caregiver", "password": "fixture"}
        ).status_code
        == 422
    )
    monkeypatch.setattr(get_settings(), "login_aliases", {"caregiver": "alias-owner@example.test"})
    for attempt in range(12):
        identifier = "caregiver" if attempt % 2 else "alias-owner@example.test"
        assert (
            client.post(
                "/api/auth/login", json={"email": identifier, "password": "incorrect"}
            ).status_code
            == 401
        )
    response = client.post("/api/auth/login", json={"email": "caregiver", "password": "incorrect"})
    assert response.status_code == 429
