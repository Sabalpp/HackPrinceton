# Saksham — UI/UX and frontend

**Branch:** `feature/saksham-ui` · **Owns:** `frontend/**`

Stack: Next.js 16 (App Router) + TypeScript + Tailwind CSS 4. **Read
`frontend/AGENTS.md`:** Next 16 has breaking changes vs. older versions. Check
`frontend/node_modules/next/dist/docs/` before using an API from memory. `cacheComponents` is
enabled in `next.config.ts`.

## Start here (no backend needed)

```bash
cd frontend && cp .env.example .env.local     # set NEXT_PUBLIC_USE_MOCKS=true to start
npm install && npm run dev
```

- Types: `import type { ParentOverview, TimelineEvent, ... } from "@/lib/api"`. These come from
  `lib/api-types.ts`, which is generated. **Never hand-edit it or redefine these shapes.**
- Data: `api.overview(token, "parent_001")`, `api.timeline(...)`, etc. in `lib/api.ts`.
  With mocks on, these return `lib/mocks.ts`. That file is type-checked against the contracts.
- Live updates: `subscribeTimeline(token, parentId, onEvent)`. On each event, show the
  `summary` in the activity feed and refetch the overview (simplest correct approach).
- Real backend: run `backend` (see CLAUDE.md), set `NEXT_PUBLIC_USE_MOCKS=false`, and use token
  `dev-child-token`.

**Reference:** the backend serves a plain Demo Console at `http://localhost:8000/console`
(`backend/app/static/console.html`). It's a dev tool, not the product UI, but it shows every
API call your dashboard needs (overview, timeline + SSE, incidents, requests, health) working
live. Borrow freely.

## Screens (priority order)

1. **Parent Overview** (single page is fine): from `GET /overview`
   - Header: parent name, local time (`parent.timezone`), connection status.
   - **Active safety incidents** at the top when present: status chip
     (`checking`/`escalated`/`responder_en_route`/`resolved`), escalation reason, responder,
     "Mark resolved" button (`api.resolveIncident`), and the emergency-number guidance.
   - Recent care requests with a status stepper:
     `awaiting_approval → sent → accepted → completed`, plus declined/expired/failed states.
     Show `responder_name`, `requested_time`, `responder_note` / ETA.
   - Latest check-in (only if present; it's `null` when the parent didn't share).
   - Care Circle list: name, relationship, categories, emergency badge, "reachable" indicator.
2. **Health timeline**: `health.observations` (daily steps, sleep, RHR) as charts (Recharts is
   fine, add it as a dependency), with a baseline line from `trends[].baseline`, the
   `observation` sentences, and **always** the `disclaimer` and a visible **"Simulated data"**
   badge when `data_mode !== "live"`. Show an empty state when `health` is `null` ("Sita hasn't
   shared health data").
3. **Activity feed**: `TimelineEvent[]` + live SSE, newest first, icon by `kind` prefix
   (`care_request.*`, `incident.*`, `checkin.*`).
4. **Emergency center** view: incident list (`GET /incidents`) and the per-incident timeline
   (filter feed by `ref_id`).
5. **Demo control panel** (hidden route, e.g. `/demo`, admin token from an input field, never
   hardcoded): buttons for "Replay fall", "Replay walking", "Reset", and a "send as…" box that
   calls `api.demoInbound(personId, text)`. This is our backup if Photon fails on stage.

## UX requirements

- Calm, warm, trustworthy; large readable type; WCAG AA contrast; keyboard accessible; works at
  375 px mobile width.
- Loading, empty, and error states for every panel. The API can be down; show it gracefully.
- Plain language. No medical/diagnostic wording. Label simulated data everywhere.
- Don't display private conversation text. The API doesn't expose it, so don't add it.

## Acceptance criteria

- [ ] With mocks: all panels render, including empty/error states, on mobile and desktop.
- [ ] With the real backend: the demo script steps 3–9 update the dashboard live without refresh.
- [ ] `npm run lint && npm run typecheck && npm run build` pass. CI's "Web" check is green.
- [ ] Deployed on Vercel with `NEXT_PUBLIC_API_URL` pointing at the deployed API.
