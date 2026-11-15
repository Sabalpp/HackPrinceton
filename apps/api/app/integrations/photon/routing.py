"""Channel and sender checks shared by intake and the durable outbox."""

from app.config import get_settings


def matches_channel(platform: str | None, agent_user_id: str | None) -> bool:
    settings = get_settings()
    # Existing iMessage records predate platform storage. Never reuse them as SMS routes.
    if (platform or "imessage") != settings.photon_platform:
        return False
    return (
        bool(agent_user_id)
        and (not settings.photon_agent_user_id or agent_user_id == settings.photon_agent_user_id)
        and (settings.photon_platform != "sms" or bool(settings.photon_agent_user_id))
    )


def route_error(person) -> str | None:
    if person.messaging_opted_out_at is not None:
        return "recipient_opted_out"
    if not person.photon_space_id or person.reachable_since is None:
        return "recipient_not_connected"
    if not matches_channel(person.photon_platform, person.photon_agent_user_id):
        return "recipient_channel_or_sender_changed"
    return None
