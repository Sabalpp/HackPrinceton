"""Serialize care transitions with worker retries and timers on PostgreSQL."""

from collections.abc import Iterator

from fastapi import HTTPException
from sqlmodel import Session

from app.services.care_requests import InvalidTransition
from app.worker import WorkerBusy, ensure_lease, locked_session


def action_session() -> Iterator[Session]:
    ensure_lease()
    try:
        with locked_session("api", track_heartbeat=False) as (session, _lease):
            yield session
    except WorkerBusy:
        raise HTTPException(
            503, "FarCare is processing an update. Please retry.", headers={"Retry-After": "2"}
        ) from None
    except InvalidTransition:
        raise HTTPException(409, "This request has changed. Refresh before trying again.") from None
