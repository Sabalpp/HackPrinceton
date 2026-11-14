# FarCare

**Far away. Always there.** ❤️

FarCare is an AI care-coordination platform for aging parents whose children live far away.
Parents can use a simple check-in screen or message FarCare through a configured Photon
channel. Family members see consented health trends; trusted nearby helpers see only their
assigned tasks. Gemini supports conversation. Deterministic code handles consent, safety
checks, and escalation. A supervised worker processes messages and retries across restarts.

> A notification tells you something may be wrong. FarCare helps connect your parent with
> someone who can actually be there.

Built at HackPrinceton Fall 2026. **Prepared for an invited pilot; public production launch
requires the deployment and validation steps below.** This is not a medical device or an
emergency response service. The default
demo uses synthetic people and health readings labeled `simulated`. The authenticated
Tiger adapter also supports real Fitbit steps, reported resting heart rate, and main
sleep with original dates, missing values, and family sharing permissions.

## Quick start

Run each block in a separate terminal, starting at the repository root. Requires
Python 3.12, uv, and Node 22. Existing private environment files are preserved.

```bash
# Terminal 1: API and simulated messages; no external service keys required.
cd apps/api
test -f .env || cp .env.example .env
uv sync --frozen
MESSAGING_CHANNEL=console PHOTON_DELIVERY=off PROACTIVE_CHECKINS_ENABLED=false uv run uvicorn app.main:app --host 127.0.0.1 --reload --timeout-graceful-shutdown 2
```

```bash
# Terminal 2: actual dashboard connected to the API above.
cd apps/web
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
For Cloudflare, use [the Workers + Containers settings](docs/cloudflare-deployment.md).
The app needs a running Next.js server and Python backend; it has no static Pages
output directory. The deployment guide explains how to replace a failing
`pages.dev` setup with the complete app.
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
| Dashboard | `apps/web/app/(app)/`, `apps/web/lib/dashboard.tsx` | Family screens, loading, and live timeline updates |
| API connection | `apps/web/lib/api.ts`, `apps/api/app/main.py` | Typed browser requests and API startup |
| Health data | `apps/api/app/health/tiger.py`, `sync_tiger.py`, `trends.py` | Tiger daily records, authenticated ingestion, and personal comparisons |
| Care workflows | `apps/api/app/services/family.py`, `care_requests.py`, `safety.py` | Family questions, parent approval, neighbor responses, and safety checks |
| Storage and access | `apps/api/app/models.py`, `auth.py`, `schemas/contracts.py` | Database tables, sharing permissions, and shared payload definitions |
| Integrations and automation | `apps/api/app/integrations/`, `.github/workflows/` | Photon/Gemini adapters and CI/deployment verification |

`apps/web/app/(web)/` contains older concept screens; the connected family app is
under `(app)`. The sensor collector and private recorded exports can run outside
this repository; the app consumes their Tiger projection through the health adapter.

- [CLAUDE.md](CLAUDE.md): project context, verified integration facts, key decisions
- [AGENTS.md](AGENTS.md): collaboration and engineering rules
- [CI and browser verification](docs/local-ci.md): checks, screenshots, and deployment gates
- [Tiger health integration](docs/tiger-health-pipeline.md): real data mapping and limitations
- [docs/architecture.md](docs/architecture.md) · [docs/api-contracts.md](docs/api-contracts.md) · [docs/demo-plan.md](docs/demo-plan.md)
- Team: [Sumit](docs/agents/SUMIT.md) · [Saksham](docs/agents/SAKSHAM.md) · [Sabal](docs/agents/SABAL.md)

## Stack

FastAPI · SQLModel · PostgreSQL · Photon 2.0 (iMessage) · Google Gemini · Google Health API
(Fitbit) · Next.js 16 · Tailwind CSS 4 · GitHub Actions
