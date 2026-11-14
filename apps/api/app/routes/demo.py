"""Demo controls (DEMO_MODE=true + admin token). Everything here is labeled simulated.

- /fall     replay a labeled synthetic IMU trace through the detector -> fall.suspected
- /inbound  inject a message as if a person texted FarCare (for rehearsal / Photon outage)
- /outbox   what FarCare sent (console channel shows simulated messages here)
- /reset    clear workflow state between rehearsals (keeps people + health data)
"""

import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import Session, select

from app.auth import require_demo_admin
from app.db import get_session, session_scope, utcnow
from app.fall_detection import detector, traces
from app.integrations.messaging import is_reachable
from app.integrations.photon.inbound import InboundText
from app.models import (
    CareRequestRow,
    CheckInRow,
    ConversationTurn,
    FallEventRecord,
    HealthObservationRow,
    InboundMessageRow,
    OutboundMessage,
    Person,
    SafetyIncidentRow,
    TimelineEventRow,
)
from app.schemas.contracts import DataMode, FallSuspectedEvent
from app.services import conversation, proactive, safety

router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[Depends(require_demo_admin)])


class FallDemoIn(BaseModel):
    parent_id: str
    trace: Literal["walking", "sitting", "bending", "lying_down", "fall", "recovery"] = "fall"
    seed: int = Field(default=1, ge=1, le=1000)


@router.post("/fall")
def demo_fall(body: FallDemoIn, session: Session = Depends(get_session)):
    detection = detector.classify(traces.generate(body.trace, body.seed))
    result = None
    if detection.label == "possible_fall":
        event = FallSuspectedEvent(
            event_id=f"simfall_{secrets.token_hex(6)}",
            parent_id=body.parent_id,
            event_type="fall.suspected",
            source="simulated_imu",
            timestamp=utcnow(),
            confidence=detection.score,
            data_mode=DataMode.simulated,
        )
        try:
            result = safety.ingest_fall_event(session, event).model_dump(mode="json")
        except LookupError:
            raise HTTPException(404, "Unknown parent_id") from None
    return {
        "trace": body.trace,
        "data_mode": "simulated",
        "detection": detection.to_dict(),
        "ingest": result,
    }


class CheckinDemoIn(BaseModel):
    parent_id: str


@router.post("/checkin")
def demo_checkin(body: CheckinDemoIn, session: Session = Depends(get_session)):
    """Send the proactive wellness check-in now (skips the daily time window)."""
    parent = session.get(Person, body.parent_id)
    if parent is None or parent.role != "parent":
        raise HTTPException(404, "Unknown parent_id")
    msg = proactive.maybe_checkin(
        session, parent, force=True, key=f"proactive:{parent.id}:demo:{secrets.token_hex(4)}"
    )
    if msg is None:
        return {"sent": False, "detail": "Skipped: unreachable, open incident, or pending approval"}
    return {"sent": msg.status in ("sent", "simulated"), "status": msg.status, "text": msg.text}


class InboundDemoIn(BaseModel):
    person_id: str
    text: str = Field(min_length=1, max_length=2000)


@router.post("/inbound")
def demo_inbound(body: InboundDemoIn):
    with session_scope() as s:
        person = s.get(Person, body.person_id)
        if person is None or not person.handle:
            raise HTTPException(404, "Unknown person (or person has no handle)")
        handle = person.handle
    started = utcnow()
    conversation.receive(
        InboundText(
            message_id=f"sim_{secrets.token_hex(8)}",
            sender_handle=handle,
            text=body.text,
            simulated=True,
        )
    )
    with session_scope() as s:
        sent = s.exec(
            select(OutboundMessage)
            .where(OutboundMessage.created_at >= started)  # type: ignore[operator]
            .order_by(OutboundMessage.created_at)
        ).all()  # type: ignore[arg-type]
        return {"outbound": [_out(s, m) for m in sent]}


def _out(s: Session, m: OutboundMessage) -> dict:
    person = s.get(Person, m.person_id) if m.person_id else None
    return {
        "to": person.display_name if person else None,
        "to_role": person.role if person else None,
        "channel": m.channel,
        "status": m.status,
        "text": m.text,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/outbox")
def outbox(limit: int = 50, session: Session = Depends(get_session)):
    rows = session.exec(
        select(OutboundMessage).order_by(OutboundMessage.created_at.desc()).limit(min(limit, 200))  # type: ignore[union-attr]
    ).all()
    return [_out(session, m) for m in rows]


@router.get("/messages")
def messages(limit: int = 300, session: Session = Depends(get_session)):
    """Every conversation in both directions, oldest first (for the demo console's phones)."""
    from app.integrations.photon.inbound import normalize_handle

    by_handle = {p.handle: p for p in session.exec(select(Person)).all() if p.handle}
    items: list[dict] = []
    for row in session.exec(select(InboundMessageRow)).all():
        person = by_handle.get(normalize_handle(row.payload.get("sender_handle", "")))
        if person is None:
            continue
        items.append(
            {
                "id": f"in:{row.id}",
                "person_id": person.id,
                "direction": "in",
                "text": row.payload.get("text", ""),
                "status": row.status,
                "channel": "simulated" if row.payload.get("simulated") else "photon",
                "at": row.received_at.isoformat(),
            }
        )
    for m in session.exec(select(OutboundMessage)).all():
        if m.person_id:
            items.append(
                {
                    "id": f"out:{m.id}",
                    "person_id": m.person_id,
                    "direction": "out",
                    "text": m.text,
                    "status": m.status,
                    "channel": m.channel,
                    "at": m.created_at.isoformat(),
                }
            )
    items.sort(key=lambda i: i["at"])
    return items[-min(limit, 1000) :]


class HealthSyncDemoIn(BaseModel):
    parent_id: str = "parent_001"
    scenario: Literal["decline", "normal", "recovering"] = "decline"
    days: int = Field(default=35, ge=1, le=120)


@router.post("/health-sync")
def demo_health_sync(body: HealthSyncDemoIn, session: Session = Depends(get_session)):
    """Run the mock Fitbit sender (Sabal's stand-in) server-side through the real ingest path."""
    import importlib.util
    from pathlib import Path

    from app.routes.ingest import ingest_health
    from app.schemas.contracts import HealthObservation

    path = Path(__file__).resolve().parents[2] / "scripts" / "mock_fitbit_sender.py"
    if not path.exists():
        raise HTTPException(404, "Mock sender not available in this build")
    spec = importlib.util.spec_from_file_location("mock_fitbit_sender", path)
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)  # type: ignore[union-attr]
    payload = sender.backfill(body.parent_id, body.days, body.scenario)
    return ingest_health([HealthObservation.model_validate(p) for p in payload], session)


@router.get("/people")
def people(session: Session = Depends(get_session)):
    return [
        {
            "person_id": p.id,
            "display_name": p.display_name,
            "role": p.role,
            "has_handle": p.handle is not None,
            "reachable": is_reachable(p),
        }
        for p in session.exec(select(Person)).all()
    ]


@router.post("/reset")
def reset(include_health: bool = False, session: Session = Depends(get_session)):
    """Clear workflow state. include_health=true also clears health observations."""
    if include_health:
        session.exec(delete(HealthObservationRow))  # type: ignore[call-overload]
    for table in (
        CareRequestRow,
        SafetyIncidentRow,
        FallEventRecord,
        TimelineEventRow,
        ConversationTurn,
        InboundMessageRow,
        OutboundMessage,
        CheckInRow,
    ):
        session.exec(delete(table))  # type: ignore[call-overload]
    return {"reset": True}
