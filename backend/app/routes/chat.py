"""The signed-in person's conversation with FarCare, inside the app.

Same agent, same memory and same tools as iMessage/SMS: a message typed here goes through the
conversation router exactly like a text. Replies to app messages are shown in the app; actions
(requests, approvals, task replies) still notify other people on their own channels.
"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.auth import current_person
from app.db import get_session, utcnow
from app.integrations.messaging import channel_name, photon_routed
from app.integrations.photon.inbound import InboundText
from app.models import InboundMessageRow, Person
from app.schemas.experience import ChatIn, ChatMessage, ChatThread
from app.services import conversation

router = APIRouter(prefix="/api/me", tags=["conversation"])

APP_MESSAGES_PER_10_MIN = 30


def _thread(session: Session, person: Person, limit: int = 60) -> ChatThread:
    return ChatThread(
        messages=[ChatMessage(**m) for m in conversation.thread(session, person, limit)],
        connected_by_text=channel_name() == "photon" and photon_routed(person),
        messaging=channel_name(),  # type: ignore[arg-type]
    )


@router.get("/conversation", response_model=ChatThread)
def get_conversation(
    limit: int = Query(default=60, ge=1, le=200),
    person: Person = Depends(current_person),
    session: Session = Depends(get_session),
):
    return _thread(session, person, limit)


@router.post("/conversation", response_model=ChatThread)
def send_message(
    body: ChatIn,
    person: Person = Depends(current_person),
    session: Session = Depends(get_session),
):
    if person.role not in ("parent", "child", "responder"):
        raise HTTPException(403, "This conversation is for parents, family and helpers")
    since = utcnow() - timedelta(minutes=10)
    recent = session.exec(
        select(InboundMessageRow).where(InboundMessageRow.received_at >= since)  # type: ignore[operator]
    ).all()
    mine = [r for r in recent if (r.payload or {}).get("person_id") == person.id]
    if len(mine) >= APP_MESSAGES_PER_10_MIN:
        raise HTTPException(429, "You're sending messages quickly. Try again in a few minutes.")
    conversation.receive(
        InboundText(
            message_id=f"app_{secrets.token_hex(10)}",
            sender_handle=person.handle or "",
            text=body.text,
            simulated=True,  # never overwrites Photon routing learned from real texts
            channel="app",
            person_id=person.id,
        )
    )
    session.expire_all()
    return _thread(session, person)
