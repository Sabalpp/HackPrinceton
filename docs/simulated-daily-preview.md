# Temporary simulated daily comparison beside a live watch

An operator can temporarily show a simulated daily comparison for one consenting parent's
dashboard while its separate watch card continues reading the real sensor endpoint. This is
a display preview, not an ingestion backfill. Real daily rows, wearer mappings, BLE collection,
Tiger delivery, personal baseline inputs, and live heart-rate processing are unchanged.

Set the server-only `SIMULATED_DAILY_PREVIEW` environment variable to a JSON object with
`parent_id` and `expires_at`. Use the verified target parent ID and an explicit timezone-aware
ISO timestamp no more than 48 hours ahead. Never commit the actual parent ID or configuration.
Apply it to the API runtime and redeploy through the normal reviewed release process.

The daily summary then returns 35 synthetic local-calendar dates ending on that parent's
yesterday: 30 usual days and five changed days. The recent example values are 2,560 daily
steps, 360 minutes asleep, and 77 resting BPM. Every observation has simulated source and mode;
the existing dashboard shows its Simulated badge. The actual instantaneous watch BPM remains
independent and may differ from the simulated daily resting BPM.

Preview trends carry simulated wording and retain calculated comparisons. `notable` and
`suggest_checkin` are false so these display values cannot initiate the existing proactive
health check-in flow. This also means automated daily-summary check-ins for this parent are
suppressed while previewing; independent live heart-rate processing is unchanged. Personal
baseline panels still use actual observations and may remain in their learning state.

Removing the variable, expiration, invalid configuration, or an invalid parent timezone restores
the ordinary summary. No restore/import is needed because the preview never writes database
records. Other parents are unaffected. Existing server-side sharing permissions still apply
before the summary is loaded.

Verify the target's authenticated daily summary has `data_mode: simulated`, 35 observations,
and no check-in suggestion. Check the sensor endpoint independently for the same source link,
real data mode, and advancing source timestamps; do not expect a constant live BPM. Check that
another parent's daily summary and the target's stored real observations are unchanged.

Tests: `cd backend && .venv/bin/pytest tests/test_health_daily_preview.py tests/test_health_trends.py tests/test_health_live.py -q`.
