# FarCare

**Far away. Always there.** ❤️

FarCare is an AI care-coordination platform for aging parents whose children live far away.
Parents can use a simple check-in screen or message FarCare through a configured Photon
channel. Family members see consented health trends; trusted nearby helpers see only their
assigned tasks. Gemini supports conversation. Deterministic code handles consent, safety
checks, and escalation. A supervised worker processes messages and retries across restarts.

## For judges

**Live app:** https://collector-bkxg.vercel.app (sign-in details are in our Devpost submission;
there is no public sign-up). Healthcare track · Photon · Gemini · Tiger Data.

**In one minute:** a parent texts FarCare or taps *I'm OK* / *Ask for help* / *I need urgent
help*. FarCare asks the parent's permission, then asks an authorized nearby helper. When the
helper replies YES, the parent and the far-away child are told, and the dashboard updates.
If a check-in goes unanswered, a fixed rule (not the AI) escalates to the Care Circle and family.

**Try it:**

1. **Family view, Today:** status at a glance, *Last 5 days vs usual*, the watch's heart rate,
   the latest check-in, and which helpers are ready.
2. **Ask in the app chat** (or by text where Photon is enabled): "How is Mom doing?",
   "what's her bpm?", "how did she sleep?". Gemini calls our tools and answers with the real reading and how long ago it was
   taken. It never calls a value normal or abnormal.
3. **Health → Heart-rate alerts:** for example, *above 120 bpm for 2 minutes*. It fires only if
   every reading in that window is past the limit with no gaps, so one spike never triggers.
   Family who can see health get a text. Optionally the parent is asked "Are you okay?", and
   nearby helpers are asked only if there's no reply. Helpers never see health numbers.
4. **Parent view:** *Ask for help* → approve → a helper accepts → the family is notified.

**How we use each technology:**

| Tech | What it does in FarCare |
| --- | --- |
| **Photon 2.0** | 1:1 iMessage/SMS threads for parent, child and each helper; opt-in by first text; STOP/START/HELP; idempotent sends; durable retries |
| **Gemini** | Function-calling agents for parent, family and helper. Tools act only for the signed-in sender. A rule-based fallback keeps working when the free quota runs out |
| **Tiger Data** | TimescaleDB hypertable of watch readings with full revision history, a continuous 1-minute heart-rate aggregate, and a separate **read-only** app connection |
| **Google Health API** | Fitbit steps, sleep, resting heart rate, blood oxygen, HRV and breathing rate, with original timestamps |

**Honest limits:** not a medical device; FarCare never contacts emergency services itself; it
tells people to. Demo people are synthetic, and simulated readings are always labeled
`simulated`. Fitbit exposes no raw motion data, so fall detection runs on labeled synthetic
traces.

The code is organized into two main folders:

```text
frontend/                 Screens, components, styling and browser API calls
backend/
  app/                    API routes, accounts, health data, care workflows and messaging
  collector/              Mac Fitbit reader, Google imports and Tiger uploads
  tests/                  API and workflow tests
packages/contracts/       Shared API definitions used by both sides
```

Start with [frontend/README.md](frontend/README.md) or
[backend/README.md](backend/README.md). The collector has its own dependencies and
continues to run near the watch; the server and dashboard can be deployed together.

> A notification tells you something may be wrong. FarCare helps connect your parent with
> someone who can actually be there.

Built at HackPrinceton Fall 2026. **Prepared for an invited pilot; public production launch
requires the deployment and validation steps below.** This is not a medical device or an
emergency response service. The default
demo uses synthetic people and health readings labeled `simulated`. The authenticated
Tiger adapter reads live Fitbit heart rate and Google Health reports with original
timestamps, missing values, and family sharing permissions. The browser refreshes
watch readings every two seconds while visible. The durable worker projects daily
steps, reported resting heart rate and main sleep every minute for linked profiles.

## Quick start

Run each block in a separate terminal, starting at the repository root. Requires
Python 3.12, uv, and Node 22. Existing private environment files are preserved.

```bash
# Terminal 1: API and simulated messages; no external service keys required.
cd backend
test -f .env || cp .env.example .env
uv sync --frozen
MESSAGING_CHANNEL=console PHOTON_DELIVERY=off PROACTIVE_CHECKINS_ENABLED=false uv run uvicorn app.main:app --host 127.0.0.1 --reload --timeout-graceful-shutdown 2
```

