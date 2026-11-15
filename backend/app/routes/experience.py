"""Real role-specific experiences. All care mutations share the worker mutex."""

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.auth import current_person, hash_token, resolve_parent_access
from app.config import get_settings
from app.db import get_session, new_id, utcnow
from app.experience_models import AccessAudit, Account, ActionReceipt, CareProfile, Invitation
from app.integrations.messaging import photon_routed
from app.models import CareCircleMember, CareRequestRow, CheckInRow, FamilyLink, Person
from app.mutations import action_session
from app.routes.parents import _requests, overview
from app.schemas.contracts import AssistanceCategory, CareRequest, CareRequestStatus
from app.schemas.experience import (
    ActionIn,
    AgentStatus,
    CheckInIn,
    CreateInviteIn,
    CreateParentIn,
    HelpIn,
    InviteIssued,
    ParentHome,
    RequestDecisionIn,
    ResponderApprovalIn,
    ResponderMember,
    ResponderReplyIn,
    ResponderTask,
    ResponderTasks,
    SharingIn,
    SharingMember,
)
from app.services import actions, care_requests, safety, timeline
from app.worker import runtime_status

router = APIRouter(prefix="/api", tags=["care experiences"])


def role(person: Person, expected: str) -> None:
    if person.role != expected:
        raise HTTPException(403, "This action is not available to your role")


def agent_status(session: Session, person: Person) -> AgentStatus:
    settings = get_settings()
    runtime = runtime_status(session)
    last = runtime["last_success_at"]
    healthy = runtime["worker_healthy"]
    status = "healthy" if healthy else "stale" if last else "offline"
    if healthy and (runtime["inbound_failed"] or runtime["delivery_failed"]):
        status = "starting"
    messaging = "photon" if settings.use_photon else "console"
    return AgentStatus(
        status=status,
        last_heartbeat_at=last,
        messaging=messaging,
        messaging_ready=settings.use_photon and photon_routed(person),
        messaging_platform=settings.photon_platform if settings.use_photon else None,
        messaging_opted_out=person.messaging_opted_out_at is not None,
        detail=(
            "The worker completed a recent cycle. An accepted message is not a delivery receipt."
            if healthy
            else "The agent has no recent heartbeat. Do not rely on automatic outreach."
        )
        + (
            " External messaging is simulated in this environment."
            if messaging == "console"
            else ""
        )
        + " Registered helpers can also receive assigned tasks inside their app.",
    )


def act(session: Session, person: Person, operation: str, body: ActionIn, callback: Callable):
    identity = hash_token(person.id + "\0" + body.action_id)
    digest = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    existing = session.get(ActionReceipt, identity)
    if existing:
        if existing.operation != operation or existing.payload_hash != digest:
            raise HTTPException(409, "This action identifier was already used for different input")
        if "invitation_token" in existing.result:
            invite = session.get(Invitation, hash_token(existing.result["invitation_token"]))
            if not invite or invite.consumed_at is not None or invite.expires_at <= utcnow():
                raise HTTPException(
                    409, "This invitation was already accepted or expired. Refresh the page."
                )
        return existing.result
    result = callback()
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    session.add(
        ActionReceipt(
            id=identity,
            person_id=person.id,
            operation=operation,
            payload_hash=digest,
            result=payload,
        )
    )
    return payload


def sharing_members(session: Session, parent_id: str) -> list[SharingMember]:
    return [
        SharingMember(
            member_id=p.id,
            display_name=p.display_name,
            relationship=link.relationship,
            share_health=link.share_health,
            share_checkins=link.share_checkins,
        )
        for link, p in session.exec(
            select(FamilyLink, Person).where(
                FamilyLink.parent_id == parent_id, FamilyLink.member_id == Person.id
            )
        ).all()
    ]


