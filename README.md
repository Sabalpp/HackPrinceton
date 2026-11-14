# FarCare

**Far away. Always there.** ❤️

FarCare is an AI care-coordination platform for aging parents whose children live far away.
Parents text FarCare over iMessage. Gemini handles the conversation. When the parent needs help,
FarCare asks their pre-approved nearby Care Circle (a neighbor, a family friend) and keeps the
distant child informed in real time. Possible-fall events start a deterministic safety check
and escalation.

> A notification tells you something may be wrong. FarCare helps connect your parent with
> someone who can actually be there.

Built at HackPrinceton Fall 2026. **Hackathon prototype: not a medical device.** Demo data is
synthetic and labeled `simulated`.

## Quick start

```bash
# API: http://localhost:8000/docs (works with no keys: simulated messaging + AI fallback)
cd apps/api && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload --timeout-graceful-shutdown 2
# Then open http://localhost:8000/console to watch and drive the whole system

# Web: http://localhost:3000
cd apps/web && cp .env.example .env.local && npm install && npm run dev
```

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
- [docs/architecture.md](docs/architecture.md) · [docs/api-contracts.md](docs/api-contracts.md) · [docs/demo-plan.md](docs/demo-plan.md)
- Team: [Sumit](docs/agents/SUMIT.md) · [Saksham](docs/agents/SAKSHAM.md) · [Sabal](docs/agents/SABAL.md)

## Stack

FastAPI · SQLModel · PostgreSQL · Photon 2.0 (iMessage) · Google Gemini · Google Health API
(Fitbit) · Next.js 16 · Tailwind CSS 4 · GitHub Actions
