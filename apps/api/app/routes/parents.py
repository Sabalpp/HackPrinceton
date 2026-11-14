"""Family dashboard read API. Every route resolves access server-side (auth.resolve_parent_access)."""

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.auth import (
    ParentAccess,
    current_person,
    current_person_sse,
    resolve_parent_access,
)
from app.db import get_session, session_scope
from app.health import service as health
from app.integrations.messaging import is_reachable
from app.models import (
    CareCircleMember as CircleRow,
)
from app.models import (
    CareRequestRow,
    CheckInRow,
    Person,
    SafetyIncidentRow,
    TimelineEventRow,
)
from app.schemas.contracts import (
    AssistanceCategory,
    CareCircleMember,
    CareRequest,
    CheckIn,
    HealthSummary,
    IncidentResolution,
    ParentOverview,
    ParentProfile,
    PersonRole,
    SafetyIncident,
    TimelineEvent,
)
from app.services import care_requests, safety, timeline

router = APIRouter(prefix="/api/parents/{parent_id}", tags=["family dashboard"])


def access(
    parent_id: str,
    viewer: Person = Depends(current_person),
    session: Session = Depends(get_session),
) -> ParentAccess:
    return resolve_parent_access(session, viewer, parent_id)


def _circle(session: Session, parent_id: str) -> list[CareCircleMember]:
    rows = session.exec(
        select(CircleRow, Person)
        .where(
            CircleRow.parent_id == parent_id,
            CircleRow.active == True,  # noqa: E712
            CircleRow.responder_id == Person.id,
        )
        .order_by(CircleRow.priority)
    ).all()
    return [
        CareCircleMember(
            responder_id=p.id,
            display_name=p.display_name,
            relationship=m.relationship,
            categories=[AssistanceCategory(c) for c in m.categories],
            emergency_contact=m.emergency_contact,
            priority=m.priority,
            reachable=is_reachable(p),
        )
        for m, p in rows
    ]


def _requests(session: Session, parent_id: str, limit: int) -> list[CareRequest]:
    rows = session.exec(
        select(CareRequestRow)
        .where(CareRequestRow.parent_id == parent_id)
        .order_by(CareRequestRow.created_at.desc())
        .limit(limit)  # type: ignore[union-attr]
    ).all()
    return [care_requests.to_contract(session, r) for r in rows]


def _incidents(
    session: Session, parent_id: str, active_only: bool, limit: int = 20
) -> list[SafetyIncident]:
    stmt = select(SafetyIncidentRow).where(SafetyIncidentRow.parent_id == parent_id)
    if active_only:
        stmt = stmt.where(SafetyIncidentRow.status.in_(safety.ACTIVE))  # type: ignore[attr-defined]
    rows = session.exec(stmt.order_by(SafetyIncidentRow.opened_at.desc()).limit(limit)).all()  # type: ignore[union-attr]
    return [safety.to_contract(r) for r in rows]


@router.get("/overview", response_model=ParentOverview)
def overview(acc: ParentAccess = Depends(access), session: Session = Depends(get_session)):
    parent = session.get(Person, acc.parent_id)
    assert parent is not None
    latest = None
    if acc.share_checkins:
        row = session.exec(
            select(CheckInRow)
            .where(CheckInRow.parent_id == parent.id)
            .order_by(CheckInRow.created_at.desc())  # type: ignore[union-attr]
        ).first()
        if row:
            latest = CheckIn(
                checkin_id=row.id,
                parent_id=row.parent_id,
                summary=row.summary,
                mood=row.mood,
                energy=row.energy,
                created_at=row.created_at,
            )  # type: ignore[arg-type]
    return ParentOverview(
        parent=ParentProfile(
            parent_id=parent.id,
            display_name=parent.display_name,
            timezone=parent.timezone,
            emergency_number=parent.emergency_number,
        ),
        viewer_role=PersonRole(acc.viewer.role),
        permissions={"share_health": acc.share_health, "share_checkins": acc.share_checkins},
        latest_checkin=latest,
        health=health.summary(session, parent.id) if acc.share_health else None,
        active_incidents=_incidents(session, parent.id, active_only=True),
        recent_requests=_requests(session, parent.id, 10),
        care_circle=_circle(session, parent.id),
    )


