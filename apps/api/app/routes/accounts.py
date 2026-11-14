"""Per-person authentication and consent-bound one-time invitation redemption."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import accounts
from app.auth import hash_token
from app.config import get_settings
from app.db import get_session, new_id, utcnow
from app.experience_models import AccessAudit, Account, Invitation
from app.models import FamilyLink, Person
from app.schemas.experience import (
    DemoLoginIn,
    InvitationInfo,
    InvitationLookup,
    LoginIn,
    RegisterIn,
    SessionInfo,
)

router = APIRouter(prefix="/api/auth", tags=["accounts"])


def invitation(session: Session, token: str, *, lock=False):
    statement = select(Invitation).where(Invitation.token_hash == hash_token(token))
    if lock:
        statement = statement.with_for_update()
    row = session.exec(statement).first()
    if row is None or row.consumed_at is not None or row.expires_at <= utcnow():
        raise HTTPException(400, "This invitation is invalid or expired")
    person = session.get(Person, row.person_id)
    if person is None or person.role != row.role or session.get(Account, person.id):
        raise HTTPException(400, "This invitation is invalid or expired")
    return row, person


@router.get("/session", response_model=SessionInfo)
def get_auth_session(request: Request, response: Response, session: Session = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        identity = accounts.cookie_identity(session, request, mutation=False)
    except HTTPException:
        response.delete_cookie(accounts.COOKIE, path="/")
        identity = None
    return accounts.session_info(session, *identity) if identity else accounts.session_info(session)


@router.post("/login", response_model=SessionInfo)
def login(
    body: LoginIn, request: Request, response: Response, session: Session = Depends(get_session)
):
    accounts.check_origin(request)
    accounts.rate_limit(request, body.email)
    account = accounts.account_by_email(session, body.email)
    # Perform the same expensive KDF when an account is absent.
    valid = accounts.verify_password(body.password, account.password_hash) if account else False
    if account is None:
        accounts.hash_password(body.password, salt=bytes(16))
    if not account or not valid:
        raise HTTPException(401, "Email or password was not recognized")
    person = session.get(Person, account.person_id)
    if person is None:
        raise HTTPException(401, "Email or password was not recognized")
    return accounts.issue_session(session, person, response)


@router.post("/register", response_model=SessionInfo, status_code=201)
def register(
    body: RegisterIn, request: Request, response: Response, session: Session = Depends(get_session)
):
    accounts.check_origin(request)
    accounts.rate_limit(request, body.email)
    if not body.invitation_token and not get_settings().registration_enabled:
        raise HTTPException(403, "An invitation is required during the private pilot")
    if accounts.account_by_email(session, body.email):
        raise HTTPException(400, "Unable to create an account with those details. Try signing in.")
    invite = None
    if body.invitation_token:
        invite, person = invitation(session, body.invitation_token, lock=True)
    else:
        person = Person(
            id=new_id("person"),
            role="child",
            display_name=body.display_name.strip(),
            timezone="UTC",
        )
        session.add(person)
        session.flush()
    person.display_name = body.display_name.strip()
    if not person.display_name:
        raise HTTPException(422, "Enter your name")
    session.add(person)
    session.add(
        Account(
            person_id=person.id,
            email=body.email,
            password_hash=accounts.hash_password(body.password),
        )
    )
    if invite:
        invite.consumed_at = utcnow()
        session.add(invite)
        if invite.role == "parent":
            for link in session.exec(
                select(FamilyLink).where(FamilyLink.parent_id == person.id)
            ).all():
                link.share_health = body.share_health
                link.share_checkins = body.share_checkins
                session.add(link)
        session.add(
            AccessAudit(
                id=new_id("audit"),
                actor_id=person.id,
                parent_id=invite.parent_id,
                action="invitation.accepted",
            )
        )
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "This account or invitation was already used") from None
    return accounts.issue_session(session, person, response)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    identity = accounts.cookie_identity(session, request)
    if identity:
        session.delete(identity[1])
    response.delete_cookie(accounts.COOKIE, path="/")


@router.post("/invitation", response_model=InvitationInfo)
def describe_invitation(
    body: InvitationLookup, request: Request, session: Session = Depends(get_session)
):
    accounts.check_origin(request)
    accounts.rate_limit(request)
    row, person = invitation(session, body.token)
    owner = session.get(Person, row.created_by_id)
    return InvitationInfo(
        role=row.role,
        display_name=person.display_name,
        invited_by=owner.display_name if owner else "Your family",
        expires_at=row.expires_at,
    )


@router.post("/demo", response_model=SessionInfo)
def demo_session(
    body: DemoLoginIn, request: Request, response: Response, session: Session = Depends(get_session)
):
    settings = get_settings()
    if settings.app_env == "production" or not settings.demo_mode:
        raise HTTPException(404, "Not found")
    accounts.check_origin(request)
    accounts.rate_limit(request)
    person = session.get(
        Person,
        {"parent": "parent_001", "child": "child_001", "responder": "responder_001"}[body.role],
    )
    if person is None:
        raise HTTPException(503, "Demo people are not configured")
    return accounts.issue_session(session, person, response, demo=True)
