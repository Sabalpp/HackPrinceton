"""Verify the actual private preview through ASGI; no sockets or external services."""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT

from scripts.local import prepare_farcare_preview as preview  # noqa: E402


def main():
    report = {
        "status": "running",
        "checks": [],
        "transport": "ASGI TestClient",
        "network_used": False,
    }

    def check(label, condition):
        report["checks"].append({"name": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    try:
        metadata = preview.prepare_preview()
        check(
            "Preview database and metadata are private",
            preview.DATABASE.stat().st_mode & 0o777 == 0o600
            and preview.METADATA.stat().st_mode & 0o777 == 0o600,
        )
        with tempfile.TemporaryDirectory(
            prefix="preview-verify-", dir=preview.PRIVATE
        ) as directory:
            clone = Path(directory) / "farcare_preview.sqlite3"
            fd = os.open(clone, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(preview.DATABASE.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(clone) as destination:
                    source.backup(destination)
            preview.configure_preview(clone)
            with preview.offline_guard():
                from app.config import get_settings
                from app.db import reset_engine, session_scope
                from app.main import app
                from app.models import FamilyLink, Person
                from fastapi.testclient import TestClient
                from sqlmodel import select

                clock = datetime.fromisoformat(metadata["generated_at"])
                raw, rows, observations = preview.real_projection(clock)
                expected = [obs.model_dump(mode="json") for obs in observations]
                check(
                    "Cached source count matches metadata", len(rows) == metadata["source_records"]
                )
                check(
                    "Cached source bytes match recorded SHA256",
                    hashlib.sha256(raw).hexdigest() == metadata["source_snapshot_sha256"],
                )
                with TestClient(app) as client:
                    st = get_settings()
                    check(
                        "External services and automatic jobs disabled",
                        not st.use_photon
                        and not st.gemini_enabled
                        and not st.background_tasks_enabled
                        and not st.proactive_checkins_enabled
                        and not st.seed_demo_data
                        and not st.seed_health_data
                        and st.photon_delivery == "off",
                    )
                    route = "/api/parents/parent_001/overview"
                    check("Anonymous health access rejected", client.get(route).status_code == 401)
                    check(
                        "Invalid token rejected",
                        client.get(route, headers={"Authorization": "Bearer invalid"}).status_code
                        == 401,
                    )
                    child = {"Authorization": "Bearer dev-child-token"}
                    response = client.get(route, headers=child)
                    check("Authorized child overview loads", response.status_code == 200)
                    overview = response.json()
                    actual = overview["health"]["observations"]
                    check("Exact projected dates, values and nulls survive API", actual == expected)
                    check(
                        "Real health remains labeled live",
                        overview["health"]["data_mode"] == "live",
                    )
                    check(
                        "Preview identity and timezone are accurate",
                        overview["parent"]["display_name"] == "Fitbit wearer (preview)"
                        and overview["parent"]["timezone"] == preview.ZONE,
                    )
                    health_response = client.get("/api/parents/parent_001/health", headers=child)
                    check(
                        "Health endpoint agrees with overview",
                        health_response.status_code == 200
                        and health_response.json()["observations"] == expected,
                    )
                    with session_scope() as session:
                        people = session.exec(select(Person)).all()
                        check(
                            "Only documented synthetic handles and no Photon spaces",
                            {person.id: person.handle for person in people if person.handle}
                            == preview.SYNTHETIC_HANDLES
                            and all(not person.photon_space_id for person in people),
                        )

                    admin = {"Authorization": "Bearer dev-admin-token"}

                    def say(person, text):
                        reply = client.post(
                            "/api/demo/inbound",
                            headers=admin,
                            json={"person_id": person, "text": text},
                        )
                        check("Synthetic inbound accepted: " + person, reply.status_code == 200)
                        return reply.json()["outbound"]

                    def requests():
                        reply = client.get("/api/parents/parent_001/care-requests", headers=child)
                        check("Care requests endpoint available", reply.status_code == 200)
                        return reply.json()

                    before_requests = {item["request_id"] for item in requests()}
                    passive = say("child_001", "How is Mom doing?")
                    check(
                        "Passive son status replies only to son",
                        bool(passive) and all(item["to_role"] == "child" for item in passive),
                    )
                    passive_text = "\n".join(item["text"] for item in passive)
                    check(
                        "Passive status includes real dated health explanation",
                        any(
                            trend["observation"] in passive_text
                            for trend in overview["health"]["trends"]
                        )
                        and "Latest recorded steps:" in passive_text,
                    )
                    check(
                        "Passive status creates no help request",
                        {item["request_id"] for item in requests()} == before_requests,
                    )
                    created = say("parent_001", "Can someone help me with groceries tomorrow?")
                    new_requests = [
                        item for item in requests() if item["request_id"] not in before_requests
                    ]
                    check(
                        "Grocery request waits for parent consent",
                        len(new_requests) == 1
                        and new_requests[0]["status"] == "awaiting_approval"
                        and not any(item["to_role"] == "responder" for item in created),
                    )
                    request_id = new_requests[0]["request_id"]
                    approved = say("parent_001", "YES")
                    care = next(item for item in requests() if item["request_id"] == request_id)
                    check(
                        "Parent YES dispatches only to the selected synthetic responder",
                        care["parent_approved"]
                        and care["responder_id"] in preview.SYNTHETIC_HANDLES
                        and any(item["to_role"] == "responder" for item in approved),
                    )
                    accepted = say(care["responder_id"], "YES, I can help at 4 PM")
                    care = next(item for item in requests() if item["request_id"] == request_id)
                    check(
                        "Responder YES reaches accepted and notifies family in console",
                        care["status"] == "accepted"
                        and any(item["to_role"] == "parent" for item in accepted)
                        and any(item["to_role"] == "child" for item in accepted),
                    )
                    check(
                        "All workflow messages remain simulated",
                        all(
                            item["channel"] == "console" and item["status"] == "simulated"
                            for item in passive + created + approved + accepted
                        ),
                    )
                    with session_scope() as session:
                        link = session.exec(select(FamilyLink)).one()
                        link.share_health = False
                        session.add(link)
                    hidden = client.get(route, headers=child).json()
                    check("Health sharing permission enforced", hidden["health"] is None)
                    check(
                        "Direct health route also respects sharing",
                        client.get("/api/parents/parent_001/health", headers=child).json() is None,
                    )
                    check(
                        "Parent retains access to own health",
                        client.get(
                            route, headers={"Authorization": "Bearer dev-parent-token"}
                        ).json()["health"]
                        is not None,
                    )
                    with session_scope() as session:
                        session.delete(session.exec(select(FamilyLink)).one())
                    check(
                        "Unlinked child cannot access parent",
                        client.get(route, headers=child).status_code == 404,
                    )
                reset_engine()
        report.update(
            status="passed",
            checks_passed=len(report["checks"]),
            source_records=metadata["source_records"],
            observation_days=len(expected),
            date_range=metadata["date_range"],
            metric_days=metadata["metric_days"],
        )
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    preview.private_json(preview.PRIVATE / "farcare_preview_verification.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
