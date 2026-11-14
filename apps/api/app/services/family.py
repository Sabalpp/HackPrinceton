"""Family-member (child) features across the Care Circle.

- status_lines: what the child may know, filtered by the parent's sharing permissions.
- request_help: the child asks for help on the parent's behalf. The PARENT is asked for
  consent first; nobody is contacted until the parent replies YES.
- check_on_parent: "Mom isn't answering" opens the same deterministic safety check as a fall.
"""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.db import utcnow
from app.health import service as health
from app.integrations.messaging import delivered, send_to_person
from app.models import CheckInRow, FamilyLink, Person
from app.schemas.contracts import AssistanceCategory, CareRequestStatus
from app.services import care_requests, safety


def links_for(session: Session, member: Person) -> list[tuple[FamilyLink, Person]]:
    rows = session.exec(
        select(FamilyLink, Person).where(
            FamilyLink.member_id == member.id, FamilyLink.parent_id == Person.id
        )
    ).all()
    return list(rows)


def who(link: FamilyLink, parent: Person) -> str:
    return link.parent_label or parent.display_name


def _ago(ts: datetime) -> str:
    minutes = int((utcnow() - ts.astimezone(UTC)).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


def status_lines(session: Session, link: FamilyLink, parent: Person) -> list[str]:
    name = who(link, parent)
    lines: list[str] = []
    inc = safety.active_incident(session, parent.id)
    if inc is not None:
        lines.append(f"{name}: active safety check ({inc.status.replace('_', ' ')}).")
    for r in care_requests.open_requests(session, parent.id)[:3]:
        helper = session.get(Person, r.responder_id) if r.responder_id else None
        by = f" with {helper.display_name}" if helper else ""
        lines.append(f"{name}: {care_requests.label(r)}{by} - {r.status.replace('_', ' ')}.")
    if link.share_checkins:
        latest = session.exec(
            select(CheckInRow)
            .where(CheckInRow.parent_id == parent.id)
            .order_by(CheckInRow.created_at.desc())  # type: ignore[union-attr]
        ).first()
        if latest is not None:
            lines.append(f'Latest check-in ({_ago(latest.created_at)}): "{latest.summary}"')
    if link.share_health:
        summ = health.summary(session, parent.id)
        notable = [t.observation for t in summ.trends if t.notable]
        if notable:
            label = " (simulated data)" if summ.data_mode != "live" else ""
            lines.append(f"Activity: {notable[0]}{label}")
    if inc is None and not care_requests.open_requests(session, parent.id):
        lines.append(f"{name}: no active alerts or open help requests.")
    return lines


def request_help(
    session: Session,
    member: Person,
    link: FamilyLink,
    parent: Person,
    category: AssistanceCategory,
    when: str = "",
    details: str = "",
) -> dict:
    name = who(link, parent)
    if care_requests.pending_approval(session, parent.id) is not None:
        return {
            "status": "parent_already_deciding",
            "note": f"{name} is already deciding on another request. Try again shortly.",
        }
    req = care_requests.create_request(
        session, parent, category, details=details, requested_time=when or None, requested_by=member
    )
    if req.status != CareRequestStatus.awaiting_approval:
        return {
            "status": "no_helper_available",
            "note": "Nobody in the Care Circle can help with this right now.",
        }
    msg = send_to_person(
        session,
        parent,
        f"Hi {parent.display_name}, {member.display_name} asked me to arrange "
        f"{care_requests.label(req)} for you. "
        + care_requests.approval_prompt(session, req, mention_task=False),
        f"care:{req.id}:consent",
    )
    if not delivered(msg):
        care_requests.cancel(session, req, summary=f"Couldn't reach {parent.display_name} to ask")
        return {
            "status": "parent_unreachable",
            "note": f"FarCare couldn't reach {name} to ask for consent.",
        }
    helper = session.get(Person, req.responder_id) if req.responder_id else None
    return {
        "status": "asked_parent_for_consent",
        "proposed_helper": helper.display_name if helper else None,
        "note": f"Nothing happens until {name} says yes.",
    }


def check_on_parent(
    session: Session, member: Person, link: FamilyLink, parent: Person, note: str = ""
) -> dict:
    existing = safety.active_incident(session, parent.id)
    if existing is not None:
        return {"status": "already_active", "incident_status": existing.status}
    safety.open_family_concern(session, parent, member, note)
    return {
        "status": "check_in_sent",
        "note": f"If {who(link, parent)} doesn't answer or asks for help, FarCare alerts "
        "the Care Circle automatically.",
    }
