# FarCare architecture

## Feasibility verdict

The proposed scope is feasible for three people in 36 hours **if** it is one backend, one
frontend, and one database, with every external integration behind a fallback. The riskiest
dependencies are external (Photon line assignments, Google OAuth, Gemini quota), not code. Each
one has a working fallback so the core workflow never blocks on them:

| Dependency | Risk | Fallback (already implemented) |
| --- | --- | --- |
| Photon 2.0 Beta | Shared-line rules, plan seats, Beta feature gaps | `console` messaging channel + `/api/demo/inbound` |
| Gemini | Free-tier quota (~20 req/day on some models), model churn | Deterministic intent fallback; same tools, same flow |
| Google Health API | OAuth consent/verification, new API (legacy shut off 2026-11-02) | Labeled simulated 35-day dataset |
| Fall detection | No raw IMU from Fitbit | Labeled synthetic traces + explainable detector |
| Hosting | Webhooks need public HTTPS; free tiers sleep | Polling mode (`PHOTON_DELIVERY=poll`) needs no public URL |

## System diagram

```text
 Parent (iMessage)   Child (iMessage)   Responders (iMessage)
        \                  |                  /
         \                 |                 /
          +------- Photon 2.0 (Beta) -------+        Google Health API (Fitbit)
                       |        ^                          |
        webhook (signed)|        | send_message             | Sabal's sync job (or simulated)
          or polling    v        |                          v
 +---------------------------------------------------------------------------+
 | FastAPI backend (single instance)                                          |
 |                                                                             |
 |  routes/webhooks ─► conversation router ─► safety (deterministic)           |
 |                         │                ├► care_requests (state machine)   |
 |                         │                └► gemini agent (tools) ─ fallback |
 |  routes/ingest ─► safety.ingest_fall_event / health.ingest                  |
 |  messaging gateway (photon | console, idempotent outbox)                    |
 |  background tick: safety-check timeouts, responder timeouts, retries        |
 |  timeline (append-only) ─► routes/parents (REST + SSE stream)               |
 +---------------------------------------------------------------------------+
                       |                                   ^
                PostgreSQL (prod, e.g. Tiger Data)         | REST + SSE (bearer token)
                SQLite (dev/tests)                          |
                                                   Next.js dashboard (Vercel)
 Fall detector (edge / replay script) ── POST /api/events/fall (ingest key) ──^
```

## Components

| Module | Responsibility |
| --- | --- |
| `schemas/contracts.py` | Shared payloads; exported to `packages/contracts` and TS types |
| `services/conversation.py` | Routes inbound texts by sender role; deterministic checks before the LLM |
| `services/care_requests.py` | Request lifecycle, responder selection, authorization, messages |
| `services/safety.py` | Incident state machine, dedupe, escalation, stand-down |
| `services/replies.py` | Deterministic parsing: YES/NO, OK/HELP, emergency phrases, ETA |
| `integrations/gemini/agent.py` | Companion conversation + tool calling; fallback rules |
| `integrations/messaging.py` | Single outbound path; idempotency; photon vs console |
| `integrations/photon/` | SDK wrapper, webhook verification, payload normalization |
| `health/` (Sabal) | Simulated + Google Health providers, baseline/trend detection |
| `fall_detection/` | Synthetic traces, threshold detector |
| `routes/parents.py` | Dashboard API, permissions, SSE stream |

## Data model (tables)

`people` (role: parent/child/responder/admin; Photon routing learned from first message) ·
`family_links` (child↔parent + sharing permissions) · `care_circle` (responder↔parent,
categories, emergency flag, priority) · `care_requests` · `safety_incidents` · `fall_events`
(dedupe by event_id) · `health_observations` · `checkins` · `timeline_events` (append-only,
drives SSE) · `conversation_turns` (agent memory) · `inbound_messages` (queue + dedupe by Photon
message id) · `outbound_messages` (idempotent outbox).

## Workflows

### Care request

