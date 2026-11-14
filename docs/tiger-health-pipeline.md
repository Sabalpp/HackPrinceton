# Tiger health-data integration

This work extends the latest `feature/sumit-core` backend (`444bb85`), on
`feature/sabal-fitbit`. `main` was older when the integration started. The newer
`feature/saksham-ui` client was inspected read-only for API compatibility.

## Existing backend, reused

FarCare already uses SQLModel/PostgreSQL through `DATABASE_URL`, with SQLite for
tests. The same Tiger database can hold ordinary application tables and the
`air_health` sensor schema. `HealthObservationRow` stays an ordinary table: its
single-column primary key and `session.get()` behavior should not be changed into
a time-partitioned key without a coordinated migration.

The flow is:

```text
BLE + authorized Google Health exports → durable sensor collector → Tiger air_health
  → app.health.tiger daily projection → authenticated POST /api/health/observations
  → health_observations + health.synced timeline → existing authorized REST/SSE → UI
```

The collector runs outside the web request path. Its canonical view resolves
revisions before the adapter reads. All reads bind an explicit upstream subject.
The ingest route validates the target parent and key; family reads keep existing
`resolve_parent_access`/sharing checks.

## Run

Configure the API's private `.env` (never commit credentials):

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/tsdb?sslmode=require&connect_timeout=10
INGEST_API_KEY=YOUR_PRIVATE_INGEST_KEY
SEED_HEALTH_DATA=false
```

For a controlled local demo, use `MESSAGING_CHANNEL=console`,
`BACKGROUND_TASKS_ENABLED=false`, `PROACTIVE_CHECKINS_ENABLED=false`, and
`PHOTON_DELIVERY=off`. These existing settings prevent the verification run from
contacting anyone. Bind the local API to loopback.

```bash
cd apps/api
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# In a second Terminal, with the same .env:
uv run python -m app.health.sync_tiger \
  --subject-id YOUR_AUTHORIZED_UPSTREAM_SUBJECT \
  --parent-id YOUR_EXISTING_PARENT_ID \
  --timezone America/New_York --days 8 \
  --api-url http://127.0.0.1:8000 --watch-seconds 60
```

The sender needs a nonempty ingest key, allows unencrypted HTTP only for loopback,
does not follow redirects, uses bounded requests, and retries without advancing
its successful-payload digest on failure. Restart replay uses stable observation
IDs. It posts through the route so receipts and SSE events retain their current
behavior. A successful unchanged replay creates no extra health timeline event.

The sensor collector must already supply `air_health.measurements_current` with
event identity, subject/source/stream, metric, times/date, value/unit/kind and
attributes. This adapter is not another Google OAuth implementation. The older
`google_health.fetch_daily` direct-Google stub is not used by this Tiger path.

## Preserved contract and limits

| Existing field | Actual source/handling |
| --- | --- |
| `steps` | One unambiguous stream of non-overlapping step intervals; recorded total, not proof of full-day wear; today remains null |
| `resting_heart_rate` | Explicit Google daily resting-HR measurement, rounded half-up to the contract's integer; never live BLE BPM |
| `sleep_minutes` | Exactly one explicit main, non-nap session's reported minutes asleep, assigned to wake date |
| `timestamp` | Source local-date label encoded as UTC day-start, as required by the existing contract |
| `data_mode` / `source` | `live` / `fitbit`; Tiger is storage, not a sensor type |

Missing and ambiguous values remain null. Alternative devices are not added
together. Cross-midnight steps are not allocated proportionally. The trend engine
uses actual calendar windows, deduplicates identical same-day values, excludes
conflicts, and refuses to compare across real/simulated sources or a zero baseline.

The shared API currently has no fields for instantaneous BPM, SpO2, HRV, skin
temperature, source coverage, or raw sleep stages. Those remain in Tiger. Adding
them requires the shared-contract generation/review process and Saksham's UI
integration; do not send unsupported fields to the existing endpoint.

The UI real-mode client already uses these endpoints and SSE. Set
`NEXT_PUBLIC_USE_MOCKS=false` and `NEXT_PUBLIC_API_URL` to the API origin. Rich
sensor panels and clear mixed/missing-data presentation remain coordinated UI
work. Sumit's proactive-service direction wording also needs review before using
notable upward trends to trigger outreach; this integration leaves check-ins off
in its local verification configuration.

## Verification and ownership

Normalization/projector fixtures are synthetic and offline. Actual canonical
records were independently queried from Tiger, projected, accepted by the real
FastAPI route in an isolated temporary SQLite database, replayed idempotently,
and hidden when sharing was disabled. No real values/fixtures are committed here.

The agent Python environment could not resolve the Tiger hostname. Therefore a
long-running Python API-to-Tiger connection is configured but not claimed as
verified. The sensor uploader's separate permitted network runtime connected over
TLS, stored actual data, and verified archive hashes against the local outbox.

Final verification: **191 backend tests passed**; `ruff check .` passed and
`ruff format --check .` reported 62 formatted files. The TestClient emits one
upstream timeout-deprecation warning; no check was disabled. The real-data
handoff produced eight daily records, preserved nulls, labeled them live, replayed
with zero changed rows, emitted one health sync event and enforced sharing.

All implementation changes are in Sabal-owned health modules and tests; no shared
contract, route, model, messaging service or frontend source was changed. The
adapter and trend fixes were implemented independently, then integrated with the
existing backend test suite. This branch includes the latest core branch ancestry;
review/merge that upstream work before promoting a draft PR into main.
