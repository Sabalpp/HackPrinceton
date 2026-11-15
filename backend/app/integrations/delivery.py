"""Durable Photon dispatch. Call only inside a worker-locked transaction.

The business transaction commits its outbox intent before any network send.
After an ambiguous network failure or a crash, the exact request is retried with
the same provider key. This is at-least-once submission, not an exactly-once or
device-delivery guarantee. Provider idempotency retention still applies.
"""

import hashlib
import math
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from types import SimpleNamespace

from sqlmodel import Session, select

from app.config import get_settings
from app.db import utcnow
from app.experience_models import Account
from app.integrations.photon.routing import route_error
from app.models import CareCircleMember, CareRequestRow, FamilyLink, OutboundMessage, Person
from app.runtime_models import DeliveryJob

VISIBILITIES = {
    "direct",
    "family",
    "family_context",
    "health",
    "checkin",
    "emergency",
    "responder",
    "responder_emergency",
    "responder_cancellation",
}


def snapshot_context(
    session: Session,
    person: Person,
    *,
    parent_id: str | None,
    visibility: str,
    request_id: str | None,
    offer_only: bool,
) -> dict:
    """Capture authorization requirements, never infer them from message text."""
    if visibility not in VISIBILITIES:
        raise ValueError("Unknown message visibility")
    if parent_id is None and person.role == "parent":
        parent_id = person.id
    account = session.get(Account, person.id)
    context = {
        "parent_id": parent_id,
        "visibility": visibility,
        "recipient_role": person.role,
        "account_required": account is not None or get_settings().app_env == "production",
        "account_created_at": account.created_at if account else None,
        "request_id": request_id,
        "offer_only": offer_only,
        "require_share_health": visibility == "health",
        "require_share_checkins": visibility == "checkin",
        "require_emergency": visibility in ("emergency", "responder_emergency"),
    }
    if parent_id and parent_id != person.id:
        if visibility.startswith("responder"):
            member = session.exec(
                select(CareCircleMember).where(
                    CareCircleMember.parent_id == parent_id,
                    CareCircleMember.responder_id == person.id,
                )
            ).first()
            context["care_circle_id"] = member.id if member else None
        else:
            link = session.exec(
                select(FamilyLink).where(
                    FamilyLink.parent_id == parent_id, FamilyLink.member_id == person.id
                )
            ).first()
            context["family_link_id"] = link.id if link else None
            if link and visibility == "family_context":
                # A family-agent reply may combine every currently shared field.
                context["require_share_health"] = link.share_health
                context["require_share_checkins"] = link.share_checkins
    return context


def authorization_error(session: Session, person: Person | None, context) -> str | None:
    """Check the present grant/account/assignment before any external send."""
    if person is None or person.role != context.recipient_role:
        return "recipient_inactive"
    if context.account_required:
        account = session.get(Account, person.id)
        if account is None or account.created_at != context.account_created_at:
            return "recipient_account_inactive"
    if context.visibility not in VISIBILITIES:
        return "message_context_missing"
    if context.parent_id is None:
        return None if context.visibility == "direct" else "message_context_missing"
    parent = session.get(Person, context.parent_id)
    if parent is None or parent.role != "parent":
        return "parent_inactive"
    if person.id == parent.id:
        return None
    if context.visibility.startswith("responder"):
        member = (
            session.get(CareCircleMember, context.care_circle_id)
            if context.care_circle_id
            else None
        )
        if (
            member is None
            or not member.active
            or member.parent_id != parent.id
            or member.responder_id != person.id
            or (context.require_emergency and not member.emergency_contact)
        ):
            return "care_membership_revoked"
        if context.request_id:
            request = session.get(CareRequestRow, context.request_id)
            approved = request is not None and (
                request.parent_approved
                or (
                    request.origin == "emergency"
                    and request.approval_basis == "emergency_policy"
                    and member.emergency_contact
                )
            )
            if (
                request is None
                or request.parent_id != parent.id
                or request.responder_id != person.id
                or not approved
                or (
                    request.status != "cancelled"
                    if context.visibility == "responder_cancellation"
                    else request.status == "cancelled"
                )
                or (context.offer_only and request.status not in ("approved", "sent"))
                or (request.origin == "emergency" and not member.emergency_contact)
                or (request.origin != "emergency" and request.request_type not in member.categories)
            ):
                return "task_assignment_changed"
        return None
    if context.visibility == "direct":
        return "message_context_missing"
    link = session.get(FamilyLink, context.family_link_id) if context.family_link_id else None
    if (
        link is None
        or link.parent_id != parent.id
        or link.member_id != person.id
        or (context.require_share_health and not link.share_health)
        or (context.require_share_checkins and not link.share_checkins)
        or (context.require_emergency and not link.receives_emergency_alerts)
    ):
        return "family_consent_revoked"
    return None


