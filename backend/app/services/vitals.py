"""Latest readings for questions like "what's the bpm?", and heart-rate alert limits.

Readings
  * Live heart rate: the newest Bluetooth sample from the linked watch, read through the
    read-only watch connection. Bluetooth notification identities are immutable in the
    collector, so the newest row is the reading and the lookup stays one indexed query.
  * Daily numbers from Google Health (resting heart rate, blood oxygen, HRV, breathing rate,
    main sleep): the newest eligible record per metric, newest revision first.
  * Steps: the newest completed day already projected into FarCare. Today's partial total is
    never presented as a day's steps.
  Every value carries its time. Nothing is interpolated, inferred or labelled normal/abnormal.

Alert limits
  The parent, or a family member the parent shares health with, picks a high and/or low heart
  rate and a window. A limit counts as crossed only when every live sample in the window is
  beyond it, the samples cover the window without gaps, and the newest one is fresh, so a
  single spike or a gap never triggers. Then, at most once per cooldown: family who can see
  health get a text with the numbers, and/or the parent is asked "are you okay?" through the
  normal safety check, which reaches nearby helpers only if there's no reply. Helpers never
  receive health numbers. These are the family's own numbers, not medical thresholds.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db import new_id, utcnow, watch_data_session
from app.experience_models import AccessAudit
from app.health.live_models import WatchSourceLink
from app.integrations.messaging import send_to_person
from app.models import FamilyLink, HealthObservationRow, HeartRateAlertRow, Person
from app.services import safety, timeline

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(seconds=30)
MAX_GAP_SECONDS = 30  # a longer silence inside the window means the watch wasn't sending
FRESH_SECONDS = 30  # the newest sample must be at most this old to act on it
LIVE_FOR = timedelta(seconds=20)  # same freshness as the app's live panel
HIGH_RANGE, LOW_RANGE, MINUTES_RANGE = (60, 220), (30, 100), (1, 30)

DAILY = {  # collector metric -> label, unit
    "resting_heart_rate_bpm": ("Resting heart rate", "bpm"),
    "spo2_daily_mean_percent": ("Blood oxygen", "%"),
    "hrv_daily_average_ms": ("Heart rate variability", "ms"),
    "respiratory_rate_bpm": ("Breathing rate", "breaths/min"),
    "sleep_minutes_asleep": ("Sleep", "min"),
}

LIVE_SQL = """
SELECT value, observed_at FROM air_health.measurement_versions
WHERE subject_id = :subject_id AND source = 'ble' AND metric = 'heart_rate_bpm'
  AND canonical_eligible AND observed_at >= :since AND observed_at <= :now
