import os

# Env vars override any developer .env file, so tests can never hit real Photon/Gemini.
os.environ.update(
    APP_ENV="test",
    BACKGROUND_TASKS_ENABLED="false",
    PHOTON_API_KEY="",
    PHOTON_PROJECT_ID="",
    PHOTON_WEBHOOK_SECRET="",
    PHOTON_PLATFORM="imessage",
    PHOTON_AGENT_USER_ID="",
    GEMINI_API_KEY="",
    INGEST_API_KEY="",
    TIGER_DATABASE_URL="",
    LOGIN_ALIASES="{}",
    DEMO_MODE="true",
    SEED_DEMO_DATA="true",
    PROACTIVE_CHECKINS_ENABLED="false",
    DEMO_EMERGENCY_NUMBER="102",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402

CHILD = {"Authorization": "Bearer dev-child-token"}
PARENT = {"Authorization": "Bearer dev-parent-token"}
ADMIN = {"Authorization": "Bearer dev-admin-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # TEST_DATABASE_URL (e.g. postgresql://...) runs the suite against real Postgres,
    # which is what production uses; otherwise each test gets a fresh SQLite file.
    pg_url = os.environ.get("TEST_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", pg_url or f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    db.reset_engine()
    if pg_url:
        from sqlmodel import SQLModel

        from app import models  # noqa: F401

        SQLModel.metadata.drop_all(db.get_engine())
    from app.main import app

    with TestClient(app) as c:
        yield c
    db.reset_engine()
    get_settings.cache_clear()


def say(client: TestClient, person_id: str, text: str) -> list[dict]:
    """Simulate `person_id` texting FarCare; returns FarCare's outbound messages."""
    r = client.post("/api/demo/inbound", json={"person_id": person_id, "text": text}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["outbound"]


def texts_to(outbound: list[dict], role: str) -> list[str]:
    return [m["text"] for m in outbound if m["to_role"] == role]
