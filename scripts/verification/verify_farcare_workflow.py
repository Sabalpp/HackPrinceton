"""Verify FarCare through ASGI with real projected health and simulated care.

This executable reads the existing private Tiger snapshot, creates a temporary
SQLite application database, and writes only a private result artifact. It does
not contact Google, Tiger, Photon, Gemini, or any real person. Run with the app's
Python environment: app_repo/backend/.venv/bin/python app_repo/scripts/verification/verify_farcare_workflow.py
"""

import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import tempfile
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import REPO_ROOT, WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT
SOURCE = ROOT / "pipeline_data/farcare_source_rows.json"
ARTIFACT = ROOT / "pipeline_data/farcare_workflow_verification.json"
ZONE = "America/New_York"
PRIVATE_CHECKIN_MARKER = "synthetic_private_checkin_marker"

# Explicit process settings prevent developer credentials, handles, or .env
# defaults from being used. These tokens and people belong to the temporary DB.
os.environ.update(
    APP_ENV="test",
    BACKGROUND_TASKS_ENABLED="false",
    PHOTON_API_KEY="",
    PHOTON_PROJECT_ID="",
    PHOTON_WEBHOOK_SECRET="",
    PHOTON_DELIVERY="off",
    GEMINI_API_KEY="",
    PROACTIVE_CHECKINS_ENABLED="false",
    MESSAGING_CHANNEL="console",
    DEMO_MODE="true",
    SEED_DEMO_DATA="true",
    SEED_HEALTH_DATA="false",
    INGEST_API_KEY=secrets.token_hex(24),
    DEMO_CHILD_TOKEN="dev-child-token",
    DEMO_PARENT_TOKEN="dev-parent-token",
    DEMO_ADMIN_TOKEN="dev-admin-token",
    DEMO_PARENT_HANDLE="+15550100001",
    DEMO_CHILD_HANDLE="+15550100002",
    DEMO_RESPONDER_HANDLE="+15550100003",
    DEMO_RESPONDER2_HANDLE="+15550100004",
    DEMO_EMERGENCY_NUMBER="",
)
os.environ.pop("TEST_DATABASE_URL", None)
logging.disable(logging.CRITICAL)  # Never print private projected observations.
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402 - test settings must precede backend imports
from app.db import reset_engine, session_scope, utcnow  # noqa: E402 - test settings must precede backend imports
from app.health.sync_tiger import SyncState, publish_if_changed  # noqa: E402 - test settings must precede backend imports
from app.health.tiger import project_daily  # noqa: E402 - test settings must precede backend imports
from app.models import CareRequestRow, FamilyLink, InboundMessageRow, Person, SafetyIncidentRow  # noqa: E402 - test settings must precede backend imports
from app.services.background import run_tick  # noqa: E402 - test settings must precede backend imports
from fastapi.testclient import TestClient  # noqa: E402 - test settings must precede backend imports
from sqlmodel import select  # noqa: E402 - test settings must precede backend imports

CHILD = {"Authorization": "Bearer dev-child-token"}
PARENT = {"Authorization": "Bearer dev-parent-token"}
ADMIN = {"Authorization": "Bearer dev-admin-token"}


class VerificationFailure(Exception):
    pass


