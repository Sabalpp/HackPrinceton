import os

# Env vars override any developer .env file, so tests can never hit real Photon/Gemini.
os.environ.update(
    APP_ENV="test",
    BACKGROUND_TASKS_ENABLED="false",
    PHOTON_API_KEY="",
    PHOTON_PROJECT_ID="",
    PHOTON_WEBHOOK_SECRET="",
    GEMINI_API_KEY="",
    INGEST_API_KEY="",
    DEMO_MODE="true",
    SEED_DEMO_DATA="true",
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
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    db.reset_engine()
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
