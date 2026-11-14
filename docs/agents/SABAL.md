# Sabal — Google Health API (Fitbit) integration and health trends

**Branch:** `feature/sabal-fitbit` · **Owns:** `apps/api/app/health/**`, health tests,
`HealthObservationRow` (coordinate schema changes with Sumit). You are also the **repo admin**.

## Admin tasks first (≈10 min, unblocks everyone)

1. After Sumit pushes the foundation to `main`: `gh auth login`, then `scripts/setup-github.sh`.
   This sets up squash-only merges, auto-merge, the `main` ruleset with required checks + 1
   approval, and creates the three feature branches.
2. Vercel: import the repo, root directory `apps/web`, env `NEXT_PUBLIC_API_URL`.
3. Optional: add the `AUTOMERGE_TOKEN` secret (fine-grained PAT, this repo only, Contents +
   Pull requests read/write) so auto-merged PRs trigger the Deploy workflow.

## Your handoff to Sumit (already working with mock data)

`apps/api/scripts/mock_fitbit_sender.py` stands in for your sync **today**. It is standalone
(HTTP only, no backend imports) and sends exactly what your real code must send:

```bash
cd apps/api && uv run python scripts/mock_fitbit_sender.py            # 35-day backfill
uv run python scripts/mock_fitbit_sender.py --stream --interval 60    # today's running total
```

- `POST /api/health/observations` with header `X-FarCare-Ingest-Key: $INGEST_API_KEY`, body a
  JSON array of `HealthObservation` (one per parent per day, `observation_id` stable per day,
  for example `fitbit_<parent>_<YYYY-MM-DD>`, so re-sends upsert).
- The receipt tells you what landed: `accepted`, `changed`, `notable_changes`,
  `suggest_checkin`. A retry of identical data returns `changed: 0` and adds nothing to the
  dashboard timeline.
- **Swap in your real data** by replacing the mock's `observation()` with values from the
  Google Health API, using `source="fitbit"` and `data_mode="live"`. Nothing on Sumit's side changes.
- Known gap (your module): if simulated and live rows exist for the **same day** (different
  `observation_id`s), `health/service.recent()` counts both. Either reset simulated data first
  (`POST /api/demo/reset?include_health=true`, or `SEED_HEALTH_DATA=false`) or dedupe per day,
  preferring live.

## What exists already (working, simulated)

- `health/simulated.py`: 35 days of labeled synthetic data (steps decline over the last 5 days).
- `health/trends.py`: personal baseline (mean of up to 30 days before the last 5) vs. recent
  mean, notable thresholds, plain-language **non-diagnostic** observations.
- `health/service.py`: `ingest()` (upsert by `observation_id`), `recent()`, `summary()`.
- `POST /api/health/observations` (ingest key) accepts `HealthObservation[]`.
- `health/google_health.py`: **stub to implement**.

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

## Tasks (priority order)

1. **Time-box OAuth (≤ 3 h).** Google Cloud project → enable the Google Health API → OAuth
   consent screen in *Testing* mode with the Fitbit owner as a test user → desktop/web client.
   Get a refresh token for the demo account with a tiny local script. Store client
   id/secret/refresh token only in env, never in git.
2. **Implement `google_health.fetch_daily(parent_id, start, end)`** → one `HealthObservation`
   per day (`source="fitbit"`, `data_mode="live"`, `timestamp` = day start UTC). Prefer
   `dailyRollUp` for steps; take sleep minutes from the main sleep session; RHR from
   `daily-resting-heart-rate`. Handle missing days (nulls), timezones, and pagination.
3. **Sync job:** `apps/api/scripts/sync_health.py`, either calling `health.service.ingest()`
   directly or POSTing to `/api/health/observations` with the ingest key. Run on demand before
   the demo, plus optionally on a schedule.
4. **Live + simulated together:** the demo may show live recent days on top of a simulated
   baseline. If you mix them, `summary().data_mode` becomes `"mixed"`, and the UI must say so.
   Never relabel simulated data as live.
5. **Tests:** add `tests/test_health_*.py` with recorded/fixture API responses (no network in
   CI). Cover normalization, missing data, the baseline window, and the trend thresholds. Keep
   observation text non-diagnostic (the existing test checks for banned words).
6. **Document** in this file which metrics are live vs. simulated in the final demo.
7. Optional (Tiger Data prize): `health_observations` as a Timescale hypertable on Tiger Cloud
   (`DATABASE_URL`), with a continuous aggregate for weekly averages. Coordinate with Sumit:
   one database only.

## Acceptance criteria

- [ ] Simulated fallback still works with no Google credentials (the default).
- [ ] With credentials, real Fitbit days appear on the dashboard labeled live.
- [ ] Trends describe changes vs. the person's own baseline and never diagnose.
- [ ] Tests pass in CI without network access.
