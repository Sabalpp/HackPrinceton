"""Possible-fall / safety incident workflow. 100% deterministic — no LLM involvement.

fall.suspected ─► checking ──(parent: OK)──────────────► resolved (parent_ok)
                     │
                     ├─(parent: HELP)──┐
                     └─(timeout)───────┴► escalated ─► responder_en_route ─► resolved
                                              │
                                              └─ family alerted + emergency-services guidance
Rules:
- Duplicate event_ids are idempotent; new events during an open incident are merged
  (no new alerts) for FALL_DEDUPE_WINDOW_SECONDS.
- If the parent cannot be reached for the safety check, escalate immediately.
- FarCare never contacts real emergency services; it tells humans to.
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.db import new_id, utcnow
from app.integrations.messaging import delivered, send_to_person
from app.models import CareRequestRow, FallEventRecord, Person, SafetyIncidentRow
from app.schemas.contracts import (
    CareRequestStatus,
    DataMode,
    EscalationReason,
    FallIngestResult,
    FallSuspectedEvent,
    IncidentResolution,
    IncidentStatus,
    SafetyIncident,
)
from app.services import timeline
from app.services.notify import notify_family

log = logging.getLogger(__name__)
I = IncidentStatus  # noqa: E741
ACTIVE = [I.checking, I.escalated, I.responder_en_route]

REASON_PHRASES = {
    EscalationReason.help_requested: "asked for help after a possible fall",
    EscalationReason.no_response: "did not answer a safety check after a possible fall",
    EscalationReason.parent_reported_emergency: "sent a message that may be an emergency",
}
FAMILY_CONCERN_PHRASES = {
    EscalationReason.help_requested: "asked for help when FarCare checked in",
    EscalationReason.no_response: "did not answer a check-in that family asked for",
}


def _phrase(inc: SafetyIncidentRow, reason: EscalationReason) -> str:
    if inc.trigger == "family_concern" and reason in FAMILY_CONCERN_PHRASES:
        return FAMILY_CONCERN_PHRASES[reason]
    return REASON_PHRASES[reason]


def _demo_suffix(inc: SafetyIncidentRow) -> str:
    return " [Demo: simulated sensor event]" if inc.data_mode == DataMode.simulated else ""


def _local_time(parent: Person) -> str:
    try:
        tz = ZoneInfo(parent.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return utcnow().astimezone(tz).strftime("%-I:%M %p")


def to_contract(row: SafetyIncidentRow) -> SafetyIncident:
    return SafetyIncident(
        incident_id=row.id,
        parent_id=row.parent_id,
        trigger=row.trigger,  # type: ignore[arg-type]
        source=row.source,
        data_mode=DataMode(row.data_mode),
        confidence=row.confidence,
        status=I(row.status),
        escalation_reason=EscalationReason(row.escalation_reason)
        if row.escalation_reason
        else None,
        resolution=IncidentResolution(row.resolution) if row.resolution else None,
        no_responder_available=row.no_responder_available,
        check_deadline=row.check_deadline,
        opened_at=row.opened_at,
        escalated_at=row.escalated_at,
        resolved_at=row.resolved_at,
    )


def active_incident(session: Session, parent_id: str) -> SafetyIncidentRow | None:
    return session.exec(
        select(SafetyIncidentRow)
        .where(SafetyIncidentRow.parent_id == parent_id, SafetyIncidentRow.status.in_(ACTIVE))  # type: ignore[attr-defined]
        .order_by(SafetyIncidentRow.opened_at.desc())  # type: ignore[union-attr]
    ).first()


# --------------------------------------------------------------------------- ingestion


def ingest_fall_event(session: Session, event: FallSuspectedEvent) -> FallIngestResult:
    parent = session.get(Person, event.parent_id)
    if parent is None or parent.role != "parent":
        raise LookupError("Unknown parent_id")

    existing = session.get(FallEventRecord, event.event_id)
    if existing is not None:
        return FallIngestResult(
            event_id=event.event_id,
            incident_id=existing.incident_id,
            duplicate=True,
            detail="Event already processed",
        )

    settings = get_settings()
    now = utcnow()
    payload = event.model_dump(mode="json")
    current = active_incident(session, parent.id)
    window = timedelta(seconds=settings.fall_dedupe_window_seconds)
    if current is not None and now - current.opened_at < window:
        session.add(
            FallEventRecord(
                event_id=event.event_id,
                parent_id=parent.id,
                incident_id=current.id,
                duplicate=True,
                payload=payload,
            )
        )
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return FallIngestResult(
                event_id=event.event_id,
                incident_id=current.id,
                duplicate=True,
                detail="Event already processed",
            )
        timeline.record(
            session,
            parent.id,
            "fall.duplicate_suppressed",
            "Another possible-fall signal was merged into the open incident (no new alerts).",
            ref_id=current.id,
            data={"event_id": event.event_id, "confidence": event.confidence},
        )
        return FallIngestResult(
            event_id=event.event_id,
            incident_id=current.id,
            duplicate=True,
            detail="Merged into open incident; no new alerts sent",
        )

    inc = SafetyIncidentRow(
        id=new_id("inc"),
        parent_id=parent.id,
        trigger="fall.suspected",
        source=event.source,
        data_mode=event.data_mode.value,
        confidence=event.confidence,
        status=I.checking,
        check_deadline=now + timedelta(seconds=settings.safety_check_timeout_seconds),
    )
    session.add(
        FallEventRecord(
            event_id=event.event_id, parent_id=parent.id, incident_id=inc.id, payload=payload
        )
    )
    try:
        session.flush()
    except IntegrityError:  # concurrent redelivery of the same event_id
        session.rollback()
        return FallIngestResult(
            event_id=event.event_id,
            incident_id=None,
            duplicate=True,
            detail="Event already processed",
        )
    session.add(inc)
    session.flush()

    mode = "simulated sensor event" if event.data_mode == DataMode.simulated else "sensor event"
    timeline.record(
        session,
        parent.id,
        "incident.opened",
        f"Possible fall detected ({mode}, detector score {event.confidence:.2f}). "
        f"Safety check sent to {parent.display_name}.",
        ref_id=inc.id,
        data={
            "event_id": event.event_id,
            "confidence": event.confidence,
            "data_mode": event.data_mode.value,
            "source": event.source,
            "check_deadline": inc.check_deadline.isoformat() if inc.check_deadline else None,
        },
    )
    msg = send_to_person(
        session,
        parent,
        f"Hi {parent.display_name}, this is FarCare. A sensor noticed a possible fall. "
        "Are you okay? Reply OK if you're fine, or HELP if you'd like someone to come."
        + _demo_suffix(inc),
        f"incident:{inc.id}:check",
    )
    if not delivered(msg):
        timeline.record(
            session,
            parent.id,
            "incident.check_undeliverable",
            "Safety check could not be delivered; escalating immediately.",
            ref_id=inc.id,
            data={"error": msg.error},
        )
        escalate(session, inc, EscalationReason.no_response)
    return FallIngestResult(
        event_id=event.event_id, incident_id=inc.id, duplicate=False, detail="Safety check started"
    )


def open_family_concern(
    session: Session, parent: Person, requester: Person, note: str = ""
) -> SafetyIncidentRow:
    """A family member is worried (e.g. "Mom isn't answering"). Run the same deterministic
    safety check as a fall: ask the parent, and escalate on HELP or no reply."""
    current = active_incident(session, parent.id)
    if current is not None:
        return current
    settings = get_settings()
    inc = SafetyIncidentRow(
        id=new_id("inc"),
        parent_id=parent.id,
        trigger="family_concern",
        source="family_request",
        data_mode=DataMode.live.value,
        status=I.checking,
        check_deadline=utcnow() + timedelta(seconds=settings.family_check_timeout_seconds),
    )
    session.add(inc)
    session.flush()
    timeline.record(
        session,
        parent.id,
        "incident.opened",
        f"{requester.display_name} asked FarCare to check on {parent.display_name}. Check-in sent.",
        ref_id=inc.id,
        data={
            "requested_by": requester.id,
            "note": note[:200],
            "check_deadline": inc.check_deadline.isoformat() if inc.check_deadline else None,
        },
    )
    msg = send_to_person(
        session,
        parent,
        f"Hi {parent.display_name}, {requester.display_name} asked me to check on you. Are you "
        "okay? Reply OK if you're fine, or HELP if you'd like someone to come.",
        f"incident:{inc.id}:check",
    )
    if not delivered(msg):
        timeline.record(
            session,
            parent.id,
            "incident.check_undeliverable",
            "Check-in could not be delivered; escalating immediately.",
            ref_id=inc.id,
            data={"error": msg.error},
        )
        escalate(session, inc, EscalationReason.no_response)
    return inc


def open_from_message(session: Session, parent: Person, text: str) -> SafetyIncidentRow:
    """Parent wrote something that matches an emergency rule (or the agent escalated)."""
    current = active_incident(session, parent.id)
    if current is not None:
        if current.status == I.checking:
            escalate(session, current, EscalationReason.help_requested)
        return current
    inc = SafetyIncidentRow(
        id=new_id("inc"),
        parent_id=parent.id,
        trigger="parent_message",
        source="message",
        data_mode=DataMode.live.value,
        status=I.checking,
    )
    session.add(inc)
    session.flush()
    timeline.record(
        session,
        parent.id,
        "incident.opened",
        f"{parent.display_name} sent a message that may be an emergency.",
        ref_id=inc.id,
        data={"message": text[:300]},
    )
    escalate(session, inc, EscalationReason.parent_reported_emergency, quote=text)
    return inc


# --------------------------------------------------------------------------- transitions


def escalate(
    session: Session, inc: SafetyIncidentRow, reason: EscalationReason, quote: str | None = None
) -> None:
    if inc.status != I.checking:
        return  # idempotent: already escalated / en route / resolved
    from app.services import care_requests

    parent = session.get(Person, inc.parent_id)
    assert parent is not None
    inc.status = I.escalated
    inc.escalation_reason = reason.value
    inc.escalated_at = utcnow()
    session.add(inc)
    phrase = _phrase(inc, reason)
    timeline.record(
        session,
        parent.id,
        "incident.escalated",
        f"{parent.display_name} {phrase}. Alerting family and Care Circle.",
        ref_id=inc.id,
        data={"reason": reason.value},
    )

    req = care_requests.create_emergency_request(session, parent, inc.id, phrase)
    responder = session.get(Person, req.responder_id) if req.responder_id else None
    asked = responder is not None and req.status == CareRequestStatus.sent
    num = f" ({parent.emergency_number})" if parent.emergency_number else ""
    said = f' Their message: "{quote[:160]}".' if quote else ""
    who_line = (
        f"We've asked {responder.display_name} to check on them now. "
        if asked
        else "No Care Circle responder could be reached. "
    )
    notify_family(
        session,
        parent,
        lambda who: (
            f"URGENT from FarCare: {who} {phrase} ({_local_time(parent)} local time)."
            f"{said} {who_line}If you think this is an emergency, call local emergency "
            f"services{num} for {who}'s location." + _demo_suffix(inc)
        ),
        f"incident:{inc.id}:family_alert",
        emergency=True,
    )
    send_to_person(
        session,
        parent,
        (
            f"I'm contacting {responder.display_name} and your family now."
            if asked
            else "I'm contacting your family now."
        )
        + f" If you can, call emergency services{num}.",
        f"incident:{inc.id}:parent_escalated",
    )


def mark_responder_en_route(
    session: Session, incident_id: str, responder: Person, minutes: int | None
) -> None:
    inc = session.get(SafetyIncidentRow, incident_id)
    if inc is None or inc.status not in (I.escalated,):
        return
    inc.status = I.responder_en_route
    inc.no_responder_available = False
    session.add(inc)
    eta = f" (ETA ~{minutes} min)" if minutes else ""
    timeline.record(
        session,
        inc.parent_id,
        "incident.responder_en_route",
        f"{responder.display_name} is on the way{eta}.",
        ref_id=inc.id,
        data={"responder_id": responder.id, "eta_minutes": minutes},
    )


def mark_no_responder(session: Session, incident_id: str, notify: bool = True) -> None:
    inc = session.get(SafetyIncidentRow, incident_id)
    if inc is None or inc.status == I.resolved or inc.no_responder_available:
        return
    parent = session.get(Person, inc.parent_id)
    assert parent is not None
    inc.no_responder_available = True
    session.add(inc)
    timeline.record(
        session,
        inc.parent_id,
        "incident.no_responder",
        "No Care Circle responder is available.",
        ref_id=inc.id,
    )
    if notify:  # the initial escalation alert already covers "nobody reachable"
        num = f" ({parent.emergency_number})" if parent.emergency_number else ""
        notify_family(
            session,
            parent,
            lambda who: (
                f"FarCare: no Care Circle responder is available to check on {who}. "
                f"Please call local emergency services{num} or someone nearby."
            ),
            f"incident:{inc.id}:no_responder",
            emergency=True,
        )


def resolve_by_id(session: Session, incident_id: str, resolution: str, actor: str = "") -> None:
    inc = session.get(SafetyIncidentRow, incident_id)
    if inc is not None:
        resolve(session, inc, IncidentResolution(resolution), actor=actor)


def resolve(
    session: Session, inc: SafetyIncidentRow, resolution: IncidentResolution, actor: str = ""
) -> None:
    if inc.status == I.resolved:
        return
    parent = session.get(Person, inc.parent_id)
    assert parent is not None
    was_escalated = inc.status != I.checking
    inc.status = I.resolved
    inc.resolution = resolution.value
    inc.resolved_at = utcnow()
    session.add(inc)
    summaries = {
        IncidentResolution.parent_ok: f"{parent.display_name} confirmed they're okay.",
        IncidentResolution.responder_confirmed: f"{actor} confirmed {parent.display_name} is okay.",
        IncidentResolution.family_resolved: f"Marked resolved by {actor or 'family'}.",
    }
    timeline.record(
        session,
        parent.id,
        "incident.resolved",
        summaries[resolution],
        ref_id=inc.id,
        data={"resolution": resolution.value},
    )

    # Stand down any responder still being asked / on the way.
    from app.services import care_requests

    open_reqs = session.exec(
        select(CareRequestRow).where(
            CareRequestRow.incident_id == inc.id,
            CareRequestRow.status.in_(  # type: ignore[attr-defined]
                [CareRequestStatus.approved, CareRequestStatus.sent, CareRequestStatus.accepted]
            ),
        )
    ).all()
    for req in open_reqs:
        care_requests.cancel(session, req, summary="Incident resolved; responder stood down")
        responder = session.get(Person, req.responder_id) if req.responder_id else None
        if responder:
            send_to_person(
                session,
                responder,
                f"Update from FarCare: {parent.display_name} is okay "
                f"({summaries[resolution].rstrip('.')}). No need to go. Thank you!",
                f"incident:{inc.id}:stand_down:{responder.id}",
            )

    if resolution == IncidentResolution.parent_ok:
        send_to_person(
            session,
            parent,
            "Thank you for letting me know. I'm glad you're okay. 💛",
            f"incident:{inc.id}:parent_ok",
        )
        notify_family(
            session,
            parent,
            lambda who: (
                (
                    f"FarCare: {who} had a possible-fall alert"
                    + (" that was escalated" if was_escalated else "")
                    + " and has now replied that they're okay."
                    if inc.trigger == "fall.suspected"
                    else f"FarCare: I checked in and {who} replied that they're okay. 💛"
                )
                + _demo_suffix(inc)
            ),
            f"incident:{inc.id}:resolved_family",
            emergency=True,
        )
    elif resolution == IncidentResolution.responder_confirmed:
        notify_family(
            session,
            parent,
            lambda who: f"FarCare: {actor} is with {who} and confirmed they're okay. 💛",
            f"incident:{inc.id}:resolved_family",
            emergency=True,
        )


def parent_replied(session: Session, inc: SafetyIncidentRow, answer: str) -> None:
    if answer == "ok":
        resolve(session, inc, IncidentResolution.parent_ok)
    elif answer == "help":
        if inc.status == I.checking:
            escalate(session, inc, EscalationReason.help_requested)
        else:
            parent = session.get(Person, inc.parent_id)
            if parent:
                send_to_person(
                    session,
                    parent,
                    "Help is already on the way and your family knows. If you can, "
                    "call emergency services.",
                    f"incident:{inc.id}:help_again:{utcnow().timestamp():.0f}",
                )


def check_overdue(session: Session) -> int:
    now = utcnow()
    overdue = session.exec(
        select(SafetyIncidentRow).where(
            SafetyIncidentRow.status == I.checking,
            SafetyIncidentRow.check_deadline <= now,  # type: ignore[operator]
        )
    ).all()
    for inc in overdue:
        escalate(session, inc, EscalationReason.no_response)
    return len(overdue)
