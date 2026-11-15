"""Authentication + authorization.

- People authenticate with expiring HttpOnly cookie sessions and mutation CSRF tokens.
- Legacy bearer tokens work only in development/test, never in production.
- Access to a parent's data is decided SERVER-SIDE from FamilyLink rows and the parent's
  sharing permissions. A client-supplied parent_id is never trusted on its own.
- Machine sources (fall detector, health sync) use the X-FarCare-Ingest-Key header.
- Demo controls require an admin token AND DEMO_MODE=true.
"""

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models import FamilyLink, Person
from app.services import timeline


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ParentAccess:
    viewer: Person
    parent_id: str
    share_health: bool
    share_checkins: bool

    @property
    def is_parent_or_admin(self) -> bool:
        return self.viewer.role in ("parent", "admin")

    def can_see(self, visibility: str) -> bool:
        if visibility == timeline.FAMILY:
            return True
        if visibility == timeline.HEALTH:
            return self.share_health
        if visibility == timeline.CHECKIN:
            return self.share_checkins
        return self.is_parent_or_admin

    def visible_kinds(self) -> list[str]:
        return [
            v
            for v in (timeline.FAMILY, timeline.HEALTH, timeline.CHECKIN, timeline.PARENT_ONLY)
            if self.can_see(v)
        ]


def _person_for_token(session: Session, token: str | None) -> Person:
    if get_settings().app_env == "production":
        raise HTTPException(401, "Sign in with your personal account")
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    person = session.exec(select(Person).where(Person.api_token_hash == hash_token(token))).first()
    if person is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid token", headers={"WWW-Authenticate": "Bearer"}
        )
    return person


def current_person(
    request: Request,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Person:
    from app.accounts import cookie_identity

    identity = cookie_identity(session, request)
    if identity:
        return identity[0]
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return _person_for_token(session, token)


def current_person_sse(
    request: Request,
    access_token: str | None = Query(default=None, description="Legacy development/test only"),
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Person:
    from app.accounts import cookie_identity

    identity = cookie_identity(session, request)
    if identity:
        return identity[0]
    token = access_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return _person_for_token(session, token)


def resolve_parent_access(session: Session, viewer: Person, parent_id: str) -> ParentAccess:
    parent = session.get(Person, parent_id)
    if parent is None or parent.role != "parent":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent not found")
    if viewer.id == parent_id:
        return ParentAccess(viewer, parent_id, True, True)
    if viewer.role == "admin" and get_settings().demo_mode:
        return ParentAccess(viewer, parent_id, True, True)
    link = session.exec(
        select(FamilyLink).where(
            FamilyLink.parent_id == parent_id, FamilyLink.member_id == viewer.id
        )
    ).first()
    if link is None:
        # 404 rather than 403 so we don't reveal which parent ids exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent not found")
    return ParentAccess(viewer, parent_id, link.share_health, link.share_checkins)


def accessible_parent_ids(session: Session, viewer: Person) -> list[str]:
    if viewer.role == "parent":
        return [viewer.id]
    if viewer.role == "admin" and get_settings().demo_mode:
        return [p.id for p in session.exec(select(Person).where(Person.role == "parent")).all()]
    links = session.exec(select(FamilyLink).where(FamilyLink.member_id == viewer.id)).all()
    return [link.parent_id for link in links]


def require_ingest_key(x_farcare_ingest_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = settings.ingest_api_key
    if not expected:
        if settings.app_env in ("development", "test"):
            return  # convenient local default; production refuses to start without a key
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Ingestion not configured")
    if not x_farcare_ingest_key or not secrets.compare_digest(x_farcare_ingest_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid ingest key")


def require_demo_admin(viewer: Person = Depends(current_person)) -> Person:
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if viewer.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Demo controls require the admin token")
    return viewer
