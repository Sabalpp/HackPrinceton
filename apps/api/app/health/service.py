"""OWNER: Sabal. Storage + summary for health observations."""

from datetime import timedelta

from sqlmodel import Session, select

from app.db import utcnow
from app.health import simulated
from app.health.trends import DISCLAIMER, compute_trends
from app.models import HealthObservationRow
from app.schemas.contracts import DataMode, HealthObservation, HealthSummary


def ingest(session: Session, observations: list[HealthObservation]) -> int:
    """Upsert by observation_id."""
    for obs in observations:
        row = session.get(HealthObservationRow, obs.observation_id)
        data = obs.model_dump()
        data["data_mode"] = obs.data_mode.value
        if row is None:
            session.add(HealthObservationRow(**data))
        else:
            for k, v in data.items():
                setattr(row, k, v)
            session.add(row)
    session.flush()
    return len(observations)


def recent(session: Session, parent_id: str, days: int = 35) -> list[HealthObservation]:
    since = utcnow() - timedelta(days=days)
    rows = session.exec(
        select(HealthObservationRow)
        .where(HealthObservationRow.parent_id == parent_id, HealthObservationRow.timestamp >= since)  # type: ignore[operator]
        .order_by(HealthObservationRow.timestamp)  # type: ignore[arg-type]
    ).all()
    return [
        HealthObservation(
            observation_id=r.observation_id,
            parent_id=r.parent_id,
            source=r.source,  # type: ignore[arg-type]
            data_mode=DataMode(r.data_mode),
            timestamp=r.timestamp,
            steps=r.steps,
            resting_heart_rate=r.resting_heart_rate,
            sleep_minutes=r.sleep_minutes,
        )
        for r in rows
    ]


def summary(session: Session, parent_id: str, days: int = 35) -> HealthSummary:
    obs = recent(session, parent_id, days)
    modes = {o.data_mode.value for o in obs}
    mode = "none" if not modes else (modes.pop() if len(modes) == 1 else "mixed")
    trends = compute_trends(obs)
    return HealthSummary(
        parent_id=parent_id,
        data_mode=mode,  # type: ignore[arg-type]
        observations=obs,
        trends=trends,
        suggest_checkin=any(t.notable for t in trends),
        disclaimer=DISCLAIMER,
    )


def seed_simulated(session: Session, parent_id: str) -> int:
    return ingest(session, simulated.generate(parent_id))
