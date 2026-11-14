from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlmodel import Session

from app.auth import accessible_parent_ids, current_person
from app.config import get_settings
from app.db import get_session
from app.integrations.messaging import channel_name, is_reachable
from app.models import Person
from app.schemas.contracts import Me, PersonRole, PersonSummary, SystemHealth

router = APIRouter(tags=["system"])


@router.get("/health", response_model=SystemHealth)
def system_health(session: Session = Depends(get_session)):
    settings = get_settings()
    try:
        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        db = "ok"
    except Exception:
        db = "error"
    return SystemHealth(
        status="ok",
        environment=settings.app_env,
        commit=settings.git_commit or None,
        database=db,  # type: ignore[arg-type]
        messaging=channel_name(),  # type: ignore[arg-type]
        photon_delivery=settings.photon_delivery if settings.use_photon else "off",
        ai="gemini" if settings.gemini_enabled else "fallback",
        demo_mode=settings.demo_mode,
    )


@router.get("/api/me", response_model=Me)
def me(viewer: Person = Depends(current_person), session: Session = Depends(get_session)):
    return Me(
        person=PersonSummary(
            person_id=viewer.id,
            display_name=viewer.display_name,
            role=PersonRole(viewer.role),
            reachable=is_reachable(viewer),
        ),
        parent_ids=accessible_parent_ids(session, viewer),
    )