ORDER BY observed_at DESC
LIMIT :limit
"""

DAILY_SQL = """
SELECT m.metric, v.value, v.observed_at, v.ended_at, v.local_date
FROM unnest(CAST(:metrics AS text[])) AS m(metric)
CROSS JOIN LATERAL (
    SELECT value, observed_at, ended_at, local_date FROM air_health.measurement_versions
    WHERE subject_id = :subject_id AND source = 'google_health' AND metric = m.metric
      AND canonical_eligible AND observed_at >= :since AND observed_at <= :now
      AND (m.metric <> 'sleep_minutes_asleep' OR attributes->>'main_sleep' = 'true')
    ORDER BY observed_at DESC, source_revision_at DESC, received_at DESC
    LIMIT 1
) v
"""


@dataclass
class Reading:
    label: str
    value: float
    unit: str
    at: datetime | None = None  # a moment (live sample)
    day: date | None = None  # a reported day
    source: str = "Google Health"
    live: bool = False

    def shown(self) -> str:
        if self.unit == "min":
            minutes = round(self.value)
            return f"{minutes // 60}h {minutes % 60:02d}m"
        if self.unit == "steps":
            return f"{round(self.value):,} steps"
        if self.unit in ("bpm", "ms"):
            return f"{round(self.value)} {self.unit}"
        number = f"{self.value:.1f}".rstrip("0").rstrip(".")
        return f"{number}%" if self.unit == "%" else f"{number} {self.unit}"

    def when(self, now: datetime) -> str:
        if self.at is not None:
            seconds = max(0, int((now - self.at).total_seconds()))
            if seconds < 60:
                return "just now" if seconds < 15 else f"{seconds} sec ago"
            if seconds < 3600:
                return f"{seconds // 60} min ago"
            if seconds < 86400:
                return f"{seconds // 3600} hr ago"
            return f"{seconds // 86400} days ago"
        return f"on {self.day:%b} {self.day.day}" if self.day else "date unknown"


def _subject(session: Session, parent_id: str) -> str | None:
    link = session.get(WatchSourceLink, parent_id)
    return link.subject_id if link is not None and link.enabled else None


def _watch_rows(session: Session, sql: str, params: dict) -> list[dict]:
    """One bounded read on the watch database. SQLite (no collector tables) returns nothing."""
    with watch_data_session(session) as watch:
        if watch.get_bind().dialect.name != "postgresql":
            return []
        watch.execute(text("SET LOCAL statement_timeout = '3s'"))
        return [dict(r) for r in watch.execute(text(sql), params).mappings()]


def recent_heart_rate(
    session: Session, subject_id: str, *, now: datetime, seconds: int
) -> list[tuple[datetime, float]]:
    rows = _watch_rows(
        session,
        LIVE_SQL,
        {
            "subject_id": subject_id,
            "since": now - timedelta(seconds=seconds),
            "now": now,
            "limit": 5000,
        },
    )
    return sorted((r["observed_at"], float(r["value"])) for r in rows)


def latest_readings(
    session: Session, parent: Person, *, now: datetime | None = None
) -> list[Reading]:
    clock = now or utcnow()
    out: list[Reading] = []
    subject = _subject(session, parent.id)
    if subject:
        params = {"subject_id": subject, "now": clock}
        try:
            live = _watch_rows(
                session, LIVE_SQL, {**params, "since": clock - timedelta(days=2), "limit": 1}
            )
            daily = _watch_rows(
                session,
                DAILY_SQL,
                {**params, "since": clock - timedelta(days=7), "metrics": list(DAILY)},
            )
        except SQLAlchemyError as exc:
            if getattr(exc, "connection_invalidated", False):
                raise
            log.warning("Watch readings unavailable")
            live, daily = [], []
        for row in live:
            out.append(
                Reading(
                    "Heart rate",
                    float(row["value"]),
                    "bpm",
                    at=row["observed_at"],
                    source="watch",
                    live=clock - row["observed_at"] <= LIVE_FOR,
                )
            )
        for row in sorted(daily, key=lambda r: list(DAILY).index(r["metric"])):
            label, unit = DAILY[row["metric"]]
            out.append(Reading(label, float(row["value"]), unit, day=row["local_date"]))
    # FarCare's own daily records: steps always; resting heart rate and sleep when no watch has them.
    have = {r.label for r in out}
    today = clock.astimezone(ZoneInfo(parent.timezone)).date()
    rows = session.exec(
        select(HealthObservationRow)
        .where(
            HealthObservationRow.parent_id == parent.id,
            HealthObservationRow.timestamp >= clock - timedelta(days=14),  # type: ignore[operator]
        )
        .order_by(HealthObservationRow.timestamp.desc())  # type: ignore[union-attr]
    ).all()
    for field, label, unit in (
        ("steps", "Steps", "steps"),
        ("resting_heart_rate", "Resting heart rate", "bpm"),
        ("sleep_minutes", "Sleep", "min"),
    ):
        if label in have:
            continue
        for row in rows:
            day = row.timestamp.date()
            value = getattr(row, field)
            if value is None or (field == "steps" and day >= today):
                continue
            source = "simulated demo data" if row.data_mode == "simulated" else "Google Health"
            out.append(Reading(label, float(value), unit, day=day, source=source))
            break
    return out


def describe_alert(rule: HeartRateAlertRow | None) -> str | None:
    if rule is None or not rule.enabled:
        return None
    limits = [f"above {rule.high_bpm} bpm"] if rule.high_bpm else []
    limits += [f"below {rule.low_bpm} bpm"] if rule.low_bpm else []
    return f"heart rate {' or '.join(limits)} for {rule.sustain_minutes} min" if limits else None


def readings_facts(session: Session, parent: Person, *, now: datetime | None = None) -> dict:
    """Tool result for the AI agents: exact values with their times, nothing interpreted."""
    clock = now or utcnow()
    readings = latest_readings(session, parent, now=clock)
    alert = describe_alert(session.get(HeartRateAlertRow, parent.id))
    return {
        "readings": [
            {
                "what": r.label,
                "value": r.shown(),
                "when": r.when(clock),
                "source": r.source,
                **({"live_now": r.live} if r.at is not None else {}),
            }
            for r in readings
        ],
        "watch_linked": _subject(session, parent.id) is not None,
        "alert_limit": alert,
        "note": "Quote values exactly with their time and source. If live_now is false, say it is "
        "the last reading and how old it is. Never call a value normal, abnormal or a diagnosis.",
    }


ASK = {
    "Heart rate": re.compile(r"bpm|heart ?(rate|beat)|pulse|\bhr\b", re.I),
    "Resting heart rate": re.compile(r"resting", re.I),
    "Blood oxygen": re.compile(r"oxygen|spo2|\bo2\b|saturation", re.I),
    "Heart rate variability": re.compile(r"hrv|variability", re.I),
    "Breathing rate": re.compile(r"breath|respirat", re.I),
    "Sleep": re.compile(r"sleep|slept", re.I),
    "Steps": re.compile(r"steps?\b|walk", re.I),
}
READINGS_RE = re.compile(
    # No bare "hr" ("in 2 hr") or "breathing" (emergency phrases are handled before this).
    r"\b(bpm|heart ?(rate|beat)|pulse|spo2|oxygen|o2|hrv|breathing rate|readings?|vitals?|"
    r"how many steps|steps today|my steps|her steps|his steps|how (much|long) did (i|she|he) sleep)\b",
    re.I,
)
ALERT_RE = re.compile(r"\b(alert|text|tell|notify|message)\b.*\b(if|when)\b", re.I)
ABOVE_RE = re.compile(r"(?:above|over|higher than|more than|greater than|>)\s*(\d{2,3})", re.I)
BELOW_RE = re.compile(r"(?:below|under|lower than|less than|<)\s*(\d{2,3})", re.I)
MINUTES_RE = re.compile(r"(\d{1,2})\s*(?:min|minute)", re.I)


def answer(question: str, facts: dict, whose: str) -> str:
    """Deterministic SMS answer for when Gemini is unavailable."""
    rows = facts["readings"]
    if not rows:
        return f"I don't have any readings for {whose} yet." + (
            "" if facts["watch_linked"] else " No watch is linked."
        )
    wanted = {label for label, pattern in ASK.items() if pattern.search(question)}
    if (
        "Heart rate" in wanted
        and "Resting heart rate" not in wanted
        and not ASK["Resting heart rate"].search(question)
    ):
        wanted.add("Resting heart rate")
    picked = [r for r in rows if not wanted or r["what"] in wanted] or rows
    lines = []
    for r in picked:
        if r.get("live_now") is False:
            lines.append(f"{r['what']}: {r['value']}, last reading {r['when']} ({r['source']}).")
        else:
            lines.append(f"{r['what']}: {r['value']} ({r['when']}, {r['source']}).")
    return (
        f"{whose[0].upper()}{whose[1:]}: " + " ".join(lines) + " Readings only, not medical advice."
    )


def parse_alert_request(message: str) -> dict | None:
    """'text me if her bpm is above 120 for 5 minutes' -> limits, or None."""
    if not ALERT_RE.search(message):
        return None
    above, below, minutes = (
        ABOVE_RE.search(message),
        BELOW_RE.search(message),
        MINUTES_RE.search(message),
    )
    if not above and not below:
        return None
    return {
        "high_bpm": int(above.group(1)) if above else None,
        "low_bpm": int(below.group(1)) if below else None,
        "minutes": int(minutes.group(1)) if minutes else 2,
    }


def validate(high_bpm: int | None, low_bpm: int | None, sustain_minutes: int) -> None:
    if high_bpm is None and low_bpm is None:
        raise ValueError("Set a high limit, a low limit, or both.")
    if high_bpm is not None and not HIGH_RANGE[0] <= high_bpm <= HIGH_RANGE[1]:
        raise ValueError(f"The high limit must be between {HIGH_RANGE[0]} and {HIGH_RANGE[1]} bpm.")
    if low_bpm is not None and not LOW_RANGE[0] <= low_bpm <= LOW_RANGE[1]:
        raise ValueError(f"The low limit must be between {LOW_RANGE[0]} and {LOW_RANGE[1]} bpm.")
    if high_bpm is not None and low_bpm is not None and low_bpm >= high_bpm:
        raise ValueError("The low limit must be below the high limit.")
    if not MINUTES_RANGE[0] <= sustain_minutes <= MINUTES_RANGE[1]:
        raise ValueError(f"Choose between {MINUTES_RANGE[0]} and {MINUTES_RANGE[1]} minutes.")


def save_alert(
    session: Session,
    parent: Person,
    actor: Person,
    *,
    high_bpm: int | None,
    low_bpm: int | None,
    sustain_minutes: int = 2,
    notify_family: bool = True,
    check_on_parent: bool = False,
    enabled: bool = True,
) -> HeartRateAlertRow:
    """Callers must already have checked that `actor` may see this parent's health."""
    if enabled:
        validate(high_bpm, low_bpm, sustain_minutes)
    row = session.get(HeartRateAlertRow, parent.id) or HeartRateAlertRow(
        parent_id=parent.id, updated_by=actor.id
    )
    row.high_bpm, row.low_bpm, row.sustain_minutes = high_bpm, low_bpm, sustain_minutes
    row.notify_family, row.check_on_parent, row.enabled = notify_family, check_on_parent, enabled
    row.updated_by, row.updated_at, row.next_check_at = actor.id, utcnow(), None
    session.add(row)
    session.add(
        AccessAudit(
            id=new_id("audit"),
            actor_id=actor.id,
            parent_id=parent.id,
            action="heart_rate_alert.updated",
        )
    )
    described = describe_alert(row)
    timeline.record(
        session,
        parent.id,
        "health.alert_limit_set",
        f"{actor.display_name} set a heart-rate alert: {described}."
        if described
        else f"{actor.display_name} turned off heart-rate alerts.",
        data={
            "high_bpm": high_bpm,
            "low_bpm": low_bpm,
            "minutes": sustain_minutes,
            "enabled": enabled,
        },
        visibility=timeline.HEALTH,
    )
    session.flush()
    return row


