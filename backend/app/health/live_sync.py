"""Durable daily projection and optional family check-in prompts for linked sources.

Run under the worker's singleton transaction. At most one due parent is read per
cycle and each link is checked at most once per minute. Sensor SQL has a short
timeout and a savepoint, so an absent collector schema cannot abort other work.
This uses the existing descriptive baseline rules, never a diagnostic threshold.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.db import utcnow
from app.health import service, tiger
from app.health.live_models import WatchSourceLink
from app.health.trends import compute_trends
from app.integrations.messaging import send_to_person
from app.models import FamilyLink, HealthObservationRow, OutboundMessage, Person
from app.schemas.contracts import HealthObservation
from app.services import timeline

log = logging.getLogger(__name__)
SYNC_SECONDS = 60


def _fetch(
    session: Session, link: WatchSourceLink, parent: Person, now: datetime
) -> list[HealthObservation]:
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("Tiger PostgreSQL is not configured")
    today = now.astimezone(ZoneInfo(parent.timezone)).date()
    session.execute(text("SET LOCAL statement_timeout = '5s'"))
    return tiger.fetch_daily(
        session,
        subject_id=link.subject_id,
        parent_id=parent.id,
        start=today - timedelta(days=34),
        end=today,
        now=now,
        timezone=parent.timezone,
    )


def queue_daily_notices(
    session: Session,
    parent: Person,
    observations: list[HealthObservation],
    *,
    now: datetime,
) -> int:
    """One permission-checked family notice/day, only for recent live trends.

    These thresholds are descriptive product defaults. The notice is a prompt to
    check in, not an emergency, diagnosis, fall prediction or clinical decision.
    Outbound status remains queued/available until the messaging worker handles it.
    """
    if not observations or any(o.source != "fitbit" or o.data_mode != "live" for o in observations):
        return 0
    today = now.astimezone(ZoneInfo(parent.timezone)).date()
    trends = compute_trends(observations, completed_steps_through=today - timedelta(days=1))
    noteworthy = [
        trend
        for trend in trends
        if trend.notable
        and any(
            getattr(obs, trend.metric) is not None
            and today - timedelta(days=2) <= obs.timestamp.date() <= today
            for obs in observations
        )
    ]
    if not noteworthy:
        return 0
    labels = {
        "steps": "daily activity",
        "sleep_minutes": "sleep",
        "resting_heart_rate": "resting heart rate",
    }
    changes = ", ".join(labels[t.metric] for t in noteworthy)
    linked = session.exec(
        select(FamilyLink, Person).where(
            FamilyLink.parent_id == parent.id,
            FamilyLink.member_id == Person.id,
            FamilyLink.share_health == True,  # noqa: E712
            Person.role == "child",
        )
    ).all()
    created = 0
    for membership, member in linked:
        key = f"health-pattern:{parent.id}:{today.isoformat()}:{member.id}"
        if session.get(OutboundMessage, key) is not None:
            continue
        label = membership.parent_label or parent.display_name
        send_to_person(
            session,
            member,
            f"{label}'s recent {changes} differs from their own earlier pattern. "
            "You may want to check in. Open FarCare for the dated readings and missing-data details. "
            "This is a wellbeing prompt, not a medical alert.",
            key,
            parent_id=parent.id,
            visibility="health",
        )
        created += 1
    if created:
        timeline.record(
            session,
            parent.id,
            "health.pattern_noted",
            "Recent daily measurements changed from the personal baseline; a family check-in prompt was recorded.",
            data={"metrics": [t.metric for t in noteworthy], "data_mode": "live"},
            visibility=timeline.HEALTH,
        )
    return created


def _sync_one(session: Session, link: WatchSourceLink, now: datetime) -> int:
    parent = session.get(Person, link.parent_id)
    if parent is None or parent.role != "parent":
        raise ValueError("Linked parent is unavailable")
    observations = _fetch(session, link, parent, now)
    # No null-only dates may masquerade as collected data. Keep null corrections
    # for previously projected dates so revised source data can clear old values.
    existing = {
        row.observation_id: row
        for row in session.exec(
            select(HealthObservationRow).where(
                HealthObservationRow.parent_id == parent.id,
                HealthObservationRow.observation_id.in_([o.observation_id for o in observations]),
            )
        ).all()
    }
    observations = [
        o
        for o in observations
        if o.observation_id in existing
        or any(
            getattr(o, metric) is not None
            for metric in ("steps", "resting_heart_rate", "sleep_minutes")
        )
    ]
    payload = [o.model_dump(mode="json") for o in observations]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    changed = [
        o
        for o in observations
        if o.observation_id not in existing
        or any(
            getattr(existing[o.observation_id], name) != getattr(o, name)
            for name in (
                "source",
                "data_mode",
                "timestamp",
                "steps",
                "resting_heart_rate",
                "sleep_minutes",
            )
        )
    ]
    if digest == link.last_digest and not changed:
        return 0
    if changed:
        service.ingest(session, changed)
        timeline.record(
            session,
            parent.id,
            "health.synced",
            "Source-reported daily Fitbit measurements updated.",
            data={"count": len(changed), "data_mode": "live"},
            visibility=timeline.HEALTH,
        )
        queue_daily_notices(session, parent, observations, now=now)
    link.last_digest = digest
    return len(changed)


def sync_linked_sources(session: Session, *, now: datetime | None = None) -> int:
    """Return changed daily records, without committing the caller's transaction."""
    clock = now or utcnow()
    try:
        with session.begin_nested():
            links = session.exec(
                select(WatchSourceLink)
                .where(
                    WatchSourceLink.enabled == True,  # noqa: E712
                    or_(
                        WatchSourceLink.next_sync_at.is_(None),
                        WatchSourceLink.next_sync_at <= clock,
                    ),
                )
                .order_by(
                    WatchSourceLink.next_sync_at.asc().nullsfirst(), WatchSourceLink.parent_id
                )
                .limit(1)
            ).all()
    except SQLAlchemyError as exc:
        if getattr(exc, "connection_invalidated", False):
            raise  # Let the worker rollback its entire unit and reconnect.
        log.warning("Watch source registry is unavailable; sensor sync skipped")
        return 0
    changed = 0
    for link in links:
        try:
            with session.begin_nested():
                changed += _sync_one(session, link, clock)
        except (SQLAlchemyError, ValueError, KeyError) as exc:
            if getattr(exc, "connection_invalidated", False):
                raise
            link.sync_status = "unavailable"
            log.warning("Watch source synchronization unavailable; retry scheduled")
        else:
            link.sync_status = "ok"
            link.last_sync_at = clock
        link.next_sync_at = clock + timedelta(seconds=SYNC_SECONDS)
        session.add(link)
    session.flush()
    return changed
