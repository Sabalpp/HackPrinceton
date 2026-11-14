"""Invite-first account sessions. Passwords and session tokens never enter logs."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, func, text
from sqlmodel import Session, select

from app.auth import accessible_parent_ids, hash_token
from app.config import get_settings
from app.db import new_id, session_scope, utcnow
from app.experience_models import Account, AuthAttempt, LoginSession
from app.integrations.messaging import is_reachable
from app.models import Person
from app.schemas.contracts import PersonRole, PersonSummary
from app.schemas.experience import SessionInfo

COOKIE = "farcare_session"


def hash_password(password: str, salt: bytes | None = None) -> str:
    """scrypt N=2^15,r=8,p=3; a fresh 128-bit salt per password."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=64 * 1024 * 1024
    )
    return "scrypt-v1$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt, _ = encoded.split("$")
        if scheme != "scrypt-v1":
            return False
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), encoded)
    except (ValueError, TypeError):
        return False


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().cors_origin_list:
        raise HTTPException(403, "This origin is not allowed")


def rate_limit(request: Request, identity: str = "") -> None:
    """Persist attempts even when the login transaction fails; serialize PG buckets."""
    host = request.client.host if request.client else "unknown"
    buckets = [(hash_token("ip:" + host), 40)]
    if identity:
        buckets.append((hash_token("identity:" + identity), 12))
    now = utcnow()
    with session_scope() as session:
        if session.get_bind().dialect.name == "postgresql":
            for key, _ in sorted(buckets):
                lock = int(key[:15], 16)
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        session.exec(delete(AuthAttempt).where(AuthAttempt.created_at < now - timedelta(days=1)))
        for key, maximum in buckets:
            count = session.exec(
                select(func.count())
                .select_from(AuthAttempt)
                .where(
                    AuthAttempt.bucket == key, AuthAttempt.created_at > now - timedelta(minutes=15)
                )
            ).one()
            if count >= maximum:
                raise HTTPException(
                    429,
                    "Too many attempts. Try again in 15 minutes.",
                    headers={"Retry-After": "900"},
                )
        for key, _ in buckets:
            session.add(AuthAttempt(id=new_id("attempt"), bucket=key))


def cookie_identity(session: Session, request: Request, *, mutation: bool = True):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = session.get(LoginSession, hash_token(token))
    if row is None or row.expires_at <= utcnow():
        raise HTTPException(401, "Your session expired. Please sign in again.")
    person = session.get(Person, row.person_id)
    if person is None:
        raise HTTPException(401, "Your session expired. Please sign in again.")
    if mutation and request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, row.csrf_token):
            raise HTTPException(403, "Refresh the page before trying again")
    return person, row


def session_info(session: Session, person: Person | None = None, row: LoginSession | None = None):
    settings = get_settings()
    return SessionInfo(
        authenticated=person is not None,
        person=PersonSummary(
            person_id=person.id,
            display_name=person.display_name,
            role=PersonRole(person.role),
            reachable=is_reachable(person) or session.get(Account, person.id) is not None,
        )
        if person
        else None,
        parent_ids=accessible_parent_ids(session, person) if person else [],
        csrf_token=row.csrf_token if row else None,
        expires_at=row.expires_at if row else None,
        demo_available=settings.demo_mode and settings.app_env != "production",
        demo_session=row.is_demo if row else False,
        registration_available=settings.registration_enabled,
    )


def issue_session(session: Session, person: Person, response: Response, *, demo=False):
    token = secrets.token_urlsafe(32)
    row = LoginSession(
        token_hash=hash_token(token),
        person_id=person.id,
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=get_settings().session_hours),
        is_demo=demo,
    )
    session.add(row)
    # Old expired sessions are removed; active sessions can be revoked by signing out.
    session.exec(delete(LoginSession).where(LoginSession.expires_at < utcnow()))
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="lax",
        max_age=get_settings().session_hours * 3600,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return session_info(session, person, row)


def account_by_email(session: Session, email: str) -> Account | None:
    return session.exec(select(Account).where(Account.email == email)).first()