def crossing(
    samples: Sequence[tuple[datetime, float]], rule: HeartRateAlertRow, now: datetime
) -> tuple[str, float] | None:
    """Pure rule: ('above'|'below', newest bpm) only when the whole window is beyond a limit."""
    start = now - timedelta(minutes=rule.sustain_minutes)
    inside = [(t, v) for t, v in samples if start <= t <= now]
    if len(inside) < 3:
        return None
    if (now - inside[-1][0]).total_seconds() > FRESH_SECONDS:
        return None
    if (inside[0][0] - start).total_seconds() > MAX_GAP_SECONDS:
        return None
    if any(
        (b[0] - a[0]).total_seconds() > MAX_GAP_SECONDS
        for a, b in zip(inside, inside[1:], strict=False)
    ):
        return None
    values = [v for _, v in inside]
    if rule.high_bpm is not None and min(values) > rule.high_bpm:
        return "above", values[-1]
    if rule.low_bpm is not None and max(values) < rule.low_bpm:
        return "below", values[-1]
    return None


def _fire(
    session: Session,
    rule: HeartRateAlertRow,
    parent: Person,
    direction: str,
    bpm: int,
    now: datetime,
) -> None:
    limit = rule.high_bpm if direction == "above" else rule.low_bpm
    local = now.astimezone(ZoneInfo(parent.timezone)).strftime("%I:%M %p").lstrip("0")
    stamp = now.strftime("%Y%m%d%H%M")
    asked = False
    if rule.check_on_parent and safety.active_incident(session, parent.id) is None:
        requester = session.get(Person, rule.updated_by) or parent
        safety.open_family_concern(
            session,
            parent,
            requester,
            prompt=f"Hi {parent.display_name}, your watch shows a heart rate {direction} the limit your "
            "family set. Are you okay? Reply OK if you're fine, or HELP if you'd like someone to come.",
            # This incident entry is visible to all family; the numbers stay in the health entry below.
            summary=f"FarCare sent {parent.display_name} a check-in after a watch alert.",
        )
        asked = True
    notified = 0
    if rule.notify_family:
        members = session.exec(
            select(FamilyLink, Person).where(
                FamilyLink.parent_id == parent.id,
                FamilyLink.member_id == Person.id,
                FamilyLink.share_health == True,  # noqa: E712
                Person.role == "child",
            )
        ).all()
        for link, member in members:
            label = link.parent_label or parent.display_name
            next_step = (
                f"I've asked {label} if they're okay." if asked else "You may want to check in."
            )
            send_to_person(
                session,
                member,
                f"FarCare: {label}'s heart rate has been {direction} your alert limit of {limit} bpm "
                f"for {rule.sustain_minutes} min (now {bpm} bpm, {local} their time). {next_step} "
                "This is your own limit, not a medical alert. If you think it's an emergency, call "
                "local emergency services.",
                f"hr-alert:{parent.id}:{stamp}:{member.id}",
                parent_id=parent.id,
                visibility="health",
            )
            notified += 1
    rule.last_alert_at, rule.last_alert_bpm = now, bpm
    session.add(rule)
    who = [f"texted {notified} family member{'s' if notified != 1 else ''}"] if notified else []
    who += [f"asked {parent.display_name} if they're okay"] if asked else []
    timeline.record(
        session,
        parent.id,
        "health.alert_limit_crossed",
        f"Heart rate stayed {direction} the alert limit of {limit} bpm for {rule.sustain_minutes} min "
        f"(now {bpm} bpm)." + (f" FarCare {' and '.join(who)}." if who else ""),
        data={
            "direction": direction,
            "limit_bpm": limit,
            "bpm": bpm,
            "family_notified": notified,
            "asked_parent": asked,
        },
        visibility=timeline.HEALTH,
    )


