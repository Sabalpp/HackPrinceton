"""OWNER: Sabal. Google Health API (Fitbit) provider — TO BE IMPLEMENTED.

Facts verified 2026-11-14 (see docs/agents/SABAL.md for details and links):
- The legacy Fitbit Web API and Fitbit OAuth endpoints were turned off on 2026-11-02.
  Use the Google Health API: base https://health.googleapis.com, version v4.
- Data points: users/me/dataTypes/{dataType}/dataPoints (list), plus rollUp / dailyRollUp.
- Relevant data types: steps, sleep, heart-rate, daily-resting-heart-rate.
- OAuth scopes (Google OAuth 2.0):
    https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
    https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
    https://www.googleapis.com/auth/googlehealth.sleep.readonly
- There is NO raw accelerometer/gyroscope or fall-event data type. Do not use this
  provider for fall detection.

Contract: `fetch_daily(parent_id, start, end)` must return normalized
`HealthObservation`s with source="fitbit" and data_mode="live" (one per day), then the
sync job calls `app.health.service.ingest()`. Verify exact request/response shapes against
the official reference before implementing; do not guess field names.
"""

from datetime import date

from app.schemas.contracts import HealthObservation


def fetch_daily(parent_id: str, start: date, end: date) -> list[HealthObservation]:
    raise NotImplementedError("Sabal: implement Google Health API sync (see module docstring).")
