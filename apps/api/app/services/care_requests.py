"""Care request lifecycle (Autonomous Care Circle).

created -> awaiting_approval -> approved -> sent -> accepted -> completed
Alternative states: declined, cancelled, expired, failed.
Declined/expired are not dead ends: the next authorized responder can be proposed.

Rules enforced here (never by the LLM):
- Only active Care Circle members authorized for the category are ever contacted.
- Ordinary requests require the parent's explicit YES before anyone is contacted.
- Emergency requests are pre-approved by the parent's emergency policy.
- Responders get the minimum information: parent's name, task, time, task details.
"""

from datetime import timedelta

from sqlmodel import Session, select

from app.config import get_settings
from app.db import new_id, utcnow
from app.integrations.messaging import delivered, is_reachable, send_to_person
from app.models import CareCircleMember, CareRequestRow, FamilyLink, Person
from app.schemas.contracts import (
    AssistanceCategory,
    CareRequest,
    CareRequestOrigin,
    CareRequestStatus,
)
from app.services import timeline
from app.services.notify import notify_family
from app.services.replies import ResponderIntent, parse_eta

S = CareRequestStatus

CATEGORY_LABELS: dict[str, str] = {
    AssistanceCategory.grocery_assistance: "help with groceries",
    AssistanceCategory.transportation: "a ride",
    AssistanceCategory.meal_assistance: "help with a meal",
    AssistanceCategory.medication_pickup: "a medicine pickup",
    AssistanceCategory.household_help: "help around the house",
    AssistanceCategory.companionship: "a visit",
    AssistanceCategory.urgent_check_in: "an urgent check-in",
    AssistanceCategory.other: "some help",
}

ALLOWED: dict[S, set[S]] = {
    S.created: {S.awaiting_approval, S.approved, S.failed, S.cancelled},
    S.awaiting_approval: {S.approved, S.cancelled, S.expired},
    S.approved: {S.sent, S.failed, S.cancelled},
    S.sent: {S.accepted, S.declined, S.expired, S.cancelled, S.failed},
    S.accepted: {S.completed, S.cancelled},
    S.declined: {S.awaiting_approval, S.approved, S.failed, S.cancelled},
    S.expired: {S.awaiting_approval, S.approved, S.failed, S.cancelled},
    S.completed: set(),
    S.cancelled: set(),
    S.failed: set(),
}
OPEN = {S.created, S.awaiting_approval, S.approved, S.sent, S.accepted}


class InvalidTransition(Exception):
    pass


def label(req: CareRequestRow) -> str:
    text = CATEGORY_LABELS.get(req.request_type, "some help")
    return f"{text} ({req.requested_time})" if req.requested_time else text


def transition(
    session: Session, req: CareRequestRow, new: S, *, summary: str, data: dict | None = None
) -> None:
    old = S(req.status)
    if new not in ALLOWED[old]:
        raise InvalidTransition(f"care request {req.id}: {old} -> {new}")
    now = utcnow()
    req.status = new
    req.updated_at = now
    req.status_changed_at = now
    session.add(req)
    timeline.record(
        session,
        req.parent_id,
        "care_request.updated",
        summary,
        ref_id=req.id,
        data={
            "from": old.value,
            "to": new.value,
            "request_type": req.request_type,
            "responder_id": req.responder_id,
            "origin": req.origin,
            **(data or {}),
        },
    )