def write_private(report):
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = ARTIFACT.with_name(ARTIFACT.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(ARTIFACT)


def main():
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "transport": "ASGI TestClient; no server sockets",
        "health_source": {
            "data_mode": "live",
            "source": "Existing private canonical Tiger export",
            "snapshot": "pipeline_data/farcare_source_rows.json",
            "fresh_remote_read": False,
            "note": "Real consenting tester measurements projected onto a synthetic test identity.",
        },
        "care_interactions": {
            "data_mode": "simulated",
            "people": "Seeded synthetic FarCare personas",
            "messaging": "console",
            "real_messages_sent": 0,
        },
        "database": "Temporary SQLite; removed after verification",
        "checks": [],
        "scenarios": {},
        "network_attempts": 0,
        "limitations": [
            "No deployed server, browser rendering, or live Photon/Gemini delivery is tested.",
            "The real health snapshot is reused locally; this run does not verify a fresh device/cloud sync.",
            "All help requests, approvals, responder replies, and silence conditions are simulated.",
            "Timeout checks move only temporary database deadlines and invoke one real background tick.",
            "Passive status queries are separate from ordinary help requests, which still require the parent's YES.",
        ],
    }
    stage = "initialize"

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise VerificationFailure(name)

    def blocked_network(*args, **kwargs):
        report["network_attempts"] += 1
        raise VerificationFailure("Unexpected outbound network attempt")

    def request(client, method, url, *, headers=CHILD, body=None, expected=200):
        response = client.request(method, url, headers=headers, json=body)
        check(f"HTTP {method} {url} -> {expected}", response.status_code == expected)
        return response.json()

    def say(client, person, text):
        return request(
            client,
            "POST",
            "/api/demo/inbound",
            headers=ADMIN,
            body={"person_id": person, "text": text},
        )["outbound"]

    def messages_to(messages, role):
        return [item for item in messages if item["to_role"] == role]

    def no_private_health(name, value):
        serialized = json.dumps(value).lower()
        terms = (
            PRIVATE_CHECKIN_MARKER,
            "resting_heart_rate",
            "sleep_minutes",
            "heart rate",
            "heart-rate",
            "spo2",
            "bpm",
            "feeling tired",
            "latest check-in",
        )
        check(name, not any(term in serialized for term in terms))

    def latest(client):
        rows = request(client, "GET", "/api/parents/parent_001/care-requests")
        check("A synthetic care request exists", bool(rows))
        return rows[0]

    def timeline(client):
        return request(client, "GET", "/api/parents/parent_001/timeline")

    def outbox(client):
        return request(client, "GET", "/api/demo/outbox?limit=200", headers=ADMIN)

    try:
        stage = "project_real_private_snapshot"
        raw = SOURCE.read_bytes()
        rows = json.loads(raw)
        check("Real canonical snapshot is nonempty", isinstance(rows, list) and bool(rows))
        dates = [date.fromisoformat(row["local_date"]) for row in rows]
        clock = datetime.now(timezone.utc)
        observations = project_daily(
            rows,
            subject_id="demo",
            parent_id="parent_001",
            start=min(dates),
            end=clock.astimezone(ZoneInfo(ZONE)).date(),
            now=clock,
            timezone=ZONE,
        )
        check("Real source projects to nonempty daily observations", bool(observations))
        check(
            "Projected observations retain live provenance",
            all(
                str(getattr(item.data_mode, "value", item.data_mode)) == "live"
                for item in observations
            ),
        )
        report["health_source"].update(
            sha256=hashlib.sha256(raw).hexdigest(),
            canonical_records=len(rows),
            projected_days=len(observations),
            timezone=ZONE,
            metric_days={
                metric: sum(getattr(item, metric) is not None for item in observations)
                for metric in ("steps", "resting_heart_rate", "sleep_minutes")
            },
        )
        with tempfile.TemporaryDirectory(
            prefix="workflow-", dir=ROOT / "pipeline_data"
        ) as temporary:
            os.environ["DATABASE_URL"] = "sqlite:///" + temporary + "/farcare.sqlite3"
            get_settings.cache_clear()
            reset_engine()
            from app.main import app

            with ExitStack() as guard:
                guard.enter_context(patch.object(socket.socket, "connect", blocked_network))
                guard.enter_context(patch.object(socket.socket, "connect_ex", blocked_network))
                guard.enter_context(patch.object(socket, "create_connection", blocked_network))
                with TestClient(app) as client:
                    settings = get_settings()
                    check(
                        "External integrations and background scheduling disabled",
                        not settings.use_photon
                        and not settings.gemini_enabled
                        and not settings.background_tasks_enabled
                        and settings.messaging_channel == "console"
                        and settings.photon_delivery == "off",
                    )
                    with session_scope() as session:
                        parent = session.get(Person, "parent_001")
                        parent.timezone = ZONE
                        session.add(parent)

                    stage = "authenticated_real_health_overview"
                    request(
                        client, "GET", "/api/parents/parent_001/overview", headers={}, expected=401
                    )
                    request(
                        client,
                        "POST",
                        "/api/health/observations",
                        headers={},
                        body=[],
                        expected=401,
                    )
                    receipt = publish_if_changed(
                        client,
                        "http://127.0.0.1",
                        observations,
                        ingest_key=settings.ingest_api_key,
                        state=SyncState(),
                    )
                    replay = publish_if_changed(
                        client,
                        "http://127.0.0.1",
                        observations,
                        ingest_key=settings.ingest_api_key,
                        state=SyncState(),
                    )
                    check(
                        "Real projection accepted and identical replay is idempotent",
                        receipt.accepted == len(observations) and replay.changed == 0,
                    )
                    overview = request(client, "GET", "/api/parents/parent_001/overview")
                    check(
                        "Authenticated overview contains live projected health",
                        overview["health"]["data_mode"] == "live"
                        and bool(overview["health"]["observations"]),
                    )
                    health_events = [
                        event for event in timeline(client) if event["kind"] == "health.synced"
                    ]
                    check(
                        "Replay creates no duplicate health timeline event", len(health_events) == 1
                    )
                    report["scenarios"]["real_health_overview"] = {
                        "passed": True,
                        "mode": overview["health"]["data_mode"],
                        "visible_observation_count": len(overview["health"]["observations"]),
                        "replay_changed": replay.changed,
                    }

                    stage = "passive_child_monitoring_before_parent_inbound"
                    with session_scope() as session:
                        check(
                            "Passive monitoring starts before any parent inbound message",
                            not session.exec(select(InboundMessageRow)).all(),
                        )
                    passive_phrases = ["How is Mom doing?", "status", "Any news?"]
                    passive_replies = []
                    for phrase in passive_phrases:
                        response = say(client, "child_001", phrase)
                        check(
                            f"Passive query '{phrase}' replies only to the child",
                            bool(messages_to(response, "child"))
                            and not messages_to(response, "parent")
                            and not messages_to(response, "responder"),
                        )
                        passive_replies.append(
                            "\n".join(item["text"] for item in messages_to(response, "child"))
                        )
                    with session_scope() as session:
                        parent = session.get(Person, "parent_001")
                        parent_inbound = sum(
                            item.payload.get("sender_handle") == parent.handle
                            for item in session.exec(select(InboundMessageRow)).all()
                        )
                        check(
                            "Parent can remain silent throughout passive status queries",
                            parent_inbound == 0,
                        )
                        check(
                            "Passive queries create no care request or safety check",
                            not session.exec(select(CareRequestRow)).all()
                            and not session.exec(select(SafetyIncidentRow)).all(),
                        )
                    expected_trends = overview["health"]["trends"]
                    all_insufficient = all(
                        item["direction"] == "insufficient_data" for item in expected_trends
                    )
                    grounded = [
                        any(item["observation"] in reply for item in expected_trends)
                        for reply in passive_replies
                    ]
                    expected_metric_lines = []
                    for metric, prefix, suffix in (
                        ("steps", "Latest recorded steps: ", ""),
                        ("resting_heart_rate", "Latest reported resting heart rate: ", " bpm"),
                        ("sleep_minutes", "Latest recorded main sleep: ", " min"),
                    ):
                        available = [
                            item
                            for item in overview["health"]["observations"]
                            if item.get(metric) is not None
                        ]
                        if available:
                            latest_value = max(available, key=lambda item: item["timestamp"])
                            formatted = (
                                f"{latest_value[metric]:,}"
                                if metric == "steps"
                                else str(latest_value[metric])
                            )
                            expected_metric_lines.append(
                                f"{prefix}{formatted}{suffix} on {latest_value['timestamp'][:10]} (live Fitbit data)."
                            )
                    report["scenarios"]["passive_child_monitoring"] = {
                        "passed": all(grounded),
                        "query_interactions": "simulated child messages",
                        "health_data_mode": "live",
                        "verified_phrases": passive_phrases,
                        "parent_inbound_messages": parent_inbound,
                        "parent_or_neighbor_contacted": False,
                        "source_baseline_insufficient": all_insufficient,
                        "authorized_health_or_baseline_explanation_returned": all(grounded),
                        "ordinary_help_still_requires_parent_yes": True,
                    }
                    check(
                        "Passive queries return authorized health or explain insufficient baseline",
                        all(grounded),
                    )
                    check(
                        "Passive status reports exact latest dated real metrics without invented current values",
                        bool(expected_metric_lines)
                        and all(
                            line in reply
                            for reply in passive_replies
                            for line in expected_metric_lines
                        ),
                    )
                    report["scenarios"]["passive_child_monitoring"][
                        "latest_dated_metric_facts_verified"
                    ] = len(expected_metric_lines)
                    with session_scope() as session:
                        link = session.exec(select(FamilyLink)).one()
                        link.share_health = False
                        link.share_checkins = False
                        session.add(link)
                    private_response = say(client, "child_001", "How is Mom doing?")
                    private_text = "\n".join(
                        item["text"] for item in messages_to(private_response, "child")
                    ).lower()
                    check(
                        "Passive monitoring respects revoked sharing without contacting the parent",
                        not messages_to(private_response, "parent")
                        and not messages_to(private_response, "responder")
                        and not any(
                            term in private_text
                            for term in (
                                "heart rate",
                                "resting",
                                "bpm",
                                "steps",
                                "sleep",
                                "baseline",
                                "fitbit",
                            )
                        ),
                    )
                    with session_scope() as session:
                        link = session.exec(select(FamilyLink)).one()
                        link.share_health = True
                        link.share_checkins = True
                        session.add(link)
                    report["scenarios"]["passive_child_monitoring"]["sharing_enforced"] = True

                    stage = "sharing_revocation_before_new_requests"
                    say(client, "parent_001", "I've been feeling tired. " + PRIVATE_CHECKIN_MARKER)
                    shared = request(client, "GET", "/api/parents/parent_001/overview")
                    check(
                        "Synthetic check-in exists before privacy test",
                        PRIVATE_CHECKIN_MARKER in shared["latest_checkin"]["summary"],
                    )
                    with session_scope() as session:
                        link = session.exec(select(FamilyLink)).one()
                        link.share_health = False
                        link.share_checkins = False
                        session.add(link)
                    hidden = request(client, "GET", "/api/parents/parent_001/overview")
                    check(
                        "Child overview honors revoked health and check-in sharing",
                        hidden["health"] is None and hidden["latest_checkin"] is None,
                    )
                    check(
                        "Direct health endpoint honors sharing revocation",
                        request(client, "GET", "/api/parents/parent_001/health") is None,
                    )
                    own = request(client, "GET", "/api/parents/parent_001/overview", headers=PARENT)
                    check(
                        "Parent retains access to own real health",
                        own["health"]["data_mode"] == "live",
                    )

                    stage = "simulated_parent_request_lifecycle"
                    created = say(
                        client, "parent_001", "Can someone help me with groceries tomorrow?"
                    )
                    care = latest(client)
                    care_id = care["request_id"]
                    states = [care["status"]]
                    check(
                        "Parent request waits for explicit approval and contacts nobody",
                        care["status"] == "awaiting_approval"
                        and not care["parent_approved"]
                        and not messages_to(created, "responder"),
                    )
                    approved = say(client, "parent_001", "Yes")
                    care = latest(client)
                    states.append(care["status"])
                    check(
                        "Parent approval dispatches to authorized responder",
                        care["status"] == "sent"
                        and care["parent_approved"]
                        and care["approval_basis"] == "parent_reply"
                        and care["responder_id"] == "responder_001"
                        and len(messages_to(approved, "responder")) == 1,
                    )
                    no_private_health(
                        "Responder request contains no prior health/check-in data",
                        messages_to(approved, "responder"),
                    )
                    accepted = say(client, "responder_001", "Yes, I can help at 4 PM")
                    care = latest(client)
                    states.append(care["status"])
                    check(
                        "Responder accepts with a parsed time and family notification",
                        care["status"] == "accepted"
                        and care["responder_note"] == "4 PM"
                        and bool(messages_to(accepted, "child"))
                        and bool(messages_to(accepted, "parent")),
                    )
                    completed = say(client, "responder_001", "Done, dropped the groceries off")
                    care = latest(client)
                    states.append(care["status"])
                    check(
                        "Responder completion updates request and family",
                        care["status"] == "completed" and bool(messages_to(completed, "child")),
                    )
                    history = [event for event in timeline(client) if event["ref_id"] == care_id]
                    transitions = {event["data"].get("to") for event in history}
                    check(
                        "Timeline contains the full request lifecycle",
                        any(event["kind"] == "care_request.created" for event in history)
                        and {"approved", "sent", "accepted", "completed"} <= transitions,
                    )
                    report["scenarios"]["parent_request"] = {
                        "data_mode": "simulated",
                        "passed": True,
                        "states": states,
                        "timeline_events": len(history),
                        "responder_minimum_information": True,
                    }

                    stage = "simulated_family_request_consent"
                    asked = say(
                        client, "child_001", "Can someone help Mom with groceries tomorrow?"
                    )
                    care = latest(client)
                    check(
                        "Family-origin request still requires the parent's consent",
                        care["status"] == "awaiting_approval"
                        and care["requested_by_id"] == "child_001"
                        and not messages_to(asked, "responder")
                        and bool(messages_to(asked, "parent")),
                    )
                    no_private_health(
                        "New family-origin request exposes no hidden health data", care
                    )
                    sent = say(client, "parent_001", "Yes")
                    no_private_health(
                        "Family-origin responder dispatch exposes no hidden health data",
                        messages_to(sent, "responder"),
                    )
                    say(client, "responder_001", "YES 20 min")
                    check(
                        "Family-origin responder acceptance is recorded",
                        latest(client)["status"] == "accepted",
                    )
                    say(client, "responder_001", "Done")
                    check(
                        "Family-origin request completes", latest(client)["status"] == "completed"
                    )
                    status = say(client, "child_001", "status")
                    no_private_health(
                        "Child status after fresh requests honors sharing",
                        messages_to(status, "child"),
                    )
                    visible = timeline(client)
                    check(
                        "New request timeline does not re-expose private health/check-in events",
                        not any(
                            event["kind"]
                            in {"health.synced", "checkin.recorded", "checkin.prompted"}
                            for event in visible
                        ),
                    )
                    no_private_health(
                        "Visible family timeline excludes private check-in content", visible
                    )
                    report["scenarios"]["family_consent_and_privacy"] = {
                        "data_mode": "simulated",
                        "passed": True,
                        "parent_consent_required": True,
                        "health_and_checkins_hidden": True,
                    }

                    stage = "simulated_responder_silence"
                    say(client, "parent_001", "Can someone help me with groceries tomorrow?")
                    say(client, "parent_001", "Yes")
                    care = latest(client)
                    old_outbox_count = len(outbox(client))
                    with session_scope() as session:
                        pending = session.get(CareRequestRow, care["request_id"])
                        pending.status_changed_at = utcnow() - timedelta(
                            seconds=settings.request_reply_timeout_seconds + 1
                        )
                        session.add(pending)
                    run_tick()
                    proposed = latest(client)
                    new_messages = outbox(client)[: len(outbox(client)) - old_outbox_count]
                    check(
                        "Silent responder causes a new proposal requiring renewed consent",
                        proposed["status"] == "awaiting_approval"
                        and proposed["responder_id"] == "responder_002"
                        and not messages_to(new_messages, "responder")
                        and bool(messages_to(new_messages, "parent")),
                    )
                    say(client, "parent_001", "No")
                    check(
                        "Parent can decline the replacement responder",
                        latest(client)["status"] == "cancelled",
                    )
                    report["scenarios"]["responder_timeout"] = {
                        "data_mode": "simulated",
                        "passed": True,
                        "clock_control": "Temporary request status_changed_at moved before deadline",
                        "replacement_requires_parent_consent": True,
                    }

                    stage = "simulated_family_concern_silence"
                    concern = say(
                        client, "child_001", "I haven't heard from Mom all day, I'm worried"
                    )
                    incidents = request(client, "GET", "/api/parents/parent_001/incidents")
                    incident = incidents[0]
                    check(
                        "Synthetic family concern starts a parent safety check",
                        incident["trigger"] == "family_concern"
                        and incident["status"] == "checking"
                        and bool(messages_to(concern, "parent")),
                    )
                    with session_scope() as session:
                        pending = session.get(SafetyIncidentRow, incident["incident_id"])
                        pending.check_deadline = utcnow() - timedelta(seconds=1)
                        session.add(pending)
                    run_tick()
                    escalated = request(client, "GET", "/api/parents/parent_001/incidents")[0]
                    check(
                        "No-response deadline deterministically escalates",
                        escalated["status"] == "escalated"
                        and escalated["escalation_reason"] == "no_response",
                    )
                    urgent = [item for item in outbox(client) if "URGENT" in item["text"]]
                    check(
                        "Console escalation reaches family and authorized responder",
                        bool(messages_to(urgent, "child"))
                        and bool(messages_to(urgent, "responder")),
                    )
                    say(client, "responder_001", "YES 5")
                    check(
                        "Emergency responder acceptance updates incident",
                        request(client, "GET", "/api/parents/parent_001/incidents")[0]["status"]
                        == "responder_en_route",
                    )
                    say(client, "responder_001", "Done, she's okay")
                    resolved = request(client, "GET", "/api/parents/parent_001/incidents")[0]
                    check(
                        "Emergency check-in completes and resolves incident",
                        resolved["status"] == "resolved"
                        and resolved["resolution"] == "responder_confirmed",
                    )
                    report["scenarios"]["family_concern_timeout"] = {
                        "data_mode": "simulated",
                        "passed": True,
                        "trigger": "Synthetic family concern; no sensor fall event",
                        "clock_control": "Temporary incident check_deadline moved into the past",
                        "states": ["checking", "escalated", "responder_en_route", "resolved"],
                    }

                    stage = "final_privacy_and_delivery_audit"
                    messages = outbox(client)
                    check(
                        "Every generated outbound message remains simulated console delivery",
                        bool(messages)
                        and all(
                            item["channel"] == "console" and item["status"] == "simulated"
                            for item in messages
                        ),
                    )
                    no_private_health(
                        "All responder messages exclude private health history",
                        messages_to(messages, "responder"),
                    )
                    final = request(client, "GET", "/api/parents/parent_001/overview")
                    check(
                        "Final child overview still hides health and check-ins",
                        final["health"] is None and final["latest_checkin"] is None,
                    )
                    check(
                        "No external network connections attempted", report["network_attempts"] == 0
                    )
                    report["care_interactions"]["simulated_outbound_messages"] = len(messages)
                    report["care_interactions"]["care_requests"] = len(
                        request(client, "GET", "/api/parents/parent_001/care-requests")
                    )
                    report["care_interactions"]["timeline_events_visible_to_child"] = len(
                        timeline(client)
                    )
            reset_engine()
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["failure"] = {"stage": stage, "error_type": type(exc).__name__}
        if isinstance(exc, VerificationFailure):
            report["failure"]["check"] = str(exc)
    finally:
        reset_engine()
        get_settings.cache_clear()
        write_private(report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(report["checks"]),
                "real_health_source_records": report["health_source"].get("canonical_records", 0),
                "real_projected_days": report["health_source"].get("projected_days", 0),
                "care_interactions": "simulated; console only",
                "network_attempts": report["network_attempts"],
                "failure": report.get("failure"),
                "artifact": str(ARTIFACT),
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
