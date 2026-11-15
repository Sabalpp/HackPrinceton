"""Development-only in-process loops. Production uses ``python -m app.worker``.

Both paths share the durable worker mutex/outbox. Polling remains a local demo
convenience; its in-memory cursor is not a production replay checkpoint.
"""

import asyncio
import logging
from datetime import timedelta

from app.config import get_settings
from app.db import utcnow
from app.services import conversation

log = logging.getLogger(__name__)


def run_tick() -> None:
    from app.worker import run_cycle

    run_cycle()


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
                    await asyncio.to_thread(conversation.enqueue, msg)
            cursor = started
        except Exception:
            log.exception("Photon poll failed")
        await asyncio.sleep(settings.photon_poll_interval_seconds)
