"""Machine ingestion: fall detector events and health-data sync. Requires the ingest key."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.auth import require_ingest_key
from app.db import get_session
from app.health import service as health
from app.models import Person
from app.schemas.contracts import (
    FallIngestResult,
    FallSuspectedEvent,
    HealthIngestResult,
    HealthObservation,
)
from app.services import safety

router = APIRouter(prefix="/api", tags=["ingestion"], dependencies=[Depends(require_ingest_key)])


@router.post("/events/fall", response_model=FallIngestResult)
def ingest_fall(event: FallSuspectedEvent, session: Session = Depends(get_session)):
    try:
        return safety.ingest_fall_event(session, event)
    except LookupError:
        raise HTTPException(404, "Unknown parent_id") from None


@router.post("/health/observations", response_model=HealthIngestResult)
def ingest_health(observations: list[HealthObservation], session: Session = Depends(get_session)):
    if len(observations) > 1000:
        raise HTTPException(413, "At most 1000 observations per request")
    for parent_id in {o.parent_id for o in observations}:
        p = session.get(Person, parent_id)
        if p is None or p.role != "parent":
            raise HTTPException(404, f"Unknown parent_id {parent_id}")
    return HealthIngestResult(accepted=health.ingest(session, observations))
