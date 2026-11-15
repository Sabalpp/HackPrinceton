"""Append-only timeline. Every state change a family member may see is recorded here."""

from sqlmodel import Session

from app.models import TimelineEventRow
from app.schemas.contracts import TimelineEvent

# visibility values
FAMILY = "family"  # any linked family member
HEALTH = "health"  # requires share_health
CHECKIN = "checkin"  # requires share_checkins
PARENT_ONLY = "parent_only"


def record(
    session: Session,
    parent_id: str,
    kind: str,
    summary: str,
    *,
    ref_id: str | None = None,
    data: dict | None = None,
    visibility: str = FAMILY,
) -> TimelineEventRow:
    row = TimelineEventRow(
        parent_id=parent_id,
        kind=kind,
        summary=summary,
        ref_id=ref_id,
        data=data or {},
        visibility=visibility,
    )
    session.add(row)
    session.flush()
    return row


def to_contract(row: TimelineEventRow) -> TimelineEvent:
    return TimelineEvent(
        id=row.id or 0,
        parent_id=row.parent_id,
        kind=row.kind,
        summary=row.summary,
        ref_id=row.ref_id,
        data=row.data,
        created_at=row.created_at,
    )
