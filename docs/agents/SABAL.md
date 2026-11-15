# Sabal — Google Health API (Fitbit) integration and health trends

**Branch:** `feature/sabal-fitbit` · **Owns:** `backend/collector/**`, `research/fitbit_air/**`,
`backend/app/health/**`, health tests, and the local launcher/verification scripts.
Coordinate `HealthObservationRow`, API runtime, and deployment changes with Sumit. You are
also the **repo admin**. Read [the file map](../codebase-organization.md) before moving code.

## Branch and deployment workflow

Work on `feature/sabal-fitbit`, submit a focused PR into production `main`, and preserve
history with an ordinary merge after required checks/reviews. After merge, use
`scripts/sync-after-merge.sh`. Do not rerun the old administrative setup script to change
repository governance during routine feature work or cleanup.

Cloudflare uses Workers + Containers; see [deployment settings](../cloudflare-deployment.md).
Keep its build root at the repository root. The collector runs near the watch on the Mac;
it is not part of the API or web container image.

## Current health pipeline

1. `farcare_collector.integrations.fitbit_hr` records individual BLE HR notifications.
2. `farcare_collector.integrations.google_health` handles authorized Google OAuth and
   exports original cloud response pages and TCX files.
3. `farcare_collector.services.health_pipeline` normalizes those records into a durable
   SQLite outbox; the Tiger integrations deliver measurements and original record bytes.
4. `app.health.tiger`, `sync_tiger`, `live`, and `live_sync` read the Tiger records for the
   API and worker. `app.health.sources` requires explicit consent and an eligible real
   parent profile before linking a collector subject.
5. Parent and permitted family dashboards display available values, dates and freshness;
   missing measurements stay missing. Photon/Gemini continue through the existing API
   integration and care-workflow modules.

The same-named files are different components: the collector's `integrations/google_health.py`
is the working OAuth/export client; `backend/app/health/google_health.py` remains an
unimplemented direct-provider scaffold. The current path goes through Tiger and does not
call that scaffold. Do not remove it merely because the filenames match, or report it as
implemented. [Tiger handoff and permissions](../tiger-health-pipeline.md) describe the current
integration.

The existing mock sender is still available for synthetic tests and demonstrations:

```bash
cd backend && uv run python scripts/mock_fitbit_sender.py            # 35-day backfill
uv run python scripts/mock_fitbit_sender.py --stream --interval 60    # today's running total
```

- `POST /api/health/observations` with header `X-FarCare-Ingest-Key: $INGEST_API_KEY`, body a
  JSON array of `HealthObservation` (one per parent per day, `observation_id` stable per day,
  for example `fitbit_<parent>_<YYYY-MM-DD>`, so re-sends upsert).
- The receipt tells you what landed: `accepted`, `changed`, `notable_changes`,
  `suggest_checkin`. A retry of identical data returns `changed: 0` and adds nothing to the
  dashboard timeline.
- Keep the mock sender synthetic. Use the existing collector/Tiger adapters for real data;
  never relabel generated history as `live` to make a demo appear connected.
- Keep `SEED_HEALTH_DATA=false` for real profiles. A record's source and `data_mode` must
  survive ingestion, summaries and the dashboard.

## Existing API modules

- `health/simulated.py`: 35 days of labeled synthetic data (steps decline over the last 5 days).
- `health/trends.py`: personal baseline (mean of up to 30 days before the last 5) vs. recent
  mean, notable thresholds, plain-language **non-diagnostic** observations.
- `health/service.py`: `ingest()` (upsert by `observation_id`), `recent()`, `summary()`.
- `POST /api/health/observations` (ingest key) accepts `HealthObservation[]`.
- `health/live.py`, `sources.py`, `live_sync.py`: authenticated sensor views, trusted wearer
  mapping, and worker synchronization.
- `health/google_health.py`: retained direct-provider scaffold, outside the active Tiger path.

## Facts verified 2026-11-14

- The legacy Fitbit Web API and Fitbit OAuth endpoints were **turned off 2026-11-02**. Use the
  **Google Health API**: base `https://health.googleapis.com`, version `v4`.
- List data points: `users/me/dataTypes/{dataType}/dataPoints`. Aggregates: `rollUp`,
  `dailyRollUp`. Webhooks exist.
- Data types for us: `steps`, `sleep`, `heart-rate`, `daily-resting-heart-rate`.
- OAuth scopes:
  `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`,
  `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`,
  `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
- **No raw accelerometer/gyroscope or fall data type exists.** Don't build fall detection on it.
- Docs: <https://developers.google.com/health/data-types>,
  <https://developers.google.com/health/migration/api-specifications>,
  <https://developers.google.com/health/reference/rpc/google.devicesandservices.health.v4>.
  Read the exact request/response shapes there before coding. Don't guess field names.

## Maintenance checks

- Keep the collector's Python 3.9+ environment separate from the API's Python 3.12 environment.
  Preserve `FARCARE_WORKSPACE`, OAuth cache, outbox checkpoints and raw file locations when
  moving source; package SQL assets with their Tiger integration.
- Run synthetic collector tests, research probe tests and backend health tests after path or
  import changes. The managed-Node fallback has its own synthetic tests; it is not redundant
  just because the Python collector also publishes records.
- Distinguish successful tests from actual collection, live database connectivity and SMS
  delivery. Never expose cached health records or credentials in Git/CI artifacts.
- Keep research in `research/fitbit_air/`. Existing Android and third-party research workspaces
  remain local and are not backend dependencies. This cleanup adds no fall functionality.
- Follow the shared contract-change process with Sumit and Saksham whenever an API or frontend
  change is needed. Organizational changes alone should not alter those contracts.
