"""Who-gets-told helpers. All outbound text goes through messaging.send_to_person."""

from collections.abc import Callable

from sqlmodel import Session, select

from app.integrations.messaging import send_to_person
from app.models import FamilyLink, OutboundMessage, Person


def family_members(
    session: Session, parent_id: str, *, emergency_only: bool = False
) -> list[tuple[FamilyLink, Person]]:
    stmt = select(FamilyLink, Person).where(
        FamilyLink.parent_id == parent_id, FamilyLink.member_id == Person.id
    )
    if emergency_only:
        stmt = stmt.where(FamilyLink.receives_emergency_alerts == True)  # noqa: E712
    return list(session.exec(stmt).all())


def notify_family(
    session: Session,
    parent: Person,
    render: Callable[[str], str],
    key: str,
    *,
    emergency: bool = False,
) -> list[OutboundMessage]:
    """render(parent_label) -> text, so each member sees e.g. 'Mom' instead of a name."""
    sent = []
    for link, member in family_members(session, parent.id, emergency_only=emergency):
        label = link.parent_label or parent.display_name
        sent.append(send_to_person(session, member, render(label), f"{key}:{member.id}"))
    return sent