```bash
# Terminal 2: actual dashboard connected to the API above.
cd frontend
test -f .env.local || cp .env.example .env.local
npm ci
NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open **http://localhost:3000/login** and choose **Parent**, **Family**, or **Helper** to
explore separate demo accounts. Use separate browser profiles for simultaneous roles.
The real routes are `/parent`, `/family`, and `/respond`; `/setup` creates private
invitations. No browser bundle contains a shared family access token.

Open **http://localhost:8000/console**
for the workflow console, or **http://localhost:8000/docs** for API documentation.
Fonts are included locally, so startup does not need Google Fonts. If the API's
private `.env` already selects Tiger, it requires network access to that database.

The child's status question works without a parent message first. Requests for a
neighbor's help still follow the parent-approval workflow below.

For individual accounts, production deployment, and device mapping, follow
[the invited-pilot setup](docs/pilot-setup.md) and [the worker runbook](docs/production-runbook.md).
For one Vercel project containing the dashboard, Tiger API and care worker, use
[the Vercel deployment guide](docs/vercel-deployment.md). Import the **repository
root**, not `backend/collector` or `frontend`; the root `vercel.json` defines both web
services and a quota-limited background daemon. The BLE collector stays on the Mac.
**Vercel is the active deployment platform.** The frontend, API and care worker use
the root configuration; Tiger remains the database. The previous Cloudflare
deployment has been retired. Existing Render templates are retained only as legacy
reference and do not publish the app.
Blood pressure comes from reported cuff readings. [The health evidence and rules](docs/health-evidence.md)
separate clinical guidance from the app's unvalidated seven-qualified-day learning heuristic.
“This is usual for me” is a provisional reference and never disables urgent guidance.

Try the whole Care Circle flow without phones:

```bash
A='Authorization: Bearer dev-admin-token'; J='content-type: application/json'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"parent_001","text":"Can someone help me with groceries tomorrow?"}'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"parent_001","text":"Yes"}'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"responder_001","text":"Yes, I can help at 4 PM"}'
curl -s -XPOST localhost:8000/api/demo/fall -H "$A" -H "$J" -d '{"parent_id":"parent_001","trace":"fall"}'
```

## Docs

Start reading the code in this order:

| Part | Entry points | Purpose |
| --- | --- | --- |
| Dashboard | `frontend/app/(app)/`, `frontend/lib/dashboard.tsx` | Family screens, loading, and live timeline updates |
| API connection | `frontend/lib/api.ts`, `backend/app/main.py` | Typed browser requests and API startup |
| Sensor collection | `backend/collector/farcare_collector/` | BLE recording, Google exports, normalization, durable outbox and Tiger delivery |
| Health data | `backend/app/health/tiger.py`, `sync_tiger.py`, `trends.py` | Tiger daily records, authenticated ingestion, and personal comparisons |
| Live watch data | `backend/app/health/live.py`, `sources.py`, `live_sync.py` | Authorized watch mapping, bounded live queries, and daily synchronization |
| Care workflows | `backend/app/services/family.py`, `care_requests.py`, `safety.py` | Family questions, parent approval, neighbor responses, and safety checks |
| Storage and access | `backend/app/models.py`, `auth.py`, `schemas/contracts.py` | Database tables, sharing permissions, and shared payload definitions |
| Integrations and automation | `backend/app/integrations/`, `.github/workflows/` | Photon/Gemini adapters and CI/deployment verification |

`frontend/app/(web)/` contains older concept screens; the connected family app is
under `(app)`. Collector source lives in `backend/collector`; its private recordings,
OAuth tokens and database state remain in the local data workspace. The app consumes
their Tiger projection through the health adapter. [The codebase guide](docs/codebase-organization.md)
maps every former root Python file to its new home and explains collector setup,
local launchers, research boundaries and tests. Existing application routes and
integrations are unchanged by this cleanup.

`main` is the production branch. Feature work goes through a PR and the full CI
suite before an ordinary merge commit; this preserves the team's branch history.
The deployment configurations track `main`. `scripts/sync-after-merge.sh` safely merges
main back into a feature branch without force-pushing or resetting its history.

For real texts, use `MESSAGING_CHANNEL=photon`, `PHOTON_PLATFORM=sms`, a provisioned
`PHOTON_AGENT_USER_ID`, and the provider secrets described in
[the Photon setup guide](docs/photon-track.md). Registered participants first text
the configured number. Replies, alerts, STOP/START/HELP and signed delivery receipts
pass through the same durable worker. A daily change can prompt an authorized
family member to check in; a single pulse value never triggers a medical diagnosis.
Console mode and synthetic demo accounts do not establish real SMS delivery.

- [CLAUDE.md](CLAUDE.md): project context, verified integration facts, key decisions
- [AGENTS.md](AGENTS.md): collaboration and engineering rules
- [Codebase organization](docs/codebase-organization.md): file map, collector commands, private data and research
- [Vercel deployment](docs/vercel-deployment.md): one project, authenticated Tiger API, care daemon and production settings
- [Native Render deployment](docs/render-deployment.md): build settings, Tiger connection, accounts and optional real SMS
- [CI and browser verification](docs/local-ci.md): checks, screenshots, and deployment gates
- [Tiger health integration](docs/tiger-health-pipeline.md): real data mapping and limitations
- [docs/architecture.md](docs/architecture.md) · [docs/api-contracts.md](docs/api-contracts.md) · [docs/demo-plan.md](docs/demo-plan.md)
- Team: [Sumit](docs/agents/SUMIT.md) · [Saksham](docs/agents/SAKSHAM.md) · [Sabal](docs/agents/SABAL.md)

## Stack

FastAPI · SQLModel · PostgreSQL · Photon 2.0 (SMS / iMessage) · Google Gemini · Google Health API
(Fitbit) · Next.js 16 · Tailwind CSS 4 · GitHub Actions
