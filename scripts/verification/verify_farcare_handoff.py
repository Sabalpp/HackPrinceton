"""Private-data integration check; never commits fixtures or touches live app tables."""

import json
import os
import secrets
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import REPO_ROOT, WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT
os.environ.update(
    APP_ENV="test",
    BACKGROUND_TASKS_ENABLED="false",
    PHOTON_API_KEY="",
    PHOTON_PROJECT_ID="",
    GEMINI_API_KEY="",
    PROACTIVE_CHECKINS_ENABLED="false",
    MESSAGING_CHANNEL="console",
    DEMO_MODE="true",
    SEED_DEMO_DATA="true",
    SEED_HEALTH_DATA="false",
    INGEST_API_KEY=secrets.token_hex(24),
)
os.environ.pop("TEST_DATABASE_URL", None)
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402 - test settings must precede backend imports
from app.db import reset_engine, session_scope  # noqa: E402 - test settings must precede backend imports
from app.health.sync_tiger import SyncState, publish_if_changed  # noqa: E402 - test settings must precede backend imports
from app.health.tiger import project_daily  # noqa: E402 - test settings must precede backend imports
from app.models import FamilyLink  # noqa: E402 - test settings must precede backend imports
from fastapi.testclient import TestClient  # noqa: E402 - test settings must precede backend imports


def main():
    rows = json.loads((ROOT / "pipeline_data/farcare_source_rows.json").read_text())
    dates = [date.fromisoformat(row["local_date"]) for row in rows]
    observations = project_daily(
        rows,
        subject_id="demo",
        parent_id="parent_001",
        start=min(dates),
        end=datetime.now(timezone.utc).date(),
        now=datetime.now(timezone.utc),
        timezone="America/New_York",
    )
    with tempfile.TemporaryDirectory(dir=ROOT / "pipeline_data") as temporary:
        os.environ["DATABASE_URL"] = "sqlite:///" + temporary + "/farcare_test.sqlite3"
        get_settings.cache_clear()
        reset_engine()
        from app.main import app

        with TestClient(app) as client:
            key = get_settings().ingest_api_key
            assert client.post("/api/health/observations", json=[]).status_code == 401
            first = publish_if_changed(
                client, "http://127.0.0.1", observations, ingest_key=key, state=SyncState()
            )
            replay = publish_if_changed(
                client, "http://127.0.0.1", observations, ingest_key=key, state=SyncState()
            )
            child = {"Authorization": "Bearer dev-child-token"}
            response = client.get("/api/parents/parent_001/health", headers=child)
            assert response.status_code == 200
            body = response.json()
            assert body["data_mode"] == "live"
            assert first.accepted == len(observations) and replay.changed == 0
            assert len(body["observations"]) == len(observations)
            assert not body["suggest_checkin"]
            timeline = client.get("/api/parents/parent_001/timeline", headers=child).json()
            assert len([item for item in timeline if item["kind"] == "health.synced"]) == 1
            with session_scope() as session:
                session.query(FamilyLink).one().share_health = False
            assert client.get("/api/parents/parent_001/health", headers=child).json() is None
            visible = client.get("/api/parents/parent_001/timeline", headers=child).json()
            assert not any(item["kind"] == "health.synced" for item in visible)
            report = {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "source": "Actual canonical records read from Tiger",
                "backend_test_database": "temporary SQLite; production connection configured separately",
                "source_records": len(rows),
                "daily_observations": len(observations),
                "metric_days": {
                    metric: sum(getattr(o, metric) is not None for o in observations)
                    for metric in ("steps", "resting_heart_rate", "sleep_minutes")
                },
                "mode": body["data_mode"],
                "replay_changed": replay.changed,
                "unauthenticated_ingest_rejected": True,
                "sharing_permission_enforced": True,
                "one_health_synced_event": True,
                "no_unsupported_health_alerts": True,
            }
        reset_engine()
    out = ROOT / "pipeline_data/farcare_verification.json"
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(report, file, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
