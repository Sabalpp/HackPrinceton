"""Outbound messaging gateway.

`send_to_person` is the ONLY way services send messages. It is idempotent (keyed by
idempotency key), records every attempt in `outbound_messages`, and picks the channel:

- photon:  durable send intent; the worker submits it after this transaction commits
- console: simulated channel for local dev / tests / CI (clearly labeled `simulated`)
"""

import logging
from types import SimpleNamespace

from sqlmodel import Session

from app.config import get_settings
from app.experience_models import Account
from app.integrations.photon.routing import route_error
from app.models import OutboundMessage, Person

log = logging.getLogger(__name__)


def channel_name() -> str:
    return "photon" if get_settings().use_photon else "console"


def photon_routed(person: Person) -> bool:
    return route_error(person) is None


def is_reachable(person: Person, session: Session | None = None) -> bool:
    if session is not None and session.get(Account, person.id) is not None:
        return True
    if channel_name() == "console":
        return person.handle is not None
    return photon_routed(person)


def send_to_person(
    session: Session,
    person: Person,
    text: str,
    idempotency_key: str,
    *,
    parent_id: str | None = None,
    visibility: str = "direct",
    request_id: str | None = None,
    offer_only: bool = False,
) -> OutboundMessage:
    existing = session.get(OutboundMessage, idempotency_key)
    if existing is not None:
        return existing

    from app.integrations import delivery

    context = delivery.snapshot_context(
        session,
        person,
        parent_id=parent_id,
        visibility=visibility,
        request_id=request_id,
        offer_only=offer_only,
    )
    # Fill optional snapshot properties before the common permission check.
    context_view = SimpleNamespace(family_link_id=None, care_circle_id=None)
    context_view.__dict__.update(context)
    permission_error = delivery.authorization_error(session, person, context_view)
    account = session.get(Account, person.id)
    channel = channel_name()
    if account is not None and not (channel == "photon" and photon_routed(person)):
        channel = "in_app"
    msg = OutboundMessage(
        id=idempotency_key, person_id=person.id, channel=channel, text=text, status="simulated"
    )
    if permission_error:
        msg.status = "cancelled"
        msg.error = permission_error
    elif channel == "in_app":
        # The task/state is available through authenticated role-specific APIs.
        # This is not a push notification, a read receipt, or a provider send.
        msg.status = "available"
    elif channel == "photon":
        if not photon_routed(person):
            msg.status = "unreachable"
            msg.error = route_error(person)
        else:
            msg.status = "queued"
            session.add(msg)
            session.flush()
            delivery.enqueue(session, msg, person, **context)
    else:
        log.info("[console -> %s] %s", person.display_name, text)

    session.add(msg)
    session.flush()
    return msg


def show_typing(person: Person) -> None:
    """Best-effort typing indicator while the agent thinks. Never raises."""
    if (
        channel_name() != "photon"
        or get_settings().photon_platform == "sms"
        or not photon_routed(person)
    ):
        return
    from app.integrations.photon import client as photon

    try:
        photon.set_typing(
            space_id=person.photon_space_id,  # type: ignore[arg-type]
            sender_id=person.photon_agent_user_id,
        )  # type: ignore[arg-type]
    except Exception as exc:
        log.info("Typing indicator failed (ignored): %s", type(exc).__name__)


def delivered(msg: OutboundMessage) -> bool:
    """Compatibility predicate: a coordination intent was accepted for processing.

    Existing state machines use this to decide whether a reachable recipient can
    be asked. It is NOT a delivery receipt: queued/accepted can still fail later.
    Inspect OutboundMessage.status / DeliveryJob for the actual dispatch outcome.
    """
    return msg.status in ("available", "queued", "accepted", "sent", "delivered", "simulated")