def message_contexts(session: Session, person: Person, context: dict) -> list[dict]:
    """Retain grants for app history, including task facts in a helper-agent reply."""
    contexts = [context]
    if person.role == "responder" and context["visibility"] == "direct":
        for task in session.exec(
            select(CareRequestRow).where(
                CareRequestRow.responder_id == person.id,
            )
        ).all():
            scope = snapshot_context(
                session,
                person,
                parent_id=task.parent_id,
                visibility="responder_emergency" if task.origin == "emergency" else "responder",
                request_id=task.id,
                offer_only=False,
            )
            view = SimpleNamespace(family_link_id=None, care_circle_id=None)
            view.__dict__.update(scope)
            if authorization_error(session, person, view) is None:
                contexts.append(scope)
    return [
        {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in c.items()
        }
        for c in contexts
    ]


def can_read_message(session: Session, person: Person, message: OutboundMessage) -> bool:
    """Re-check grants on every app read; delivery in the past is not current consent."""
    if message.person_id != person.id or message.status == "cancelled":
        return False
    if not message.access_contexts:
        # Parents can still read their own legacy thread. Do not infer a parent's
        # consent for legacy family/helper messages that have no saved scope.
        return person.role == "parent"
    for saved in message.access_contexts:
        context = SimpleNamespace(family_link_id=None, care_circle_id=None)
        context.__dict__.update(saved)
        if context.account_created_at:
            context.account_created_at = datetime.fromisoformat(context.account_created_at)
        if authorization_error(session, person, context):
            return False
    return True


def enqueue(session: Session, message: OutboundMessage, person: Person, **context) -> None:
    """Snapshot the intended recipient and body in the business transaction."""
    if not person.photon_space_id or not person.photon_agent_user_id:
        raise ValueError("Photon recipient routing is required")
    session.add(
        DeliveryJob(
            id=message.id,
            provider_key="farcare:" + hashlib.sha256(message.id.encode()).hexdigest(),
            person_id=person.id,
            space_id=person.photon_space_id,
            sender_id=person.photon_agent_user_id,
            text=message.text,
            **context,
        )
    )


def retry_delay(attempt: int) -> float:
    settings = get_settings()
    return min(
        settings.worker_retry_max_seconds,
        settings.worker_retry_base_seconds * 2 ** min(max(attempt - 1, 0), 10),
    )


