"""OWNER: Sabal. Deterministic synthetic daily health data (clearly labeled `simulated`).

Story for the demo: ~4,000 steps/day historically; the last 5 days drop sharply, sleep
shortens a little and resting heart rate rises slightly. Values are illustrative only.
"""

import random
from datetime import UTC, date, datetime, timedelta

from app.schemas.contracts import DataMode, HealthObservation


def generate(
    parent_id: str, days: int = 35, end: date | None = None, seed: int = 7
) -> list[HealthObservation]:
    rng = random.Random(f"{parent_id}:{seed}")  # noqa: S311 reproducible synthetic data
    end = end or datetime.now(UTC).date()
    out: list[HealthObservation] = []
    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        recent = i >= days - 5
        decline = (i - (days - 6)) / 5 if recent else 0.0  # 0.2 .. 1.0 over last 5 days
        steps = int(rng.gauss(4000, 450) * (1 - 0.6 * decline))
        sleep = int(rng.gauss(420, 25) - 55 * decline)
        rhr = int(round(rng.gauss(67, 1.5) + 5 * decline))
        out.append(
            HealthObservation(
                observation_id=f"sim_{parent_id}_{day.isoformat()}",
                parent_id=parent_id,
                source="simulated",
                data_mode=DataMode.simulated,
                timestamp=datetime(day.year, day.month, day.day, tzinfo=UTC),
                steps=max(steps, 0),
                resting_heart_rate=rhr,
                sleep_minutes=max(sleep, 0),
            )
        )
    return out
