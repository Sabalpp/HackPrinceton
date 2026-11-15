# FarCare — shared project context

**FarCare — Far away. Always there.** An AI care-coordination platform for aging parents whose
children live far away. HackPrinceton Fall 2026 (Healthcare track; Photon 2.0 SMS/iMessage, Gemini,
optional Tiger Data / Photon Voice).

> A notification tells you something may be wrong. FarCare helps connect your parent with
> someone who can actually be there.

## Read this first (every Claude session)

1. This file, then [AGENTS.md](AGENTS.md) (rules you must follow).
2. Your developer file: [Sumit](docs/agents/SUMIT.md) · [Saksham](docs/agents/SAKSHAM.md) ·
   [Sabal](docs/agents/SABAL.md). Work only on your branch and owned paths.
3. Contracts: [docs/api-contracts.md](docs/api-contracts.md). Photon track: [docs/photon-track.md](docs/photon-track.md). Architecture:
   [docs/architecture.md](docs/architecture.md). Demo: [docs/demo-plan.md](docs/demo-plan.md).
4. [Codebase organization](docs/codebase-organization.md): moved collector files, local data
   paths, launch commands, and the boundary between production code and research.

## The one workflow that must always work

Parent texts FarCare (SMS or iMessage via Photon) → Gemini understands → backend picks an **authorized**
Care Circle member → parent approves (YES) → Photon texts the responder → responder replies YES →
request state updates → parent + child are told via Photon → dashboard updates live (SSE).
Plus: simulated possible-fall → safety check → no reply/HELP → deterministic escalation.

## Repository layout

```text
apps/api/                FastAPI backend (Python 3.12, uv) — Sumit owns; Sabal owns app/health/
  app/schemas/contracts.py   SINGLE SOURCE OF TRUTH for shared payloads
  app/services/              care_requests (state machine), safety (incidents), conversation (router)
  app/integrations/          photon/ (2.0 Beta client, webhook), gemini/ (agent + fallback), messaging
  app/health/                Tiger live readings, trusted source registry, daily sync, personal baselines
  app/fall_detection/        synthetic IMU traces + explainable detector
  tests/                     API, permissions, SMS, sensor and worker tests (SQLite + Postgres)
apps/collector/          Existing Mac collector (Python 3.9+) — Sabal owns
  farcare_collector/integrations/  BLE, Google OAuth/exports, Tiger delivery and SQL assets
  farcare_collector/health/        normalization, descriptive features and offline reports
  farcare_collector/services/      durable SQLite ingestion and polling
  tests/                          synthetic Python and Node tests
  runtime/                        managed-Node publisher and Google refresh fallback
apps/web/                Next.js 16 + TypeScript + Tailwind 4 — Saksham owns
  lib/api-types.ts           GENERATED from packages/contracts/openapi.json (never hand-edit)
  lib/api.ts, lib/mocks.ts   typed client + contract-checked mocks (NEXT_PUBLIC_USE_MOCKS=true)
packages/contracts/      exported JSON Schemas + OpenAPI + fixtures (generated; CI checks drift)
scripts/                 setup-github.sh (admin, once), smoke_test.sh, sync-after-merge.sh
  local/                    Mac launcher, private preview preparation, offline maintenance
  verification/             manual checks, some requiring existing private records
research/fitbit_air/      existing proprietary-BLE probe and candidate-key experiment
docs/                    architecture, contracts, demo plan, per-developer instructions
```

The collector was moved from the surrounding workspace; it is not a replacement backend.
Use package-qualified imports and `python -m farcare_collector...` entry points. `FARCARE_WORKSPACE`
selects private recordings, Google tokens and pipeline state. In the existing `app_repo`
checkout the default remains its parent workspace; a normally named checkout defaults to its
repository root. Set an explicit external directory for new setups. No personal data moved
into Git. Android/emulator and third-party reverse-engineering workspaces remain local.

## Run it

```bash
# Backend (http://localhost:8000, docs at /docs). Works with no keys: console messaging + fallback AI.
cd apps/api && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload --timeout-graceful-shutdown 2
# Watch everything live (phones + dashboard + demo buttons): http://localhost:8000/console
uv run pytest                                   # all backend tests (TEST_DATABASE_URL=postgresql://... for Postgres)
uv run python scripts/export_contracts.py       # after ANY contract change

# Frontend (http://localhost:3000)
cd apps/web && cp .env.example .env.local && npm install && npm run dev
npm run gen:api                                 # after contracts change
```

**Mock mode** (no phones, no Fitbit yet): in `apps/api/.env` set `MESSAGING_CHANNEL=console`
(simulated messages even with Photon keys) and `SEED_HEALTH_DATA=false`. Then
`uv run python scripts/mock_fitbit_sender.py` plays Sabal's sync and sends health data over
the real ingest API.

Demo tokens (dev only, refused in production): child `dev-child-token`, parent
`dev-parent-token`, admin `dev-admin-token`. Seed data is synthetic: parent `parent_001` (Sita,
Kathmandu), child `child_001` (Aarav), responders `responder_001` (Asha, neighbor) and
`responder_002` (Ram, family friend).