@router.get("/health", response_model=HealthSummary | None)
def health_summary(
    days: int = Query(default=35, ge=7, le=120),
    acc: ParentAccess = Depends(access),
    session: Session = Depends(get_session),
):
    return health.summary(session, acc.parent_id, days) if acc.share_health else None


@router.get("/care-circle", response_model=list[CareCircleMember])
def care_circle(acc: ParentAccess = Depends(access), session: Session = Depends(get_session)):
    return _circle(session, acc.parent_id)


@router.get("/care-requests", response_model=list[CareRequest])
def list_requests(
    limit: int = Query(default=50, ge=1, le=200),
    acc: ParentAccess = Depends(access),
    session: Session = Depends(get_session),
):
    return _requests(session, acc.parent_id, limit)


@router.get("/incidents", response_model=list[SafetyIncident])
def list_incidents(
    active_only: bool = False,
    acc: ParentAccess = Depends(access),
    session: Session = Depends(get_session),
):
    return _incidents(session, acc.parent_id, active_only, limit=50)


@router.post("/incidents/{incident_id}/resolve", response_model=SafetyIncident)
def resolve_incident(
    incident_id: str, acc: ParentAccess = Depends(access), session: Session = Depends(get_session)
):
    from fastapi import HTTPException

    inc = session.get(SafetyIncidentRow, incident_id)
    if inc is None or inc.parent_id != acc.parent_id:
        raise HTTPException(404, "Incident not found")
    safety.resolve(session, inc, IncidentResolution.family_resolved, actor=acc.viewer.display_name)
    return safety.to_contract(inc)


@router.get("/timeline", response_model=list[TimelineEvent])
def get_timeline(
    after: int = 0,
    limit: int = Query(default=100, ge=1, le=500),
    acc: ParentAccess = Depends(access),
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(TimelineEventRow)
        .where(
            TimelineEventRow.parent_id == acc.parent_id,
            TimelineEventRow.id > after,  # type: ignore[operator]
            TimelineEventRow.visibility.in_(acc.visible_kinds()),
        )  # type: ignore[attr-defined]
        .order_by(TimelineEventRow.id.desc())
        .limit(limit)  # type: ignore[union-attr]
    ).all()
    return [timeline.to_contract(r) for r in rows]


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(
    parent_id: str,
    request: Request,
    after: int | None = None,
    viewer: Person = Depends(current_person_sse),
):
    """Server-Sent Events. Each event: `event: timeline` + JSON TimelineEvent.

    EventSource can't set headers, so pass ?access_token=... . Reconnects resume via
    the standard Last-Event-ID header.
    """
    with session_scope() as s:
        acc = resolve_parent_access(s, viewer, parent_id)
        last = after
        if last is None:
            header = request.headers.get("last-event-id")
            last = int(header) if header and header.isdigit() else None
        if last is None:  # start from "now"
            newest = s.exec(
                select(TimelineEventRow.id)
                .where(TimelineEventRow.parent_id == parent_id)
                .order_by(TimelineEventRow.id.desc())
            ).first()  # type: ignore[union-attr]
            last = newest or 0
    kinds = acc.visible_kinds()

    def fetch(cursor: int) -> list[TimelineEvent]:
        with session_scope() as s:
            rows = s.exec(
                select(TimelineEventRow)
                .where(
                    TimelineEventRow.parent_id == parent_id,
                    TimelineEventRow.id > cursor,  # type: ignore[operator]
                    TimelineEventRow.visibility.in_(kinds),
                )  # type: ignore[attr-defined]
                .order_by(TimelineEventRow.id)
                .limit(100)  # type: ignore[arg-type]
            ).all()
            return [timeline.to_contract(r) for r in rows]

    async def events():
        cursor = last
        idle = 0
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(fetch, cursor)
            for ev in batch:
                cursor = ev.id
                yield f"id: {ev.id}\nevent: timeline\ndata: {json.dumps(ev.model_dump(mode='json'))}\n\n"
            idle = 0 if batch else idle + 1
            if idle >= 15:
                idle = 0
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
