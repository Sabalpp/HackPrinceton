"""Background loops started in the app lifespan.

- tick:  deadlines (safety-check timeout -> escalate; responder/approval timeouts) and
         retry of inbound messages that failed or were interrupted. State lives in the DB,
         so a restart loses nothing.
- poll:  only when PHOTON_DELIVERY=poll — pulls received messages from Photon.

Run exactly ONE backend instance (single worker) for the hackathon.
"""

import asyncio
import logging
from datetime import timedelta

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope, utcnow
from app.models import InboundMessageRow
from app.services import care_requests, conversation, safety

log = logging.getLogger(__name__)


def run_tick() -> None:
    with session_scope() as s:
        safety.check_overdue(s)
    with session_scope() as s:
        care_requests.expire_overdue(s)
    # Retry stuck inbound work.
    now = utcnow()
    with session_scope() as s:
        stale = s.exec(
            select(InboundMessageRow).where(
                InboundMessageRow.status.in_(["pending", "processing"]),  # type: ignore[attr-defined]
                InboundMessageRow.received_at < now - timedelta(seconds=30),  # type: ignore[operator]
            )
        ).all()
        for row in stale:
            if row.status == "processing" and row.received_at > now - timedelta(seconds=120):
                continue
            row.status = "pending" if row.attempts < 3 else "failed"
            s.add(row)
        retry = [r.id for r in stale if r.status == "pending"]
    for message_id in retry:
        conversation.process(message_id)


async def tick_loop() -> None:
    interval = get_settings().tick_interval_seconds
    while True:
        try:
            await asyncio.to_thread(run_tick)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(interval)


async def poll_loop() -> None:
    from app.integrations.photon import client as photon
    from app.integrations.photon.inbound import from_message

    settings = get_settings()
    cursor = utcnow() - timedelta(minutes=5)
    while True:
        started = utcnow()
        try:
            messages = await asyncio.to_thread(
                photon.list_received_since, cursor - timedelta(seconds=10)
            )
            for raw in messages:
                msg = from_message(raw)
                if msg is not None:
                    await asyncio.to_thread(conversation.receive, msg)
            cursor = started
        except Exception:
            log.exception("Photon poll failed")
        await asyncio.sleep(settings.photon_poll_interval_seconds)