```text
created ─► awaiting_approval ─(parent YES)─► approved ─► sent ─(responder YES)─► accepted ─(DONE)─► completed
   │              │ (NO)                                  │  (NO / timeout)
   └► failed      └► cancelled / expired                   └► declined / expired ─► next authorized
                                                              responder proposed to parent (or failed)
```

Responder selection: active Care Circle members authorized for the category, reachable (opted
in on Photon), not yet asked, ordered by priority.

### Safety incident (no LLM anywhere in this path)

```text
fall.suspected ─► checking (safety check sent; deadline SAFETY_CHECK_TIMEOUT_SECONDS)
   parent OK ─────────────────────────────► resolved (parent_ok; family FYI)
   parent HELP / timeout / emergency text ─► escalated
       ├ family alerted (+ local emergency number guidance)
       ├ emergency-authorized responder asked (pre-approved by policy)
       └ responder YES ─► responder_en_route ─ DONE ─► resolved (responder_confirmed)
         responder NO / timeout ─► next responder ─ none left ─► family told to call services
 Duplicate event_id → idempotent. New events while an incident is open (10 min) → merged, no new alerts.
 Safety check undeliverable → escalate immediately.
```

## Real-time updates

Every user-visible state change writes a `timeline_events` row with a visibility class
(`family`, `health`, `checkin`, `parent_only`). `GET /api/parents/{id}/stream` is an SSE
stream that tails that table (1 s poll), filtered by the viewer's permissions. Reconnects resume
via `Last-Event-ID`. No broker needed, and nothing is lost across restarts.

## Auth and privacy

- Dashboard users: bearer tokens (hashed in DB). `resolve_parent_access` maps viewer → parent via
  `family_links` and applies `share_health` / `share_checkins`. Unlinked parents return 404.
- Machine ingestion: `X-FarCare-Ingest-Key`. Production refuses to start without it.
- Photon webhook: Standard Webhooks HMAC over the raw body; project id checked.
- Demo routes require the admin token **and** `DEMO_MODE=true`.
- Production refuses default `dev-*` tokens.

## Deployment

| Piece | Recommended host | Notes |
| --- | --- | --- |
| Web | Vercel (Git integration, root `apps/web`) | PR previews come free; set `NEXT_PUBLIC_API_URL` |
| API | Any container host that auto-deploys `main` from `apps/api/Dockerfile` (Render, Railway, Fly, or a Vultr VM) | **Exactly one instance.** Avoid sleeping free tiers during the demo. Set `GIT_COMMIT` if the host doesn't. |
| DB | Managed Postgres (Tiger Data / Timescale Cloud fits the Tiger prize) | `DATABASE_URL`; tables auto-created on boot |

CI/CD:

- `ci.yml` runs on each PR. Checks: API lint/format/syntax/tests/contract drift, Docker image
  build + boot, web types-current/lint/typecheck/build, gitleaks secret scan.
- `auto-merge.yml` enables native squash auto-merge on approved, non-draft, same-repo PRs. GitHub
  waits for the ruleset's required checks and approval.
- `deploy.yml` runs on `main`: re-runs CI, triggers the optional backend deploy hook, waits until
  `/health.commit == sha`, then runs `scripts/smoke_test.sh` and a web check.
- The `main` ruleset (`scripts/setup-github.sh`, run once by the repo admin) requires a PR, 1
  approval, code-owner review for `.github/` and contracts, the 4 checks, and linear history. No
  force-push, no bypass actors.
- **GitHub gotcha:** merges performed with the default `GITHUB_TOKEN` don't trigger other
  workflows, so Deploy wouldn't start. Add an `AUTOMERGE_TOKEN` secret (fine-grained PAT,
  this repo only, contents + pull requests write), or dispatch Deploy manually. The hosts' Git
  integrations deploy regardless.

## Known limitations (be honest in the demo)

- Fall detection uses synthetic traces and a heuristic detector. It is not validated.
- Gemini fallback mode is keyword-based and English plus a few romanized Nepali words.
- Single backend instance (in-process tick). Fine for the hackathon, not for production.
- Photon Beta: text and images only on iMessage. Each participant must be assigned and must
  text first.
