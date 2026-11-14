"""Normalize Photon inbound payloads (webhook events or polled messages) into one shape."""

from dataclasses import asdict, dataclass

from standardwebhooks.webhooks import Webhook, WebhookVerificationError

__all__ = [
    "InboundText",
    "WebhookVerificationError",
    "from_message",
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
    content = m.get("content") or {}
    if content.get("type") != "text":
        return None
    text = (content.get("text") or "").strip()
    if not text or not m.get("id") or not sent_by.get("handle"):
        return None
    return InboundText(
        message_id=str(m["id"]),
        sender_handle=normalize_handle(str(sent_by["handle"])),
        text=text[:2000],
        space_id=space.get("id"),
        agent_user_id=received_by.get("id"),
    )


def from_webhook_event(event: dict, expected_project_id: str | None) -> InboundText | None:
    if event.get("type") != "message.received":
        return None
    data = event.get("data") or {}
    if expected_project_id and data.get("projectId") not in (None, expected_project_id):
        return None
    return from_message(data.get("object") or {})


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
