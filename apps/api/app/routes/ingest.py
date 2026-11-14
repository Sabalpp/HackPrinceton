"""Machine ingestion: fall detector events and health-data sync. Requires the ingest key."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.auth import require_ingest_key
from app.db import get_session
from app.health import service as health
from app.models import HealthObservationRow, Person
from app.schemas.contracts import (
    FallIngestResult,
    FallSuspectedEvent,
    HealthIngestResult,
    HealthObservation,
)
from app.services import safety, timeline

router = APIRouter(prefix="/api", tags=["ingestion"], dependencies=[Depends(require_ingest_key)])


@router.post("/events/fall", response_model=FallIngestResult)
def ingest_fall(event: FallSuspectedEvent, session: Session = Depends(get_session)):
    try:
        return safety.ingest_fall_event(session, event)
    except LookupError:
        raise HTTPException(404, "Unknown parent_id") from None


def _changed(session: Session, observations: list[HealthObservation]) -> set[str]:
    """Ids that are new or whose values differ from what we already stored."""
    fields = ("steps", "resting_heart_rate", "sleep_minutes", "data_mode", "source")
    changed = set()
    for o in observations:
        row = session.get(HealthObservationRow, o.observation_id)
        if row is None or any(
            getattr(row, f) != (o.data_mode.value if f == "data_mode" else getattr(o, f))
            for f in fields
        ):
            changed.add(o.observation_id)
    return changed


@router.post("/health/observations", response_model=HealthIngestResult)
def ingest_health(observations: list[HealthObservation], session: Session = Depends(get_session)):
    if len(observations) > 1000:
        raise HTTPException(413, "At most 1000 observations per request")
    for parent_id in {o.parent_id for o in observations}:
        p = session.get(Person, parent_id)
        if p is None or p.role != "parent":
            raise HTTPException(404, f"Unknown parent_id {parent_id}")
    changed_ids = _changed(session, observations)
    accepted = health.ingest(session, observations)

    # Make the handoff visible: one timeline event per parent per delivery (needs the
    # parent's share_health permission to be seen), plus a receipt for the sender.
    notable: list[str] = []
    suggest = False
    for parent_id in sorted({o.parent_id for o in observations if o.observation_id in changed_ids}):
        batch = [o for o in observations if o.parent_id == parent_id]
        modes = {o.data_mode.value for o in batch}
        mode = modes.pop() if len(modes) == 1 else "mixed"
        days = len({o.timestamp.date() for o in batch})
        newest = max(batch, key=lambda o: o.timestamp)
        latest = (
            f"; latest day {newest.timestamp:%b %-d}: {newest.steps:,} steps"
            if newest.steps is not None
            else ""
        )
        summ = health.summary(session, parent_id)
        changes = [t.observation for t in summ.trends if t.notable]
        timeline.record(
            session,
            parent_id,
            "health.synced",
            f"Received {days} day{'s' if days != 1 else ''} of activity data ({mode}){latest}.",
            data={
                "days": days,
                "data_mode": mode,
                "sources": sorted({o.source for o in batch}),
                "suggest_checkin": summ.suggest_checkin,
                "notable_changes": changes,
            },
            visibility=timeline.HEALTH,
        )
        notable += changes
        suggest = suggest or summ.suggest_checkin

    all_modes = {o.data_mode.value for o in observations}
    return HealthIngestResult(
        accepted=accepted,
        changed=len(changed_ids),
        days_received=len({(o.parent_id, o.timestamp.date()) for o in observations}),
        data_mode=(all_modes.pop() if len(all_modes) == 1 else "mixed") if all_modes else "none",  # type: ignore[arg-type]
        suggest_checkin=suggest,
        notable_changes=notable,
    )