def _evaluate(session: Session, rule: HeartRateAlertRow, now: datetime) -> int:
    if rule.last_alert_at is not None and now - rule.last_alert_at < timedelta(
        minutes=rule.cooldown_minutes
    ):
        return 0
    parent = session.get(Person, rule.parent_id)
    subject = _subject(session, rule.parent_id)
    if parent is None or parent.role != "parent" or subject is None:
        return 0
    samples = recent_heart_rate(
        session, subject, now=now, seconds=rule.sustain_minutes * 60 + MAX_GAP_SECONDS
    )
    hit = crossing(samples, rule, now)
    if hit is None:
        return 0
    _fire(session, rule, parent, hit[0], round(hit[1]), now)
    return 1


def check_alerts(session: Session, *, now: datetime | None = None) -> int:
    """Worker step: evaluate due limits; returns how many fired. Never commits."""
    clock = now or utcnow()
    try:
        with session.begin_nested():
            rules = session.exec(
                select(HeartRateAlertRow)
                .where(
                    HeartRateAlertRow.enabled == True,  # noqa: E712
                    or_(
                        HeartRateAlertRow.next_check_at.is_(None),  # type: ignore[union-attr]
                        HeartRateAlertRow.next_check_at <= clock,  # type: ignore[operator]
                    ),
                )
                .order_by(HeartRateAlertRow.parent_id)
                .limit(5)
            ).all()
    except SQLAlchemyError as exc:
        if getattr(exc, "connection_invalidated", False):
            raise
        log.warning("Heart-rate alert settings unavailable; skipped")
        return 0
    fired = 0
    for rule in rules:
        rule.next_check_at = clock + CHECK_EVERY
        session.add(rule)
        try:
            with session.begin_nested():
                fired += _evaluate(session, rule, clock)
        except SQLAlchemyError as exc:
            if getattr(exc, "connection_invalidated", False):
                raise
            log.warning("Heart-rate alert check unavailable; will retry")
    session.flush()
    return fired