def _failure(exc: Exception) -> tuple[str, bool]:
    """Store a bounded category, never an exception body, token, or phone number."""
    status = getattr(exc, "status", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return f"provider_http_{status}", status in (408, 429) or status >= 500
    return "provider_transport_or_response_error", True


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, dict):
        return None
    raw = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if not isinstance(raw, str):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
            when = when.replace(tzinfo=UTC) if when.tzinfo is None else when
            seconds = (when - utcnow()).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def dispatch(session: Session, job: DeliveryJob) -> str:
    """Try one already-committed job; caller persists outcome under the mutex."""
    if job.status != "pending":
        return job.status
    message = session.get(OutboundMessage, job.id)
    person = session.get(Person, job.person_id)
    if message is None:
        job.status = "failed"
        job.last_error_code = "outbound_missing"
        session.add(job)
        return job.status
    permission_error = authorization_error(session, person, job)
    if (
        permission_error is None
        and message.access_contexts
        and not can_read_message(session, person, message)
    ):
        permission_error = "message_consent_revoked"
    if permission_error is None and person is not None:
        permission_error = route_error(person)
    if permission_error:
        job.status = "cancelled"
        job.last_error_code = permission_error
        message.status = "cancelled"
        message.error = permission_error
    # Never silently retarget an old idempotent request after routing changes.
    elif (
        person is None
        or person.photon_space_id != job.space_id
        or person.photon_agent_user_id != job.sender_id
        or person.reachable_since is None
    ):
        job.status = "cancelled"
        job.last_error_code = "recipient_routing_changed"
        message.status = "cancelled"
        message.error = job.last_error_code
    elif not get_settings().use_photon:
        # Missing credentials must not turn a real queued message into simulated success.
        job.last_error_code = "photon_not_configured"
        job.next_attempt_at = utcnow() + timedelta(seconds=retry_delay(1))
    else:
        from app.integrations.photon import client as photon

        job.attempts += 1
        try:
            provider_id = photon.send_text(
                space_id=job.space_id,
                sender_id=job.sender_id,
                text=job.text,
                idempotency_key=job.provider_key,
            )
            if not provider_id:
                raise ValueError("Missing provider message id")
        except Exception as exc:
            code, retryable = _failure(exc)
            requested_delay = _retry_after(exc)
            if (
                requested_delay is not None
                and requested_delay > get_settings().worker_retry_max_seconds
            ):
                # Do not shorten the provider's requested wait. Operators can
                # inspect and resume this intent after the retry window.
                code, retryable = "provider_retry_after_exceeds_limit", False
            job.last_error_code = code
            message.error = code
            if not retryable or job.attempts >= get_settings().worker_max_delivery_attempts:
                job.status = "failed"
                message.status = "failed"
            else:
                job.next_attempt_at = utcnow() + timedelta(
                    seconds=max(retry_delay(job.attempts), requested_delay or 0)
                )
        else:
            job.status = "accepted"
            job.accepted_at = utcnow()
            job.last_error_code = None
            message.status = "accepted"
            message.provider_message_id = provider_id
            message.error = None
    session.add(job)
    session.add(message)
    return job.status


def opt_out(session: Session, person: Person) -> None:
    """Revoke pending SMS intents in the same transaction as the inbound STOP."""
    person.messaging_opted_out_at = utcnow()
    session.add(person)
    for job in session.exec(
        select(DeliveryJob).where(
            DeliveryJob.person_id == person.id, DeliveryJob.status == "pending"
        )
    ).all():
        job.status = "cancelled"
        job.last_error_code = "recipient_opted_out"
        message = session.get(OutboundMessage, job.id)
        if message is not None:
            message.status = "cancelled"
            message.error = "recipient_opted_out"
            session.add(message)
        session.add(job)


def apply_receipt(session: Session, receipt: dict) -> str:
    """Apply provider evidence to the original intent, never resubmit a failed receipt.

    Out-of-order dispatch receipts cannot downgrade a delivered or failed message.
    A delivery receipt confirms provider delivery, not that a person read or acted on it.
    """
    message = session.exec(
        select(OutboundMessage).where(
            OutboundMessage.channel == "photon",
            OutboundMessage.provider_message_id == receipt["provider_message_id"],
        )
    ).first()
    if message is None:
        return "ignored"
    job = session.get(DeliveryJob, message.id)
    if (
        job is None
        or job.space_id != receipt.get("space_id")
        or job.sender_id != receipt.get("agent_user_id")
    ):
        return "ignored"
    status = receipt.get("status")
    if status not in {"sent", "delivered", "failed"}:
        return "ignored"
    if message.status == "delivered" or (
        message.status in {"failed", "cancelled"} and status == "sent"
    ):
        return "done"
    message.status = job.status = status
    message.error = job.last_error_code = receipt.get("error_code") if status == "failed" else None
    if status == "failed" and receipt.get("error_code") == "recipient_opted_out":
        person = session.get(Person, job.person_id)
        if person is not None:
            opt_out(session, person)
    session.add(message)
    session.add(job)
    return "done"
