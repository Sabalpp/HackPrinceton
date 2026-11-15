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
    receipt_from_webhook_event,
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
    except (ValueError, UnicodeDecodeError):
        # standardwebhooks also parses verified JSON before it returns.
        raise HTTPException(400, "Invalid JSON") from None

    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON") from None
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid event")
    try:
        msg = from_webhook_event(event, settings.photon_project_id or None)
        receipt = receipt_from_webhook_event(event, settings.photon_project_id or None)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(400, "Invalid event structure") from None
    if msg is None and receipt is None:
        return Response(status_code=204)  # other event types are acknowledged and ignored
    try:
        if receipt is not None:
            message_id = f"photon_receipt:{event['id']}"
            is_new = conversation.enqueue_payload(message_id, receipt)
        else:
            assert msg is not None
            message_id = msg.message_id
            is_new = conversation.enqueue(msg)
    except Exception:
        log.exception("Could not persist Photon event")
        raise HTTPException(503, "Temporarily unavailable") from None
    # Production acknowledges only durable intake. A separate worker owns all
    # processing; a request-local task is not a durable queue.
    if is_new and settings.app_env != "production":
        background.add_task(conversation.process, message_id)
    return Response(status_code=204)
