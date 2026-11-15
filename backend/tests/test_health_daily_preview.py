import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.health import daily_preview, service

NOW = datetime(2026, 11, 15, 2, tzinfo=UTC)


def enable(monkeypatch, **overrides):
    config = {"parent_id": "parent_fixture", "expires_at": (NOW + timedelta(hours=12)).isoformat()}
    monkeypatch.setenv("SIMULATED_DAILY_PREVIEW", json.dumps({**config, **overrides}))


def test_preview_is_simulated_completed_local_days_and_no_dispatch(monkeypatch):
    enable(monkeypatch)
    result = daily_preview.summary("parent_fixture", "America/Los_Angeles", 35, NOW)
    assert result is not None and result.data_mode == "simulated"
    assert len(result.observations) == 35
    assert result.observations[-1].timestamp.date().isoformat() == "2026-11-13"
    assert all(row.data_mode == row.source == "simulated" for row in result.observations)
    assert {t.metric: t.recent for t in result.trends} == {
        "steps": 2560,
        "sleep_minutes": 360,
        "resting_heart_rate": 77,
    }
    assert not result.suggest_checkin
    assert not any(t.notable for t in result.trends)
    assert all(t.observation.startswith("Simulated") for t in result.trends)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "null",
        "[]",
        "invalid",
        "{}",
        json.dumps({"parent_id": "parent_other", "expires_at": "2026-11-16T00:00:00+00:00"}),
        json.dumps({"parent_id": "parent_fixture", "expires_at": "2026-11-14T00:00:00+00:00"}),
        json.dumps({"parent_id": "parent_fixture", "expires_at": "2026-11-16T00:00:00"}),
        json.dumps({"parent_id": "parent_fixture", "expires_at": "2027-11-16T00:00:00+00:00"}),
    ],
)
def test_preview_requires_exact_parent_valid_expiring_opt_in(monkeypatch, raw):
    monkeypatch.setenv("SIMULATED_DAILY_PREVIEW", raw)
    assert daily_preview.summary("parent_fixture", "Asia/Kathmandu", 35, NOW) is None


def test_preview_invalid_timezone_falls_back(monkeypatch):
    enable(monkeypatch)
    assert daily_preview.summary("parent_fixture", "invalid", 35, NOW) is None


def test_summary_preview_does_not_read_or_modify_real_observations(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        service, "recent", lambda *_: pytest.fail("Real history must stay separate")
    )
    parent = SimpleNamespace(role="parent", timezone="Asia/Kathmandu")
    session = SimpleNamespace(get=lambda *_: parent)
    result = service.summary(session, "parent_fixture")
    assert result.data_mode == "simulated"
    assert len(result.observations) == 35


def test_other_parent_and_expired_preview_keep_real_summary(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    monkeypatch.setattr(service, "recent", lambda *_: [])
    parent = SimpleNamespace(role="parent", timezone="Asia/Kathmandu")
    session = SimpleNamespace(get=lambda *_: parent)
    assert service.summary(session, "parent_other").data_mode == "none"
    monkeypatch.setattr(service, "utcnow", lambda: NOW + timedelta(days=1))
    assert service.summary(session, "parent_fixture").data_mode == "none"
