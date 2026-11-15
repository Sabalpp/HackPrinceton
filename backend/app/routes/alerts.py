"""Heart-rate alert limits: the parent's own, or set by family the parent shares health with."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.auth import current_person, resolve_parent_access
from app.db import get_session
from app.health.live_models import WatchSourceLink
from app.models import HeartRateAlertRow, Person
from app.mutations import action_session
from app.routes.experience import act
from app.schemas.experience import HeartRateAlertIn, HeartRateAlertInfo
from app.services import vitals

router = APIRouter(prefix="/api", tags=["heart-rate alerts"])
ALLOWED_ROLES = ("parent", "child", "admin")


def _info(session: Session, parent_id: str) -> HeartRateAlertInfo:
    row = session.get(HeartRateAlertRow, parent_id)
    link = session.get(WatchSourceLink, parent_id)
    by = session.get(Person, row.updated_by) if row else None
    return HeartRateAlertInfo(
        parent_id=parent_id,
        configured=row is not None,
        enabled=bool(row and row.enabled),
        high_bpm=row.high_bpm if row else None,
        low_bpm=row.low_bpm if row else None,
        sustain_minutes=row.sustain_minutes if row else 2,
        notify_family=row.notify_family if row else True,
        check_on_parent=row.check_on_parent if row else False,
        updated_by_name=by.display_name if by else None,
        updated_at=row.updated_at if row else None,
        last_alert_at=row.last_alert_at if row else None,
        last_alert_bpm=row.last_alert_bpm if row else None,
        watch_linked=link is not None and link.enabled,
    )


def _authorize(session: Session, viewer: Person, parent_id: str) -> Person:
    access = resolve_parent_access(session, viewer, parent_id)
    # Nearby helpers never see or change health settings.
    if viewer.role not in ALLOWED_ROLES:
        raise HTTPException(404, "Parent not found")
    if not access.share_health:
        raise HTTPException(403, "The parent hasn't shared health information with you")
    parent = session.get(Person, parent_id)
    assert parent
    return parent


@router.get("/parents/{parent_id}/heart-rate-alert", response_model=HeartRateAlertInfo)
def get_alert(
    parent_id: str,
    viewer: Person = Depends(current_person),
    session: Session = Depends(get_session),
):
    _authorize(session, viewer, parent_id)
    return _info(session, parent_id)


@router.post("/parents/{parent_id}/heart-rate-alert", response_model=HeartRateAlertInfo)
def save_alert(
    parent_id: str,
    body: HeartRateAlertIn,
    viewer: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    parent = _authorize(session, viewer, parent_id)

    def apply() -> HeartRateAlertInfo:
        try:
            vitals.save_alert(
                session,
                parent,
                viewer,
                high_bpm=body.high_bpm,
                low_bpm=body.low_bpm,
                sustain_minutes=body.sustain_minutes,
                notify_family=body.notify_family,
                check_on_parent=body.check_on_parent,
                enabled=body.enabled,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return _info(session, parent_id)

    return act(session, viewer, f"heart-rate-alert:{parent_id}", body, apply)
