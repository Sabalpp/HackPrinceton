"""Inbound message router: Photon (webhook or poll) and demo-simulated messages land here.

Order of precedence for a PARENT message (deterministic first, LLM last):
1. Active safety incident  -> OK / HELP parsing (no LLM)
2. Emergency phrases       -> open + escalate incident (no LLM)
3. Pending approval        -> YES / NO parsing (no LLM)
4. Gemini companion agent  (with deterministic fallback)
"""

import logging

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import session_scope, utcnow
from app.integrations.gemini import agent
from app.integrations.messaging import send_to_person, show_typing
from app.integrations.photon.inbound import InboundText, normalize_handle
from app.integrations.photon.routing import matches_channel
from app.models import ConversationTurn, InboundMessageRow, Person
from app.services import care_requests, safety, timeline
from app.services.replies import (
    looks_like_emergency,
    parse_responder_reply,
    parse_safety_reply,
    parse_yes_no,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- queue


def enqueue(msg: InboundText) -> bool:
    """Persist an inbound message once (dedupe by Photon message id). True if new."""
    return enqueue_payload(msg.message_id, msg.to_dict())


def enqueue_payload(message_id: str, payload: dict) -> bool:
    """Persist either a normalized text or signed delivery receipt exactly once."""
    try:
        with session_scope() as s:
            if s.get(InboundMessageRow, message_id) is not None:
                return False
            s.add(InboundMessageRow(id=message_id, payload=payload))
        return True
    except IntegrityError:
        return False


def process(message_id: str) -> None:
    """Claim and process one queued message. Safe to call more than once."""
    with session_scope() as s:
        claimed = s.execute(  # type: ignore[deprecated]
            update(InboundMessageRow)
            .where(InboundMessageRow.id == message_id, InboundMessageRow.status == "pending")
            .values(status="processing", attempts=InboundMessageRow.attempts + 1)
        ).rowcount
    if not claimed:
        return
    try:
        with session_scope() as s:
            row = s.get(InboundMessageRow, message_id)
            assert row is not None
            outcome = handle_payload(s, row.payload)
            row.status = outcome
            s.add(row)
    except Exception as exc:
        log.exception("Inbound message %s failed", message_id)
        with session_scope() as s:
            row = s.get(InboundMessageRow, message_id)
            if row is not None:
                row.status = "failed" if row.attempts >= 3 else "pending"
                row.error = type(exc).__name__[:100]
                s.add(row)


def receive(msg: InboundText) -> None:
    if enqueue(msg):
        process(msg.message_id)


def handle_payload(s: Session, payload: dict) -> str:
    if payload.get("kind") == "photon_receipt":
        from app.integrations import delivery

        return delivery.apply_receipt(s, payload)
    return handle(s, InboundText(**payload))


# --------------------------------------------------------------------------- routing


def _turn(s: Session, person: Person, role: str, text: str) -> None:
    s.add(ConversationTurn(person_id=person.id, role=role, text=text[:2000]))


VOICE_MARK = "(by voice call) "


def _as_turn(msg: InboundText) -> str:
    """Stored user turn. Voice turns are marked so the agent knows later that something was
    said on a call, which lets the next text continue it."""
    return (VOICE_MARK + msg.text) if msg.channel == "voice" else msg.text


def _reply(
    s: Session,
    person: Person,
    msg: InboundText,
    text: str,
    suffix: str = "",
    *,
    parent_id: str | None = None,
    visibility: str = "direct",
    request_id: str | None = None,
) -> None:
    if not text:
        return
    send_to_person(
        s,
        person,
        text,
        f"reply:{msg.message_id}{suffix}",
        parent_id=parent_id,
        visibility=visibility,
        request_id=request_id,
    )
    _turn(s, person, "assistant", text)


def handle(s: Session, msg: InboundText) -> str:
    handle_ = normalize_handle(msg.sender_handle)
    person = s.exec(select(Person).where(Person.handle == handle_)).first()
    if person is None:
        log.info("Ignoring message from unknown handle")
        return "ignored"

    first_contact = False
    if not msg.simulated and msg.space_id and msg.agent_user_id:
        if not matches_channel(msg.platform, msg.agent_user_id):
            return "ignored"
        person.photon_space_id = msg.space_id
        person.photon_agent_user_id = msg.agent_user_id
        person.photon_platform = msg.platform or "imessage"
        if person.reachable_since is None:
            person.reachable_since = utcnow()
            first_contact = True
        s.add(person)

    if not msg.simulated and msg.platform == "sms":
        command = msg.text.strip().upper()
        if command in {
            "STOP",
            "STOPALL",
            "UNSUBSCRIBE",
            "CANCEL",
            "END",
            "QUIT",
            "REVOKE",
            "OPT OUT",
        }:
            from app.integrations import delivery

            delivery.opt_out(s, person)
            # Do not queue a new SMS after opt-out; carrier/provider handles its acknowledgement.
            return "done"
        if command in {"START", "UNSTOP"}:
            person.messaging_opted_out_at = None
            s.add(person)
            _reply(
                s,
                person,
                msg,
                "FarCare messages are on. Reply STOP to pause messages or HELP for guidance.",
            )
            return "done"
        if person.messaging_opted_out_at is not None:
            return "ignored"  # Ordinary inbound text is not permission to restart alerts.
        if command == "HELP" and not (
            person.role == "parent" and safety.active_incident(s, person.id) is not None
        ):
            _reply(
                s,
                person,
                msg,
                "FarCare connects your care circle. Tell me what you need, or reply YES, NO or "
                "DONE to an active request. Reply STOP to pause SMS, START to resume. "
                "For immediate danger, call local emergency services; FarCare does not dispatch them.",
            )
            return "done"

    if person.role == "parent":
        _handle_parent(s, person, msg)
    elif person.role == "responder":
        _handle_responder(s, person, msg, first_contact)
    elif person.role == "child":
        _handle_child(s, person, msg, first_contact)
    else:
        return "ignored"
    return "done"


def _handle_parent(s: Session, parent: Person, msg: InboundText) -> None:
    text = msg.text
    _turn(s, parent, "user", _as_turn(msg))

    incident = safety.active_incident(s, parent.id)
    if incident is not None:
        answer = parse_safety_reply(text)
        if answer is not None:
            safety.parent_replied(s, incident, answer)
            return
        if incident.status == "checking":
            _reply(
                s,
                parent,
                msg,
                "Are you okay? Please reply OK if you're fine, or HELP if "
                "you'd like someone to come.",
            )
            return

    if looks_like_emergency(text):
        safety.open_from_message(s, parent, text)
        return

    pending = care_requests.pending_approval(s, parent.id)
    if pending is not None:
        decision = parse_yes_no(text)
        responder = s.get(Person, pending.responder_id) if pending.responder_id else None
        name = responder.display_name if responder else "them"
        if decision == "yes":
            if care_requests.approve(s, pending):
                _reply(
                    s, parent, msg, f"Great, I've asked {name}. I'll let you know when they reply."
                )
                care_requests.notify_requester(
                    s,
                    pending,
                    lambda who: (
                        f"{who} said yes, so I've asked {name}. I'll let you know when "
                        f"{name} replies."
                    ),
                    f"care:{pending.id}:requester_approved",
                )
            return
        if decision == "no":
            care_requests.cancel(
                s, pending, summary=f"{parent.display_name} chose not to ask {name}"
            )
            _reply(
                s,
                parent,
                msg,
                "Okay, I won't contact anyone. Just let me know if you change your mind.",
            )
            care_requests.notify_requester(
                s,
                pending,
                lambda who: (
                    f"{who} would rather not have help with that right now, so I haven't "
                    "contacted anyone."
                ),
                f"care:{pending.id}:requester_declined",
            )
            return

    show_typing(parent)
    result = agent.respond(s, parent, text)
    timeline.record(
        s,
        parent.id,
        "agent.replied",
        "FarCare companion replied",
        data={"ai_mode": result.mode, "actions": result.actions},
        visibility=timeline.PARENT_ONLY,
    )
    _reply(s, parent, msg, result.reply)


def _handle_responder(s: Session, responder: Person, msg: InboundText, first_contact: bool) -> None:
    req = care_requests.active_request_for_responder(s, responder.id)
    if req is None:
        if first_contact:
            _reply(
                s,
                responder,
                msg,
                f"Hi {responder.display_name}, you're connected to FarCare as a trusted "
                "contact. We'll only message you when someone in your care circle asks for "
                "help.",
            )
        else:
            _reply(
                s,
                responder,
                msg,
                f"Thanks, {responder.display_name}! Nothing needs your help right now.",
            )
        return
    intent = parse_responder_reply(msg.text, req.status)
    if intent is None:
        intent = agent.classify_responder_reply(msg.text, req.status)  # type: ignore[assignment]
    if intent is None:
        hint = (
            "Reply YES if you can help, or NO if you can't."
            if req.status == "sent"
            else "Reply DONE when you've finished, or NEED HELP if more help is needed."
        )
        _reply(
            s,
            responder,
            msg,
            f"Sorry, I didn't catch that. {hint}",
            parent_id=req.parent_id,
            visibility="responder_emergency" if req.origin == "emergency" else "responder",
            request_id=req.id,
        )
        return
    care_requests.apply_responder_reply(s, req, responder, intent, msg.text)


def _handle_child(s: Session, child: Person, msg: InboundText, first_contact: bool) -> None:
    from app.integrations.gemini import family_agent
    from app.services import family

    links = family.links_for(s, child)
    if not links:
        _reply(s, child, msg, "Hi! You're not linked to anyone in FarCare yet.")
        return
    link, parent = links[0]  # hackathon scope: one linked parent per family member
    _turn(s, child, "user", _as_turn(msg))
    intro = (
        f"Hi {child.display_name}! You're connected to FarCare for {family.who(link, parent)}. "
        "You'll get updates here, and you can ask me anything.\n"
        if first_contact
        else ""
    )
    if first_contact and msg.text.strip().lower() in ("join", "hi", "hello", "start"):
        reply, mode = "\n".join(family.status_lines(s, link, parent)), "fallback"
    else:
        show_typing(child)
        reply, mode = family_agent.respond(s, child, link, parent, msg.text)
    timeline.record(
        s,
        parent.id,
        "agent.replied",
        f"FarCare replied to {child.display_name}",
        data={"ai_mode": mode, "to": child.id},
        visibility=timeline.PARENT_ONLY,
    )
    _reply(s, child, msg, intro + reply, parent_id=parent.id, visibility="family_context")
