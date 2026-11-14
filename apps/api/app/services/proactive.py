"""Gentle proactive wellness check-ins ("a delightful default that doesn't demand attention").

At most ONE per parent per local day, and only when:
- their activity trend is notable vs. their own baseline (non-diagnostic),
- it's daytime where they live (PROACTIVE_CHECKIN_HOURS),
- they haven't been chatting with FarCare recently (don't interrupt),
- no safety incident is open and no approval question is pending,
- FarCare can actually reach them.
"""

import logging
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.config import get_settings
from app.db import utcnow
from app.health import service as health
from app.integrations.messaging import delivered, is_reachable, send_to_person
from app.models import ConversationTurn, OutboundMessage, Person
from app.services import care_requests, safety, timeline

log = logging.getLogger(__name__)


def _local_now(parent: Person):
    try:
        return utcnow().astimezone(ZoneInfo(parent.timezone))
    except Exception:
        return utcnow()


def _in_window(parent: Person) -> bool:
    start, end = (int(x) for x in get_settings().proactive_checkin_hours.split("-"))
    return start <= _local_now(parent).hour < end


def _recently_chatted(session: Session, parent: Person) -> bool:
    hours = get_settings().proactive_quiet_after_chat_hours
    last = session.exec(
        select(ConversationTurn)
        .where(ConversationTurn.person_id == parent.id, ConversationTurn.role == "user")
        .order_by(ConversationTurn.created_at.desc())  # type: ignore[union-attr]
    ).first()
    return last is not None and (utcnow() - last.created_at).total_seconds() < hours * 3600


def compose(parent: Person, notable_metrics: set[str], simulated: bool) -> str:
    hour = _local_now(parent).hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    if "steps" in notable_metrics:
        noticed = " I noticed you've been a little less active than usual this week."
    elif "sleep_minutes" in notable_metrics:
        noticed = " I noticed your sleep has been a bit shorter than usual."
    else:
        noticed = ""
    text = (
        f"{greeting}, {parent.display_name} 🌼 Just checking in. How are you feeling "
        f"today?{noticed} If you'd like, I can ask someone to stop by."
    )
    return text + (" [Demo: simulated data]" if simulated and noticed else "")


def maybe_checkin(
    session: Session, parent: Person, *, force: bool = False, key: str | None = None
) -> OutboundMessage | None:
    """force=True (demo) skips the trend, time-window and quiet-period checks."""
    if not is_reachable(parent):
        return None
    if safety.active_incident(session, parent.id) or care_requests.pending_approval(
        session, parent.id
    ):
        return None
    summ = health.summary(session, parent.id)
    notable = {t.metric for t in summ.trends if t.notable}
    if not force and (not notable or not _in_window(parent) or _recently_chatted(session, parent)):
        return None
    key = key or f"proactive:{parent.id}:{_local_now(parent).date().isoformat()}"
    if session.get(OutboundMessage, key) is not None:
        return None  # already checked in today
    text = compose(parent, notable, simulated=summ.data_mode != "live")
    msg = send_to_person(session, parent, text, key)
    if delivered(msg):
        session.add(ConversationTurn(person_id=parent.id, role="assistant", text=text))
        reason = (
            "activity lower than usual"
            if "steps" in notable
            else ("sleep shorter than usual" if "sleep_minutes" in notable else "daily check-in")
        )
        timeline.record(
            session,
            parent.id,
            "checkin.prompted",
            f"FarCare sent {parent.display_name} a gentle check-in ({reason}).",
            data={"metrics": sorted(notable)},
            visibility=timeline.HEALTH,
        )
    return msg


def run(session: Session) -> int:
    if not get_settings().proactive_checkins_enabled:
        return 0
    sent = 0
    for parent in session.exec(select(Person).where(Person.role == "parent")).all():
        try:
            if maybe_checkin(session, parent):
                sent += 1
        except Exception:
            log.exception("proactive check-in failed for %s", parent.id)
    return sent
