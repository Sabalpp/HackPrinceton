"""Photon 2.0 webhook receiver (Standard Webhooks signatures).

Docs: https://photon.codes/docs/beta/message-delivery/webhook
- Verify the RAW body before parsing. Invalid -> 400 (Photon stops; doesn't retry).
- Dedupe: inbound rows are keyed by Photon message id (stable across retries).
- Return 2xx fast; processing happens in a background task (Photon timeout is 20 s).
- If we can't persist the event, return 503 so Photon retries.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.config import get_settings
from app.integrations.photon.inbound import (
    WebhookVerificationError,
    from_webhook_event,
    verify_webhook,
)
from app.services import conversation

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/photon", status_code=204)
async def photon_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings = get_settings()
    if not settings.photon_webhook_secret:
        raise HTTPException(503, "Photon webhook not configured")
    raw = await request.body()
    try:
        verify_webhook(settings.photon_webhook_secret, raw, dict(request.headers))
    except WebhookVerificationError:
        raise HTTPException(400, "Invalid signature") from None

    event = json.loads(raw)
    msg = from_webhook_event(event, settings.photon_project_id or None)
    if msg is None:
        return Response(status_code=204)  # other event types are acknowledged and ignored
    try:
        is_new = conversation.enqueue(msg)
    except Exception:
        log.exception("Could not persist Photon event")
        raise HTTPException(503, "Temporarily unavailable") from None
    if is_new:
        background.add_task(conversation.process, msg.message_id)
    return Response(status_code=204)
