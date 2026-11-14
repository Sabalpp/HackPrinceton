# FarCare

**Far away. Always there.** ❤️

FarCare is an AI care-coordination platform for aging parents whose children live far away.
Parents text FarCare over iMessage. Gemini handles the conversation. When the parent needs help,
FarCare asks their pre-approved nearby Care Circle (a neighbor, a family friend) and keeps the
distant child informed in real time. Possible-fall events start a deterministic safety check
and escalation.

> A notification tells you something may be wrong. FarCare helps connect your parent with
> someone who can actually be there.

Built at HackPrinceton Fall 2026. **Hackathon prototype: not a medical device.** The default
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

Open **http://localhost:3000** for the dashboard, **http://localhost:8000/console**
for the workflow console, or **http://localhost:8000/docs** for API documentation.
Fonts are included locally, so startup does not need Google Fonts. If the API's
private `.env` already selects Tiger, it requires network access to that database.

The child's status question works without a parent message first. Requests for a
neighbor's help still follow the parent-approval workflow below.

Try the whole Care Circle flow without phones:

```bash
A='Authorization: Bearer dev-admin-token'; J='content-type: application/json'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"parent_001","text":"Can someone help me with groceries tomorrow?"}'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"parent_001","text":"Yes"}'
curl -s -XPOST localhost:8000/api/demo/inbound -H "$A" -H "$J" -d '{"person_id":"responder_001","text":"Yes, I can help at 4 PM"}'
curl -s -XPOST localhost:8000/api/demo/fall -H "$A" -H "$J" -d '{"parent_id":"parent_001","trace":"fall"}'
```

## Docs

- [CLAUDE.md](CLAUDE.md): project context, verified integration facts, key decisions
- [AGENTS.md](AGENTS.md): collaboration and engineering rules
- [CI and browser verification](docs/local-ci.md): checks, screenshots, and deployment gates
- [Tiger health integration](docs/tiger-health-pipeline.md): real data mapping and limitations
- [docs/architecture.md](docs/architecture.md) · [docs/api-contracts.md](docs/api-contracts.md) · [docs/demo-plan.md](docs/demo-plan.md)
- Team: [Sumit](docs/agents/SUMIT.md) · [Saksham](docs/agents/SAKSHAM.md) · [Sabal](docs/agents/SABAL.md)

## Stack

FastAPI · SQLModel · PostgreSQL · Photon 2.0 (iMessage) · Google Gemini · Google Health API
(Fitbit) · Next.js 16 · Tailwind CSS 4 · GitHub Actions
