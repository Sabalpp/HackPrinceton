"""Care actions shared by the app (HTTP routes) and the AI agents (text messages).

One implementation, two surfaces: a button in the app and a text to FarCare call the same
functions, so they always agree. Who-may-do-what rules live here, not in the callers.
Callers map ActionError subclasses to their own responses (HTTP status or a chat reply).
"""

from sqlmodel import Session, select

from app.db import new_id
from app.experience_models import AccessAudit
from app.integrations.messaging import send_to_person
from app.models import CareCircleMember, CareRequestRow, CheckInRow, FamilyLink, Person
from app.schemas.contracts import CareRequestOrigin, CareRequestStatus
from app.services import care_requests, safety, timeline

S = CareRequestStatus
TASK_STATUSES = ["sent", "accepted", "completed", "declined", "cancelled", "expired"]


class ActionError(Exception):
    status = 400


class NotFound(ActionError):
    status = 404


class Conflict(ActionError):
    status = 409


# --------------------------------------------------------------------------- responders


def _membership(session: Session, row: CareRequestRow, responder: Person):
    return session.exec(
        select(CareCircleMember).where(
            CareCircleMember.parent_id == row.parent_id,
            CareCircleMember.responder_id == responder.id,
            CareCircleMember.active == True,  # noqa: E712
        )
    ).first()


def assigned_task(session: Session, responder: Person, request_id: str) -> CareRequestRow:
    """A task this helper may see: assigned to them, still in the circle, and approved by the
    parent (or pre-approved by the emergency policy for an emergency contact)."""
    row = session.get(CareRequestRow, request_id)
    member = _membership(session, row, responder) if row else None
    approved = row and (
        row.parent_approved
        or (
            row.origin == CareRequestOrigin.emergency
            and row.approval_basis == "emergency_policy"
            and member is not None
            and member.emergency_contact
        )
    )
    if not row or row.responder_id != responder.id or member is None or not approved:
        raise NotFound("Task not found")
    return row


def responder_tasks(
    session: Session, responder: Person, *, open_only: bool = False
) -> list[CareRequestRow]:
    rows = session.exec(
        select(CareRequestRow)
        .join(
            CareCircleMember,
            (CareCircleMember.parent_id == CareRequestRow.parent_id)
            & (CareCircleMember.responder_id == CareRequestRow.responder_id),
        )
        .where(
            CareRequestRow.responder_id == responder.id,
            (
                (CareRequestRow.parent_approved == True)  # noqa: E712
                | (
                    (CareRequestRow.origin == "emergency")
                    & (CareRequestRow.approval_basis == "emergency_policy")
                    & CareCircleMember.emergency_contact.is_(True)
                )
            ),
            CareCircleMember.active == True,  # noqa: E712
            CareRequestRow.status.in_(["sent", "accepted"] if open_only else TASK_STATUSES),  # type: ignore[attr-defined]
        )
        .order_by(CareRequestRow.updated_at.desc())  # type: ignore[union-attr]
        .limit(50)
    ).all()
    return list(rows)


def respond_to_task(
    session: Session,
    responder: Person,
    request_id: str,
    decision: str,
    eta_minutes: int | None = None,
    note: str = "",
) -> CareRequestRow:
    """decision: accept | decline | complete."""
    row = assigned_task(session, responder, request_id)
    required = "accepted" if decision == "complete" else "sent"
    if row.status != required:
        raise Conflict("This task changed. Refresh before replying.")
    intent = {"accept": "accepted", "decline": "declined", "complete": "completed"}[decision]
    message = f"Yes, in {eta_minutes} minutes" if eta_minutes and decision == "accept" else note
    care_requests.apply_responder_reply(session, row, responder, intent, message)  # type: ignore[arg-type]
    return row


# --------------------------------------------------------------------------- parents


def set_sharing(
    session: Session, parent: Person, member_id: str, share_health: bool, share_checkins: bool
) -> FamilyLink:
    link = session.exec(
        select(FamilyLink).where(
            FamilyLink.parent_id == parent.id, FamilyLink.member_id == member_id
        )
    ).first()
    if not link:
        raise NotFound("Family member not found")
    link.share_health, link.share_checkins = share_health, share_checkins
    session.add(link)
    session.add(
        AccessAudit(
            id=new_id("audit"), actor_id=parent.id, parent_id=parent.id, action="sharing.changed"
        )
    )
    timeline.record(
        session,
        parent.id,
        "sharing.changed",
        "The parent updated sharing preferences.",
        visibility=timeline.FAMILY,
    )
    session.flush()
    return link


def cancel_request(session: Session, parent: Person, request_id: str) -> CareRequestRow:
    """The parent withdraws one of their own open requests; a helper already asked is told."""
    row = session.get(CareRequestRow, request_id)
    if row is None or row.parent_id != parent.id or row.origin != CareRequestOrigin.conversation:
        raise NotFound("Request not found")
    if row.status not in (S.awaiting_approval, S.approved, S.sent, S.accepted):
        raise Conflict("This request is already finished.")
    helper = session.get(Person, row.responder_id) if row.responder_id else None
    if row.status in (S.sent, S.accepted) and helper is not None:
        # Tell the helper BEFORE cancelling: delivery rules block task messages about a
        # request that is already cancelled.
        send_to_person(
            session,
            helper,
            f"Update from FarCare: {parent.display_name} no longer needs "
            f"{care_requests.label(row)}. Thank you for being ready to help!",
            f"care:{row.id}:cancelled:{helper.id}",
            parent_id=parent.id,
            visibility="responder",
            request_id=row.id,
        )
    care_requests.cancel(session, row, summary=f"{parent.display_name} cancelled the request")
    return row


def parent_status(session: Session, parent: Person) -> dict:
    """What the parent would see on their app home, as plain facts for the agent."""
    requests = []
    for r in care_requests.open_requests(session, parent.id):
        helper = session.get(Person, r.responder_id) if r.responder_id else None
        requests.append(
            {
                "request_id": r.id,
                "what": care_requests.label(r),
                "status": r.status.replace("_", " "),
                "helper": helper.display_name if helper else None,
                "time": r.responder_note,
            }
        )
    incident = safety.active_incident(session, parent.id)
    latest = session.exec(
        select(CheckInRow)
        .where(CheckInRow.parent_id == parent.id)
        .order_by(CheckInRow.created_at.desc())  # type: ignore[union-attr]
    ).first()
    return {
        "open_requests": requests,
        "active_safety_check": incident.status.replace("_", " ") if incident else None,
        "latest_checkin": latest.summary if latest else None,
    }


def family_by_name(session: Session, parent: Person, name: str) -> FamilyLink | None:
    """Resolve 'Aarav', 'my son', 'him' (if only one) to the parent's own family link."""
    rows = session.exec(
        select(FamilyLink, Person).where(
            FamilyLink.parent_id == parent.id, FamilyLink.member_id == Person.id
        )
    ).all()
    wanted = name.strip().lower()
    for link, member in rows:
        if wanted and (
            wanted in member.display_name.lower() or wanted in (link.relationship or "").lower()
        ):
            return link
    return rows[0][0] if len(rows) == 1 else None
