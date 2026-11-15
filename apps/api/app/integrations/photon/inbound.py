"""Normalize Photon inbound payloads (webhook events or polled messages) into one shape."""

import re
from dataclasses import asdict, dataclass

from standardwebhooks.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.integrations.photon.routing import matches_channel

__all__ = [
    "InboundText",
    "WebhookVerificationError",
    "from_message",
    "receipt_from_webhook_event",
    "from_webhook_event",
    "normalize_handle",
    "verify_webhook",
]


@dataclass(frozen=True)
class InboundText:
    message_id: str
    sender_handle: str
    text: str
    space_id: str | None = None
    agent_user_id: str | None = None
    simulated: bool = False  # True for /api/demo/inbound (never overwrites Photon routing)
    channel: str = "text"  # "voice" when spoken on a (browser) voice call
    platform: str | None = None  # Photon transport; absent for older/demo messages

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_handle(handle: str) -> str:
    handle = handle.strip()
    if "@" in handle:
        return handle.lower()
    return "".join(ch for ch in handle if ch.isdigit() or ch == "+")


def from_message(m: dict) -> InboundText | None:
    """Accepts a Photon Message object (webhook data.object or list_messages item)."""
    if m.get("status") != "received":
        return None
    sent_by = m.get("sentBy") or {}
    received_by = m.get("receivedBy") or {}
    space = m.get("space") or {}
    if sent_by.get("kind") != "human" or received_by.get("kind") != "agent":
        return None
    if space.get("kind") not in (None, "direct"):
        return None  # FarCare only handles 1:1 conversations
    platform = m.get("platform")
    if not matches_channel(platform, received_by.get("id")) or not space.get("id"):
        return None
    if not _snapshots_match(m, sent_by, received_by, space):
        return None
    content = m.get("content") or {}
    if content.get("type") != "text" or content.get("format") not in (None, "plain"):
        return None
    text = (content.get("text") or "").strip()
    if not text or not m.get("id") or not sent_by.get("handle"):
        return None
    handle = normalize_handle(str(sent_by["handle"]))
    if platform == "sms" and not re.fullmatch(r"\+[1-9][0-9]{7,14}", handle):
        return None
    return InboundText(
        message_id=str(m["id"]),
        sender_handle=handle,
        text=text[:2000],
        space_id=space.get("id"),
        agent_user_id=received_by.get("id"),
        platform=platform,
    )


def from_webhook_event(event: dict, expected_project_id: str | None) -> InboundText | None:
    if event.get("type") != "message.received":
        return None
    data = event.get("data") or {}
    if expected_project_id and data.get("projectId") != expected_project_id:
        return None
    return from_message(data.get("object") or {})


def _snapshots_match(message: dict, *snapshots: dict) -> bool:
    """Reject cross-project/channel routes, including a different SMS sender."""
    settings = get_settings()
    platform = message.get("platform") or "imessage"
    for value in (message, *snapshots):
        # Legacy iMessage snapshots omitted these fields. SMS snapshots must be explicit.
        if value.get("platform", "imessage") != platform:
            return False
        project = value.get("projectId")
        if settings.photon_project_id and project not in (None, settings.photon_project_id):
            return False
        if platform == "sms" and project != settings.photon_project_id:
            return False
    return True


def receipt_from_webhook_event(event: dict, expected_project_id: str | None) -> dict | None:
    """Keep signed provider delivery evidence separate from a person's conversation."""
    status = {
        "message.dispatched": "sent",
        "message.delivered": "delivered",
        "message.failed": "failed",
    }.get(event.get("type"))
    if status is None or not event.get("id"):
        return None
    data = event.get("data") or {}
    if expected_project_id and data.get("projectId") != expected_project_id:
        return None
    m = data.get("object") or {}
    sender, space = m.get("sentBy") or {}, m.get("space") or {}
    if (
        m.get("status") != event["type"].split(".")[1]
        or sender.get("kind") != "agent"
        or not m.get("id")
        or not space.get("id")
        or not matches_channel(m.get("platform"), sender.get("id"))
        or not _snapshots_match(m, sender, space)
    ):
        return None
    failure_code = (m.get("failure") or {}).get("code")
    if not isinstance(failure_code, str) or not re.fullmatch(r"[a-z_]{1,80}", failure_code):
        failure_code = "message_delivery_failed" if status == "failed" else None
    return {
        "kind": "photon_receipt",
        "provider_message_id": m["id"],
        "status": status,
        "platform": m.get("platform"),
        "space_id": space["id"],
        "agent_user_id": sender["id"],
        "error_code": failure_code,
    }


def verify_webhook(secret: str, raw_body: bytes, headers: dict[str, str]) -> None:
    """Standard Webhooks verification over the raw body. Raises WebhookVerificationError."""
    Webhook(secret).verify(
        raw_body,
        {
            "webhook-id": headers.get("webhook-id", ""),
            "webhook-timestamp": headers.get("webhook-timestamp", ""),
            "webhook-signature": headers.get("webhook-signature", ""),
        },
    )