## Verified facts about integrations (checked 2026-11-14 — do not "fix" these from memory)

**Photon 2.0 = Beta docs only** (`https://photon.codes/docs/beta/...`). Don't mix with Stable
(`spectrum-ts`, Basic auth).
- Python SDK `photonhq-api==0.6.0` (import `photon_api`), needs Python ≥ 3.11. API base
  `https://api.photon.codes/v1`, Bearer project key `pho_sk_…`, project id `pho_prj_…`.
  `Photon()` has **no** `api_key` kwarg — pass `headers={"Authorization": f"Bearer {key}"}`.
- **Shared iMessage line rule:** can only send in a direct Space to a person with an active
  Assignment **who has messaged the line since that Assignment**. Every human (parent, child,
  each responder) must be assigned and must text FarCare once before we can message them. We
  store their `space.id` + agent user id from that first message (= opt-in).
- Beta iMessage supports text + images only (no tapbacks/polls/effects/voice notes).
  **Group chats and calls need a dedicated iMessage line**, so FarCare coordinates people
  through separate 1:1 threads. Voice must run on a Photon **SMS** number. The beta is free.
  Track rules and judging map: [docs/photon-track.md](docs/photon-track.md).
- Inbound: webhook (Standard Webhooks signature; dedupe on message id) or polling
  `list_messages`. Outbound: `send_message` with an `idempotency-key`; 202 = accepted, not delivered.
- Full schema: `https://api.photon.codes/openapi.json`.

**Google Health API (Fitbit)** — the legacy Fitbit Web API was turned off **2026-11-02**. Use
`https://health.googleapis.com/v4/users/me/dataTypes/{type}/dataPoints`. Types: `steps`,
`sleep`, `heart-rate`, `daily-resting-heart-rate`. **No raw accelerometer/gyroscope or fall
events exist** — never present Fitbit as a fall-detection source.

**Gemini** — SDK `google-genai` 3.3 (`client.models.generate_content`, manual function
calling). 2.5 models are unavailable to new keys. Default `GEMINI_MODEL=gemini-3.5-flash-lite`
(stable); free-tier quotas are tiny, so the agent falls back to deterministic rules on any error.
Don't send `temperature` (deprecated for Gemini 3+).

## Key technical decisions

| Decision | Why |
| --- | --- |
| One FastAPI service + one Postgres DB (SQLite locally) | Smallest thing that works for 3 devs in 36 h; no microservices |
| Pydantic contracts → JSON Schema/OpenAPI → generated TS types | One source of truth; CI fails on drift |
| Safety + approvals + responder replies parsed **deterministically first** | The LLM can escalate but never suppress or decide an emergency |
| Explicit `console` or `photon` gateway | Development/CI can simulate delivery; live Photon failures never silently become success |
| DB-backed timeline + SSE that tails it | Real-time dashboard that survives restarts; no extra infra |
| Standalone worker with PostgreSQL mutex and durable queues | Timers, sensor sync, inbound messages and retries survive process restarts |
| Separate collector package and private workspace | Preserves the Mac Python environment and existing recordings while making collector code reviewable and testable |
| Synthetic IMU traces + threshold detector | Fitbit exposes no raw IMU; pipeline is real, data is labeled simulated |

## Current integration and deployment

- `main` is production. Merge PRs normally after CI; never reset or force-push a
  teammate's branch. The dark/mobile UI retains authenticated role screens.
- `/parent`, `/family`, `/respond`, `/login` and `/setup` use the real role-aware API.
  Older concept pages remain separate; they are not the production care workflow.
- `app.health.sources` registers a consenting real wearer's collector subject to
  a real parent profile. `sensor-health` serves current and stale readings with
  source timestamps; `app.worker` syncs daily trends and queues family check-in prompts.
- Photon SMS configuration is documented in `docs/photon-track.md`. Do not confuse
  an accepted request with verified delivery or enable a line without eligible
  senders/recipients. STOP cancels pending texts, START does not replay them.
- Existing databases receive additive schema upgrades at startup. No upgrade
  infers consent, maps real readings to demo people, or deletes existing rows.
- Cloudflare uses Workers + Containers, not static Pages. The account needs paid
  Containers access. A successful image build alone does not prove the app is live.
- Readiness checks compare `/health.commit` with the deployed Git SHA and check
  the database and standalone worker. Keep `GIT_COMMIT` set by the deployment.

## Non-negotiable product rules

- Not a medical device. Never claim diagnosis, prediction of heart attacks/strokes, guaranteed
  fall detection, or replacing doctors/emergency services. Detector scores are **not**
  clinical probabilities.
- Never auto-contact real emergency services. Alerts tell humans to call local services.
- Contact only authorized Care Circle members; ordinary requests need the parent's YES.
- Children see health/check-ins only with the parent's sharing permission (enforced server-side).
- Simulated data is always labeled `simulated`. Use synthetic people and consenting testers.
- No secrets, real phone numbers, or personal data in git (repo is **public**).
