"""Photon 2.0 (Beta) client wrapper.

Docs (Beta): https://photon.codes/docs/beta/get-started/imessage
SDK: `photonhq-api` (import `photon_api`). Auth is a Bearer project key passed via headers;
`Photon()` has no api_key kwarg.

Shared-line rule (from the OpenAPI spec): a shared iMessage line can only send in a direct
Space to a peer who has an active Assignment AND has messaged the line since that
Assignment. So every human (parent, child, responder) must text FarCare once before we
can message them. We learn their space id / agent user id from that first message.
"""

import logging
import threading
from datetime import datetime

from photon_api import Photon
from photon_api.generated.models import SendMessageRequest, SendTextContent, SetSpaceTypingRequest
from photon_api.rpc_generated import (
    ListMessagesInput,
    SendMessageHeader,
    SendMessageInput,
    SendMessagePath,
    SetSpaceTypingInput,
    SetSpaceTypingPath,
)

from app.config import get_settings

log = logging.getLogger(__name__)

_client: Photon | None = None
_lock = threading.Lock()


def get_client() -> Photon:
    global _client
    with _lock:
        if _client is None:
            settings = get_settings()
            _client = Photon(
                headers={"Authorization": f"Bearer {settings.photon_api_key}"},
                timeout=10,
                max_attempts=1,
            )
        return _client


def send_text(*, space_id: str, sender_id: str, text: str, idempotency_key: str) -> str:
    """Send a plain-text message into an existing direct Space. Returns Photon message id.

    202 from Photon means "accepted for async dispatch", not delivered.
    """
    settings = get_settings()
    if settings.photon_platform == "sms" and sender_id != settings.photon_agent_user_id:
        raise ValueError("SMS sender must match PHOTON_AGENT_USER_ID")
    sent = get_client().projects.send_message(
        SendMessageInput(
            path=SendMessagePath(id=settings.photon_project_id),
            headers=SendMessageHeader.model_validate({"idempotency-key": idempotency_key[:255]}),
            body=SendMessageRequest(
                spaceId=space_id,
                senderId=sender_id,
                content=SendTextContent(type="text", format="plain", text=text),
            ),
        )
    )
    data = sent.model_dump(mode="json", by_alias=True)
    return str(data.get("id", ""))


def set_typing(*, space_id: str, sender_id: str, typing: bool = True) -> None:
    """Show/hide the typing indicator. Transient: Photon expects callers to refresh it during
    long work; one call covers a short agent turn."""
    settings = get_settings()
    if settings.photon_platform == "sms":
        return  # SMS has no typing indicator.
    get_client().projects.set_space_typing(
        SetSpaceTypingInput(
            path=SetSpaceTypingPath.model_validate(
                {"id": settings.photon_project_id, "spaceId": space_id}
            ),
            body=SetSpaceTypingRequest(senderId=sender_id, typing=typing),
        )
    )


def list_received_since(created_since: datetime) -> list[dict]:
    """Poll received messages (used when PHOTON_DELIVERY=poll). Returns raw message dicts."""
    settings = get_settings()
    query: dict = {
        "createdSince": created_since.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "direction": "received",
        "platform": settings.photon_platform,
        "order": "asc",
        "pageSize": 100,
    }
    out: list[dict] = []
    client = get_client()
    while True:
        page = client.projects.list_messages(
            ListMessagesInput.model_validate(
                {"path": {"id": settings.photon_project_id}, "query": query}
            )
        )
        out.extend(item.model_dump(mode="json", by_alias=True) for item in page.items)
        token = page.nextPageToken
        if not isinstance(token, str):
            break
        query["pageToken"] = token
    return out