def to_contract(session: Session, req: CareRequestRow) -> CareRequest:
    responder = session.get(Person, req.responder_id) if req.responder_id else None
    requester = session.get(Person, req.requested_by_id) if req.requested_by_id else None
    return CareRequest(
        request_id=req.id,
        parent_id=req.parent_id,
        request_type=AssistanceCategory(req.request_type),
        status=S(req.status),
        responder_id=req.responder_id,
        responder_name=responder.display_name if responder else None,
        parent_approved=req.parent_approved,
        approval_basis=req.approval_basis,  # type: ignore[arg-type]
        origin=CareRequestOrigin(req.origin),
        details=req.details,
        requested_time=req.requested_time,
        estimated_arrival_minutes=req.eta_minutes,
        responder_note=req.responder_note,
        incident_id=req.incident_id,
        requested_by_id=req.requested_by_id,
        requested_by_name=requester.display_name if requester else None,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


def _relationship(session: Session, parent_id: str, responder_id: str) -> str:
    member = session.exec(
        select(CareCircleMember).where(
            CareCircleMember.parent_id == parent_id, CareCircleMember.responder_id == responder_id
        )
    ).first()
    return member.relationship if member else "contact"


def eligible_responders(
    session: Session, parent_id: str, category: str, *, emergency: bool, exclude: set[str]
) -> list[tuple[CareCircleMember, Person]]:
    rows = session.exec(
        select(CareCircleMember, Person)
        .where(
            CareCircleMember.parent_id == parent_id,
            CareCircleMember.active == True,  # noqa: E712
            CareCircleMember.responder_id == Person.id,
        )
        .order_by(CareCircleMember.priority)
    ).all()
    out = []
    for member, person in rows:
        if person.id in exclude:
            continue
        if emergency and not member.emergency_contact:
            continue
        if not emergency and category not in member.categories:
            continue
        if not is_reachable(person):
            continue
        out.append((member, person))
    return out


# --------------------------------------------------------------------------- ordinary flow


def create_request(
    session: Session,
    parent: Person,
    category: AssistanceCategory,
    details: str = "",
    requested_time: str | None = None,
    requested_by: Person | None = None,
) -> CareRequestRow:
    """`requested_by` = a family member asking on the parent's behalf. The parent still
    approves before anyone is contacted."""
    req = CareRequestRow(
        id=new_id("care"),
        parent_id=parent.id,
        request_type=category.value,
        details=details.strip()[:500],
        requested_time=(requested_time or "").strip()[:80] or None,
        status=S.created,
        origin=CareRequestOrigin.conversation,
        requested_by_id=requested_by.id if requested_by else None,
    )
    session.add(req)
    session.flush()
    timeline.record(
        session,
        parent.id,
        "care_request.created",
        (
            f"{requested_by.display_name} asked FarCare to arrange {label(req)} for "
            f"{parent.display_name}"
            if requested_by
            else f"{parent.display_name} asked for {label(req)}"
        ),
        ref_id=req.id,
        data={"request_type": req.request_type, "requested_time": req.requested_time},
    )
    propose_next(session, req)
    return req


def propose_next(session: Session, req: CareRequestRow) -> bool:
    """Pick the next authorized responder and ask the parent for approval."""
    parent = session.get(Person, req.parent_id)
    assert parent is not None
    candidates = eligible_responders(
        session,
        req.parent_id,
        req.request_type,
        emergency=False,
        exclude=set(req.attempted_responder_ids),
    )
    if not candidates:
        transition(
            session, req, S.failed, summary=f"No available Care Circle member for {label(req)}"
        )
        notify_family(
            session,
            parent,
            lambda who: (
                f"FarCare: {who} asked for {label(req)}, but no one in the Care "
                "Circle is available right now. You may want to arrange help."
            ),
            f"care:{req.id}:no_responder",
        )
        return False
    member, person = candidates[0]
    req.responder_id = person.id
    transition(
        session,
        req,
        S.awaiting_approval,
        summary=(
            f"Waiting for {parent.display_name}'s OK to ask "
            f"{person.display_name} ({member.relationship})"
        ),
    )
    return True


def approval_prompt(session: Session, req: CareRequestRow, mention_task: bool = True) -> str:
    responder = session.get(Person, req.responder_id) if req.responder_id else None
    name = responder.display_name if responder else "someone"
    rel = _relationship(session, req.parent_id, req.responder_id or "")
    task = f" for {label(req)}" if mention_task else ""
    return f"Would you like me to ask {name} (your {rel}){task}? Reply YES or NO."


def pending_approval(session: Session, parent_id: str) -> CareRequestRow | None:
    return session.exec(
        select(CareRequestRow)
        .where(CareRequestRow.parent_id == parent_id, CareRequestRow.status == S.awaiting_approval)
        .order_by(CareRequestRow.status_changed_at.desc())  # type: ignore[union-attr]
    ).first()


def approve(session: Session, req: CareRequestRow) -> bool:
    req.parent_approved = True
    req.approval_basis = "parent_reply"
    transition(session, req, S.approved, summary="Parent approved the request")
    return dispatch(session, req)


def cancel(session: Session, req: CareRequestRow, summary: str) -> None:
    transition(session, req, S.cancelled, summary=summary)


def notify_requester(session: Session, req: CareRequestRow, render, key: str) -> None:
    """Tell the family member who asked on the parent's behalf. render(parent_label) -> text."""
    if not req.requested_by_id:
        return
    requester = session.get(Person, req.requested_by_id)
    if requester is None:
        return
    link = session.exec(
        select(FamilyLink).where(
            FamilyLink.parent_id == req.parent_id, FamilyLink.member_id == requester.id
        )
    ).first()
    parent = session.get(Person, req.parent_id)
    who = (link.parent_label if link and link.parent_label else None) or (
        parent.display_name if parent else "them"
    )
    send_to_person(session, requester, render(who), key)


def dispatch(session: Session, req: CareRequestRow) -> bool:
    """Send an approved ordinary request to its responder."""
    parent = session.get(Person, req.parent_id)
    responder = session.get(Person, req.responder_id) if req.responder_id else None
    assert parent is not None and responder is not None
    # Skip details that just restate the task (e.g. "groceries" for a grocery request).
    redundant = req.details.strip(" .").lower() in label(req).lower()
    details = f" Details: {req.details.strip(' .')}." if req.details and not redundant else ""
    text = (
        f"Hi {responder.display_name}, this is FarCare, helping {parent.display_name}. "
        f"{parent.display_name} is asking for {label(req)}.{details} Can you help? "
        "Reply YES (you can add a time, like 'YES 4pm' or 'YES 20 min') or NO."
    )
    req.attempted_responder_ids = [*req.attempted_responder_ids, responder.id]
    msg = send_to_person(session, responder, text, f"care:{req.id}:ask:{responder.id}")
    if delivered(msg):
        transition(
            session,
            req,
            S.sent,
            summary=f"Asked {responder.display_name} for help",
            data={"channel": msg.channel},
        )
        return True
    transition(
        session,
        req,
        S.failed,
        summary=f"Could not reach {responder.display_name}",
        data={"error": msg.error},
    )
    send_to_person(
        session,
        parent,
        f"I'm sorry, I couldn't reach {responder.display_name}. I've let your family know.",
        f"care:{req.id}:unreachable:parent",
    )
    notify_family(
        session,
        parent,
        lambda who: (
            f"FarCare: {who} asked for {label(req)}, but we couldn't reach "
            f"{responder.display_name}. You may want to follow up."
        ),
        f"care:{req.id}:unreachable",
    )
    return False


# --------------------------------------------------------------------------- emergency flow


def create_emergency_request(
    session: Session, parent: Person, incident_id: str, situation: str
) -> CareRequestRow:
    req = CareRequestRow(
        id=new_id("care"),
        parent_id=parent.id,
        request_type=AssistanceCategory.urgent_check_in.value,
        details=situation[:300],
        status=S.created,
        origin=CareRequestOrigin.emergency,
        incident_id=incident_id,
        approval_basis="emergency_policy",
    )
    session.add(req)
    session.flush()
    transition(
        session,
        req,
        S.approved,
        summary="Emergency policy: urgent check-in pre-approved by parent's settings",
    )
    dispatch_emergency_next(session, req, initial=True)
    return req


def dispatch_emergency_next(session: Session, req: CareRequestRow, initial: bool = False) -> bool:
    """Ask the next emergency-authorized responder. `initial` = called during escalation,
    whose family alert already says when nobody could be reached."""
    from app.services import safety  # local import: safety <-> care_requests

    parent = session.get(Person, req.parent_id)
    assert parent is not None
    num = f" ({parent.emergency_number})" if parent.emergency_number else ""
    candidates = eligible_responders(
        session,
        req.parent_id,
        req.request_type,
        emergency=True,
        exclude=set(req.attempted_responder_ids),
    )
    for member, person in candidates:
        if req.status in (S.declined, S.expired):
            transition(
                session,
                req,
                S.approved,
                summary=f"Emergency policy: asking next responder ({person.display_name})",
            )
        req.responder_id = person.id
        req.attempted_responder_ids = [*req.attempted_responder_ids, person.id]
        text = (
            f"URGENT from FarCare: {parent.display_name} {req.details}. "
            f"Can you check on {parent.display_name} now? Reply YES with how many minutes "
            f"you need (e.g. 'YES 10'), or NO if you can't. If it looks serious, call local "
            f"emergency services{num}."
        )
        msg = send_to_person(session, person, text, f"care:{req.id}:ask:{person.id}")
        if delivered(msg):
            transition(
                session,
                req,
                S.sent,
                summary=f"Urgent request sent to {person.display_name} ({member.relationship})",
                data={"channel": msg.channel},
            )
            return True
        timeline.record(
            session,
            req.parent_id,
            "care_request.delivery_failed",
            f"Could not reach {person.display_name}",
            ref_id=req.id,
            data={"error": msg.error},
        )
    if req.status in (S.declined, S.expired, S.approved):
        transition(session, req, S.failed, summary="No Care Circle responder could be reached")
    if req.incident_id:
        safety.mark_no_responder(session, req.incident_id, notify=not initial)
    return False


# --------------------------------------------------------------------------- responder replies


def active_request_for_responder(session: Session, responder_id: str) -> CareRequestRow | None:
    rows = session.exec(
        select(CareRequestRow).where(
            CareRequestRow.responder_id == responder_id,
            CareRequestRow.status.in_([S.sent, S.accepted]),  # type: ignore[attr-defined]
        )
    ).all()
    if not rows:
        return None
    # Emergencies first, then most recent.
    return sorted(rows, key=lambda r: (r.origin == "emergency", r.status_changed_at))[-1]


def apply_responder_reply(
    session: Session, req: CareRequestRow, responder: Person, intent: ResponderIntent, text: str
) -> None:
    from app.services import safety

    parent = session.get(Person, req.parent_id)
    assert parent is not None
    emergency = req.origin == CareRequestOrigin.emergency
    rel = _relationship(session, parent.id, responder.id)
    num = f" ({parent.emergency_number})" if parent.emergency_number else ""
    key = f"care:{req.id}:{responder.id}:{intent}"

    if intent == "accepted":
        minutes, clock = parse_eta(text)
        req.eta_minutes = minutes
        req.responder_note = clock
        when = f" at {clock}" if clock else (f" in about {minutes} min" if minutes else "")
        transition(
            session,
            req,
            S.accepted,
            summary=f"{responder.display_name} accepted{when}",
            data={"eta_minutes": minutes, "time": clock},
        )
        if emergency:
            send_to_person(
                session,
                responder,
                f"Thank you! I've told {parent.display_name}'s family you're on the "
                "way. Reply DONE once you're with them and they're okay, or NEED "
                "HELP if more help is needed.",
                f"{key}:ack",
            )
            send_to_person(
                session,
                parent,
                f"{responder.display_name} is on the way{when}. Help is coming.",
                f"{key}:parent",
            )
            notify_family(
                session,
                parent,
                lambda who: (
                    f"FarCare: {responder.display_name} ({rel}) is on the "
                    f"way to check on {who}{when}."
                ),
                f"{key}:family",
                emergency=True,
            )
            if req.incident_id:
                safety.mark_responder_en_route(session, req.incident_id, responder, minutes)
        else:
            send_to_person(
                session,
                responder,
                f"Thank you! I've let {parent.display_name} know. Reply DONE when you've finished.",
                f"{key}:ack",
            )
            send_to_person(
                session,
                parent,
                f"Good news: {responder.display_name} said yes to {label(req)}{when}.",
                f"{key}:parent",
            )
            notify_family(
                session,
                parent,
                lambda who: (
                    f"FarCare update: {responder.display_name} ({rel}) "
                    f"accepted {who}'s request for {label(req)}{when}."
                ),
                f"{key}:family",
            )

    elif intent == "declined":
        transition(
            session, req, S.declined, summary=f"{responder.display_name} can't help this time"
        )
        send_to_person(session, responder, "No problem, thanks for letting us know.", f"{key}:ack")
        if emergency:
            dispatch_emergency_next(session, req)
        elif propose_next(session, req):
            send_to_person(
                session,
                parent,
                f"{responder.display_name} can't make it this time. "
                + approval_prompt(session, req),
                f"{key}:parent",
            )
        else:
            send_to_person(
                session,
                parent,
                f"{responder.display_name} can't make it, and no one else in your "
                "Care Circle is available for this. I've let your family know.",
                f"{key}:parent",
            )

    elif intent == "completed":
        transition(
            session,
            req,
            S.completed,
            summary=f"{responder.display_name} marked the request complete",
        )
        send_to_person(session, responder, "Thank you for helping! 💛", f"{key}:ack")
        if emergency and req.incident_id:
            safety.resolve_by_id(
                session, req.incident_id, "responder_confirmed", actor=responder.display_name
            )
        else:
            send_to_person(
                session,
                parent,
                f"{responder.display_name} let me know they're done. I hope that helped!",
                f"{key}:parent",
            )
            notify_family(
                session,
                parent,
                lambda who: (
                    f"FarCare: {who}'s request for {label(req)} is complete, "
                    f"thanks to {responder.display_name}."
                ),
                f"{key}:family",
            )

    elif intent == "need_more_help":
        timeline.record(
            session,
            parent.id,
            "care_request.more_help_needed",
            f"{responder.display_name} reports more help is needed",
            ref_id=req.id,
            data={"message": text[:300]},
        )
        notify_family(
            session,
            parent,
            lambda who: (
                f"URGENT from FarCare: {responder.display_name} says more help "
                f'is needed for {who}: "{text[:200]}". If this is an '
                f"emergency, call local emergency services{num}."
            ),
            f"{key}:{utcnow().timestamp():.0f}:family",
            emergency=True,
        )
        send_to_person(
            session,
            responder,
            f"I've alerted {parent.display_name}'s family. If this is a medical "
            f"emergency, call local emergency services{num} now.",
            f"{key}:{utcnow().timestamp():.0f}:ack",
        )


# --------------------------------------------------------------------------- timeouts


def expire_overdue(session: Session) -> int:
    settings = get_settings()
    now = utcnow()
    count = 0
    rows = session.exec(
        select(CareRequestRow).where(
            CareRequestRow.status.in_([S.awaiting_approval, S.sent])  # type: ignore[attr-defined]
        )
    ).all()
    for req in rows:
        age = now - req.status_changed_at
        if req.status == S.awaiting_approval:
            if age > timedelta(seconds=settings.approval_timeout_seconds):
                transition(session, req, S.expired, summary="Approval window expired")
                count += 1
        elif req.origin == CareRequestOrigin.emergency:
            if age > timedelta(seconds=settings.emergency_responder_timeout_seconds):
                transition(session, req, S.expired, summary="Responder did not reply in time")
                dispatch_emergency_next(session, req)
                count += 1
        elif age > timedelta(seconds=settings.request_reply_timeout_seconds):
            transition(session, req, S.expired, summary="Responder did not reply in time")
            parent = session.get(Person, req.parent_id)
            if propose_next(session, req) and parent:
                send_to_person(
                    session,
                    parent,
                    "I haven't heard back yet. " + approval_prompt(session, req),
                    f"care:{req.id}:expired:{len(req.attempted_responder_ids)}",
                )
            count += 1
    return count


def open_requests(session: Session, parent_id: str) -> list[CareRequestRow]:
    return list(
        session.exec(
            select(CareRequestRow).where(
                CareRequestRow.parent_id == parent_id,
                CareRequestRow.status.in_(list(OPEN)),  # type: ignore[attr-defined]
            )
        ).all()
    )
