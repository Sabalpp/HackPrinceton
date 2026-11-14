# Sumit — architecture, backend, AI, Photon, fall detection, CI/CD, integration

**Branch:** `feature/sumit-core` · **Owns:** `apps/api/**` (except `app/health/`), `.github/**`,
`scripts/**`, contract coordination.

## Already done (foundation commit)

- FastAPI backend with contracts, auth/permissions, care-request and safety state machines,
  Photon 2.0 client + verified webhook + polling, Gemini agent with tools + deterministic
  fallback, SSE timeline, synthetic fall traces + detector, demo routes, seed data.
- 101 backend tests incl. full care and safety workflows, webhook signature/dedupe, auth/privacy.
- Contract export → `packages/contracts`, TS type generation for web, CI drift checks.
- CI (`ci.yml`), auto-merge, deploy verification, smoke test, Dockerfile, ruleset script.

## Priorities (in order)

1. **Push foundation + repo setup.** Push `main`. Ask Sabal (repo admin) to run
   `scripts/setup-github.sh`, enable Vercel Git integration (root `apps/web`), and optionally
   add `AUTOMERGE_TOKEN`.
2. **Photon live (Priority 2 of the project).** Create the Photon Beta project + key, and assign
   shared lines for your own phone first. Run locally with `PHOTON_DELIVERY=poll` and text
   "hi" → confirm a real reply. Then deploy the API (one instance, Postgres `DATABASE_URL`), add
   the webhook destination, and switch to `webhook`. Verify `message.received` arrives and that
   dedupe works on redelivery (Photon dashboard → Event Deliveries).
3. **Gemini live.** Set a key and try `gemini-3.5-flash-lite`, then `gemini-3.8-flash` if
   quota allows. Tune `SYSTEM_PROMPT` in `integrations/gemini/agent.py` with real conversations.
   Keep replies SMS-short. Check that tool calls land (timeline `agent.replied` has
   `ai_mode: gemini`).
4. **End-to-end rehearsal** of `docs/demo-plan.md` with real phones; fix rough edges in message
   copy (`services/care_requests.py`, `services/safety.py`).
5. **Fall detection research (optional, after the demo flow is solid):** add
   `research/fall-detection/` with a notebook or script that loads SisFall/KFall windows,
   converts them to the `[[ax, ay, az], …]` @ 50 Hz format, and evaluates `detector.classify`
   (confusion matrix). An optional scikit-learn model must output the same `Detection` shape.
   Keep datasets out of git and respect their licenses.
6. **Voice (optional, Photon Best Voice Agent):** Photon Beta voice = Photon number → ElevenLabs
   (or Retell) over SIP (`photon.codes/docs/beta/get-started/voice`). Only after 1–4 are stable.

## Integration rules you enforce

- Review every PR touching `contracts.py`, `.github/`, or cross-module code.
- Keep `main` deployable; the Deploy workflow's smoke test must stay green.
- Saksham's UI depends on `ParentOverview`, `TimelineEvent`, and SSE. Make changes additive.

## Acceptance criteria for your area

- [ ] A real iMessage from the parent produces a Gemini reply via Photon (live channel).
- [ ] Parent YES → responder gets a real message → responder YES → parent + child get real messages.
- [ ] Simulated fall → safety check → timeout → child + responder alerted; no duplicate alerts on
      redelivery or repeated events.
- [ ] Deployed API passes `scripts/smoke_test.sh`; `/health` shows `messaging: photon`.
