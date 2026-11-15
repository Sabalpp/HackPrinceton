# Sumit — architecture, backend, AI, Photon, fall detection, CI/CD, integration

**Branch:** `feature/sumit-core` · **Owns:** `backend/**` (except `app/health/`), `.github/**`,
`scripts/**`, contract coordination.

## Done

- Foundation on `main`: FastAPI backend with contracts, auth/permissions, care-request and
  safety state machines, Photon 2.0 client + verified webhook + polling, Gemini agent with
  tools + deterministic fallback, SSE timeline, synthetic fall traces + detector, demo routes,
  seed data. CI green on GitHub (incl. Docker image build + boot, secret scan).
- `feature/sumit-core`:
  - `scripts/photon_setup.py` (status / assign lines / create webhook)
  - `scripts/check_gemini.py` (finds a model that completes a real tool call)
  - Photon typing indicator while the agent thinks
  - Test suite runs on SQLite **and Postgres 16**, in CI and locally via `TEST_DATABASE_URL`
  - From the Photon judging criteria ([docs/photon-track.md](../photon-track.md)): proactive
    once-a-day check-in, son-initiated help with Mom's consent, and a "Mom isn't answering"
    safety check

## Go-live checklist (needs your accounts; ~30 min)

1. **Photon:** create a project in the Beta dashboard (`app.beta.photon.codes`) and a server key
   with `project:read`, `project:write`, `platforms:write`. Put `PHOTON_API_KEY` and
   `PHOTON_PROJECT_ID` in `backend/.env`. Put your real iMessage handle in
   `DEMO_PARENT_HANDLE` to start.
2. `uv run python scripts/photon_setup.py assign`, then `... status`. Text the assigned number
   "hi" from your phone.
3. Run locally without a public URL: `PHOTON_DELIVERY=poll uv run uvicorn app.main:app`. You
   should get a reply within ~3 s. `/health` shows `messaging: photon`.
4. **Gemini:** put `GEMINI_API_KEY` in `.env`, run `uv run python scripts/check_gemini.py`, and
   set the `GEMINI_MODEL` it prints. Text "Can someone help me with groceries tomorrow?" and
   check that you get the YES/NO approval question.
5. Add teammates' handles (`DEMO_CHILD_HANDLE`, `DEMO_RESPONDER_HANDLE`), `assign` again, and
   have each of them text their number once.

## Next (in order)

1. **Deploy the API** (pick a host: Render / Railway / Fly / Vultr VM). Use
   `backend/Dockerfile`, a single instance, Postgres `DATABASE_URL` (Tiger Data fits the
   prize), `APP_ENV=production`, real `DEMO_*_TOKEN`s, `INGEST_API_KEY`, and
   `CORS_ORIGINS=<vercel url>`. Then run
   `scripts/photon_setup.py webhook --url https://<api>/webhooks/photon`, set
   `PHOTON_WEBHOOK_SECRET` and `PHOTON_DELIVERY=webhook`. Set repo variables
   `API_BASE_URL` / `WEB_BASE_URL` so Deploy smoke-tests production.
2. **Prompt tuning** with real conversations: keep SMS-short, natural, one question at a time.
   Watch `agent.replied` timeline events for `ai_mode`.
3. **Full rehearsal** of `docs/demo-plan.md` with real phones; fix message copy.
4. **Review PRs** from Saksham and Sabal, especially anything touching contracts.
5. **Fall detection research (optional):** `research/fall-detection/` evaluating
   `detector.classify` on SisFall/KFall windows (`[[ax, ay, az], …]` @ 50 Hz). Keep datasets
   out of git and respect their licenses.
6. **Voice (optional, Photon Best Voice Agent):** Photon number → ElevenLabs/Retell over SIP
   (`photon.codes/docs/beta/get-started/voice`). Only after 1–3 are solid.

## Acceptance criteria for your area

- [ ] A real iMessage from the parent produces a Gemini reply via Photon (live channel).
- [ ] Parent YES → responder gets a real message → responder YES → parent + child get real messages.
- [ ] Simulated fall → safety check → timeout → child + responder alerted; no duplicate alerts.
- [ ] Deployed API passes `scripts/smoke_test.sh`; `/health` shows `messaging: photon`.