def parent_home(session: Session, parent: Person) -> ParentHome:
    acc = resolve_parent_access(session, parent, parent.id)
    data = overview(acc, session)
    responders = [
        ResponderMember(
            responder_id=p.id,
            display_name=p.display_name,
            relationship=member.relationship,
            active=member.active,
            emergency_contact=member.emergency_contact,
        )
        for member, p in session.exec(
            select(CareCircleMember, Person).where(
                CareCircleMember.parent_id == parent.id, CareCircleMember.responder_id == Person.id
            )
        ).all()
    ]
    return ParentHome(
        parent=data.parent,
        requests=_requests(session, parent.id, 50),
        active_incident=data.active_incidents[0] if data.active_incidents else None,
        latest_checkin=data.latest_checkin,
        health=data.health,
        sharing=sharing_members(session, parent.id),
        responders=responders,
        agent=agent_status(session, parent),
    )


@router.get("/parent/home", response_model=ParentHome)
def get_parent_home(
    person: Person = Depends(current_person), session: Session = Depends(get_session)
):
    role(person, "parent")
    return parent_home(session, person)


@router.post("/parent/check-in", response_model=ParentHome)
def check_in(
    body: CheckInIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")

    def apply():
        incident = safety.active_incident(session, person.id)
        if body.response == "help":
            if incident:
                safety.parent_replied(session, incident, "help")
            else:
                safety.open_from_message(
                    session, person, "HELP — urgent help requested in the parent app"
                )
        else:
            if incident:
                safety.parent_replied(session, incident, "ok")
            check = CheckInRow(
                id=new_id("checkin"),
                parent_id=person.id,
                summary="I checked in and said I am okay." + (" " + body.note if body.note else ""),
            )
            session.add(check)
            timeline.record(
                session,
                person.id,
                "checkin.completed",
                "Parent checked in and said they are okay.",
                ref_id=check.id,
                visibility=timeline.CHECKIN,
            )
        session.flush()
        return parent_home(session, person)

    return act(session, person, "check-in", body, apply)


@router.post("/parent/requests", response_model=CareRequest)
def request_help(
    body: HelpIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")
    if body.request_type == AssistanceCategory.urgent_check_in:
        raise HTTPException(422, "Use the urgent help action for urgent assistance")

    def apply():
        row = care_requests.create_request(
            session, person, body.request_type, body.details, body.requested_time
        )
        return care_requests.to_contract(session, row)

    return act(session, person, "request-help", body, apply)


@router.post("/parent/requests/{request_id}/decision", response_model=CareRequest)
def request_decision(
    request_id: str,
    body: RequestDecisionIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")
    row = session.get(CareRequestRow, request_id)
    if not row or row.parent_id != person.id:
        raise HTTPException(404, "Request not found")

    def apply():
        if body.decision == "approve":
            if row.status != CareRequestStatus.awaiting_approval:
                raise HTTPException(409, "This request is no longer awaiting approval")
            care_requests.approve(session, row)
        else:
            if row.status not in care_requests.OPEN:
                raise HTTPException(409, "This request is already closed")
            care_requests.cancel(session, row, "Parent cancelled the request in their app")
        return care_requests.to_contract(session, row)

    return act(session, person, "request-decision:" + request_id, body, apply)


def responder_task(session: Session, row: CareRequestRow) -> ResponderTask:
    parent = session.get(Person, row.parent_id)
    return ResponderTask(
        request_id=row.id,
        parent_first_name=parent.display_name.split()[0] if parent else "Parent",
        request_type=row.request_type,
        details=row.details,
        requested_time=row.requested_time,
        status=row.status,
        estimated_arrival_minutes=row.eta_minutes,
        updated_at=row.updated_at,
    )


def assigned_task(session: Session, person: Person, request_id: str) -> CareRequestRow:
    try:
        return actions.assigned_task(session, person, request_id)
    except actions.ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/responder/tasks", response_model=ResponderTasks)
def get_tasks(person: Person = Depends(current_person), session: Session = Depends(get_session)):
    role(person, "responder")
    rows = actions.responder_tasks(session, person)
    return ResponderTasks(
        tasks=[responder_task(session, row) for row in rows], agent=agent_status(session, person)
    )


@router.post("/responder/tasks/{request_id}/reply", response_model=ResponderTask)
def reply_to_task(
    request_id: str,
    body: ResponderReplyIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "responder")

    def apply():
        try:
            row = actions.respond_to_task(
                session, person, request_id, body.decision, body.eta_minutes, body.note
            )
        except actions.ActionError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return responder_task(session, row)

    return act(session, person, "task-reply:" + request_id, body, apply)


@router.post("/parent/sharing", response_model=SharingMember)
def set_sharing(
    body: SharingIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")
    try:
        actions.set_sharing(session, person, body.member_id, body.share_health, body.share_checkins)
    except actions.ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return next(
        member
        for member in sharing_members(session, person.id)
        if member.member_id == body.member_id
    )


@router.post("/parent/responders/{responder_id}/approve")
def approve_responder(
    responder_id: str,
    body: ResponderApprovalIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "parent")
    member = session.exec(
        select(CareCircleMember).where(
            CareCircleMember.parent_id == person.id, CareCircleMember.responder_id == responder_id
        )
    ).first()
    if not member:
        raise HTTPException(404, "Responder not found")
    member.active, member.emergency_contact = body.active, body.emergency_contact
    session.add(member)
    session.add(
        AccessAudit(
            id=new_id("audit"),
            actor_id=person.id,
            parent_id=person.id,
            action="responder.consent_changed",
        )
    )
    return {"approved": member.active}


def issue_invite(session: Session, person: Person, parent_id: str, creator: Person) -> InviteIssued:
    if session.get(Account, person.id):
        raise HTTPException(409, "This person already has an account")
    token = secrets.token_urlsafe(32)
    # Only the newest invitation remains valid for an unclaimed profile.
    for prior in session.exec(
        select(Invitation).where(
            Invitation.person_id == person.id, Invitation.consumed_at.is_(None)
        )
    ).all():
        prior.expires_at = utcnow()
        session.add(prior)
    row = Invitation(
        token_hash=hash_token(token),
        person_id=person.id,
        parent_id=parent_id,
        created_by_id=creator.id,
        role=person.role,
        expires_at=utcnow() + timedelta(days=2),
    )
    session.add(row)
    return InviteIssued(
        parent_id=parent_id,
        invitation_token=token,
        expires_at=row.expires_at,
        role=person.role,
        display_name=person.display_name,
    )


@router.post("/onboarding/parents", response_model=InviteIssued, status_code=201)
def create_parent(
    body: CreateParentIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    role(person, "child")

    def apply():
        parent = Person(
            id=new_id("parent"),
            role="parent",
            display_name=body.display_name,
            timezone=body.timezone,
            emergency_number="911" if body.country_code == "US" else None,
        )
        session.add(parent)
        session.flush()
        session.add(
            CareProfile(parent_id=parent.id, owner_id=person.id, country_code=body.country_code)
        )
        session.add(
            FamilyLink(
                parent_id=parent.id,
                member_id=person.id,
                relationship="family",
                parent_label=body.display_name,
                share_health=False,
                share_checkins=False,
            )
        )
        return issue_invite(session, parent, parent.id, person)

    return act(session, person, "create-parent", body, apply)


@router.post("/onboarding/invites", response_model=InviteIssued, status_code=201)
def create_invite(
    body: CreateInviteIn,
    person: Person = Depends(current_person),
    session: Session = Depends(action_session),
):
    profile = session.get(CareProfile, body.parent_id)
    if person.id != body.parent_id and (not profile or profile.owner_id != person.id):
        raise HTTPException(404, "Parent not found")
    parent = session.get(Person, body.parent_id)
    if not parent or parent.role != "parent":
        raise HTTPException(404, "Parent not found")

    def apply():
        if body.role == "parent":
            return issue_invite(session, parent, parent.id, person)
        if not body.display_name or not body.categories:
            raise HTTPException(
                422, "Enter a responder name and the kinds of help they can provide"
            )
        responder = Person(
            id=new_id("responder"),
            role="responder",
            display_name=body.display_name,
            timezone=parent.timezone,
        )
        session.add(responder)
        session.flush()
        approved = person.id == parent.id
        session.add(
            CareCircleMember(
                parent_id=parent.id,
                responder_id=responder.id,
                relationship=body.relationship,
                categories=[c.value for c in body.categories],
                active=approved,
                emergency_contact=body.emergency_contact if approved else False,
            )
        )
        return issue_invite(session, responder, parent.id, person)

    return act(session, person, "create-invite", body, apply)
