"""Outbound messaging gateway.

`send_to_person` is the ONLY way services send messages. It is idempotent (keyed by
idempotency key), records every attempt in `outbound_messages`, and picks the channel:

- photon:  real iMessage via Photon 2.0 when PHOTON_API_KEY + PHOTON_PROJECT_ID are set
- console: simulated channel for local dev / tests / CI (clearly labeled `simulated`)
"""

import logging

from sqlmodel import Session

from app.config import get_settings
from app.models import OutboundMessage, Person

log = logging.getLogger(__name__)


def channel_name() -> str:
    return "photon" if get_settings().use_photon else "console"


def is_reachable(person: Person) -> bool:
    if channel_name() == "console":
        return person.handle is not None
    return bool(person.photon_space_id and person.photon_agent_user_id)


def send_to_person(
    session: Session, person: Person, text: str, idempotency_key: str
) -> OutboundMessage:
    existing = session.get(OutboundMessage, idempotency_key)
    if existing is not None:
        return existing

    channel = channel_name()
    msg = OutboundMessage(
        id=idempotency_key, person_id=person.id, channel=channel, text=text, status="simulated"
    )
    if channel == "photon":
        if not is_reachable(person):
            msg.status = "unreachable"
            msg.error = "Person has not messaged the FarCare line yet (Photon shared-line rule)."
        else:
            from app.integrations.photon import client as photon

            try:
                msg.provider_message_id = photon.send_text(
                    space_id=person.photon_space_id,  # type: ignore[arg-type]
                    sender_id=person.photon_agent_user_id,  # type: ignore[arg-type]
                    text=text,
                    idempotency_key=idempotency_key,
                )
                msg.status = "sent"
            except Exception as exc:  # network / API errors must not crash workflows
                log.exception("Photon send failed for %s", person.id)
                msg.status = "failed"
                msg.error = f"{type(exc).__name__}: {exc}"[:500]
    else:
        log.info("[console -> %s] %s", person.display_name, text)

    session.add(msg)
    session.flush()
    return msg


def show_typing(person: Person) -> None:
    """Best-effort typing indicator while the agent thinks. Never raises."""
    if channel_name() != "photon" or not is_reachable(person):
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
    return msg.status in ("sent", "simulated")
