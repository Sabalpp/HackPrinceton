"""Explicit, expiring simulated daily display; never writes sensor or daily records."""

import json
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.contracts import HealthObservation, HealthSummary

from .trends import DISCLAIMER, compute_trends


def summary(parent_id: str, timezone: str, days: int, now: datetime) -> HealthSummary | None:
    """An operator may opt one parent into a temporary display using server configuration.

    SIMULATED_DAILY_PREVIEW is {"parent_id": "...", "expires_at": "ISO timestamp"}.
    No implicit opt-in, permanent setting, database rewrite, or live sensor substitution.
    """
    try:
        config = json.loads(os.environ.get("SIMULATED_DAILY_PREVIEW", "{}"))
        if config.get("parent_id") != parent_id:
            return None
        expires = datetime.fromisoformat(config["expires_at"])
        if expires.tzinfo is None or not now < expires <= now + timedelta(days=2):
            return None
        end = now.astimezone(ZoneInfo(timezone)).date() - timedelta(days=1)
    except (ValueError, TypeError, KeyError, AttributeError, ZoneInfoNotFoundError):
        return None

    rows = []
    for age in reversed(range(min(max(days, 0), 35))):
        day = end - timedelta(days=age)
        changed = age < 5
        rows.append(
            HealthObservation(
                observation_id=f"preview_{parent_id}_{day.isoformat()}",
                parent_id=parent_id,
                source="simulated",
                data_mode="simulated",
                timestamp=datetime.combine(day, datetime.min.time(), UTC),
                steps=2560 if changed else 4992,
                sleep_minutes=360 if changed else 451,
                resting_heart_rate=77 if changed else 67,
            )
        )
    trends = compute_trends(rows)
    for trend in trends:
        # Existing proactive dispatch uses notable, not just suggest_checkin.
        # A display-only preview must not initiate a real care workflow.
        trend.notable = False
        trend.observation = "Simulated daily preview: " + trend.observation
    return HealthSummary(
        parent_id=parent_id,
        data_mode="simulated",
        observations=rows,
        trends=trends,
        suggest_checkin=False,
        disclaimer=(
            "Daily comparison values are simulated for demonstration. "
            "The separate watch card continues to show actual sensor readings. "
            "This preview does not trigger health check-ins. " + DISCLAIMER
        ),
    )
