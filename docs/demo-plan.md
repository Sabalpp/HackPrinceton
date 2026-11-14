# Demo plan — reproducible end-to-end

**Story:** Sita (mother) lives alone in Kathmandu. Her son Aarav studies in the US. Asha is her
neighbor; Ram is a family friend. All personas are synthetic. The phones belong to consenting
teammates.

**Closing line:** *"You may be thousands of miles away, but your care doesn't have to be."*

## Cast (4 iMessage identities, each must be DISTINCT)

| Role | Person id | Env var | Who holds the phone |
| --- | --- | --- | --- |
| Parent (Sita) | `parent_001` | `DEMO_PARENT_HANDLE` | teammate A |
| Child (Aarav) | `child_001` | `DEMO_CHILD_HANDLE` | teammate B (also drives the dashboard) |
| Neighbor (Asha) | `responder_001` | `DEMO_RESPONDER_HANDLE` | teammate C |
| Family friend (Ram) | `responder_002` | `DEMO_RESPONDER2_HANDLE` | optional 4th phone, or leave unassigned |

One person can cover two roles by using a phone number for one and an Apple-account email for
the other. Photon treats them as different senders.

## T-minus setup (do once, then re-check 1 hour before judging)

1. **Photon project** (Beta dashboard `app.beta.photon.codes`): create a server key with
   `project:read`, `project:write` (+ `platforms:write` to assign lines by API). Put
   `PHOTON_API_KEY` and `PHOTON_PROJECT_ID` in the backend env.
2. **Assign a shared iMessage line for each cast handle**: set the `DEMO_*_HANDLE` values, then
   `cd apps/api && uv run python scripts/photon_setup.py assign` (or use the dashboard: iMessage
   → "Add the number you or your user will send from"). Each assignment uses plan capacity, so
   confirm we have enough seats. `scripts/photon_setup.py status` shows who maps to which line.
3. **Every cast member texts their FarCare number once** (e.g. "JOIN"). This is required: a shared
   line can't message anyone who hasn't texted it since assignment. Check with
   `GET /api/demo/people` that all are `reachable: true`.
4. **Inbound delivery:** deployed backend → `uv run python scripts/photon_setup.py webhook --url
   https://<api>/webhooks/photon` (subscribes to `message.received` and prints the signing
   secret once), then set `PHOTON_WEBHOOK_SECRET`.
   Laptop-only backup → `PHOTON_DELIVERY=poll` (no public URL needed).
5. **Gemini:** set `GEMINI_API_KEY`, run `uv run python scripts/check_gemini.py`, and set the
   `GEMINI_MODEL` it recommends. `/health` shows `"ai": "gemini"`. If quota
   runs out mid-demo the fallback answers automatically, and the core flow is unchanged.
6. Set `DEMO_EMERGENCY_NUMBER` to the verified local emergency number for the story's location,
   and `SAFETY_CHECK_TIMEOUT_SECONDS=45` so the no-response path fits in the demo.
7. `POST /api/demo/reset` (admin token) for a clean timeline.
8. Dashboard open on the projector, logged in as the child, live stream connected.

## Script (target ≈ 4 minutes)

| # | Who | Action | What judges see |
| --- | --- | --- | --- |
| 1 | Sita | texts *"I haven't been feeling very energetic lately."* | Gemini replies warmly and asks about sleep. A check-in appears on Aarav's dashboard (shared with consent). |
| 2 | Aarav | points at Health panel | Steps ~50% below Sita's own baseline over 5 days. Labeled **simulated**. Non-diagnostic wording + disclaimer. |
| 3 | Sita | texts *"Can someone help me with groceries tomorrow?"* | FarCare: *"Would you like me to ask Asha (your neighbor)…? Reply YES or NO."* Dashboard: request `awaiting_approval`. Nobody contacted yet. |
| 4 | Sita | *"Yes"* | Asha's phone gets the request (name, task, time only). Dashboard: `sent`. |
| 5 | Asha | *"Yes, I can help at 4 PM"* | Sita: *"Good news…"*. Aarav's phone: *"Asha (neighbor) accepted Mom's request… at 4 PM"*. Dashboard: `accepted`, live. |
| 6 | Presenter | Demo control: replay **fall** trace (`POST /api/demo/fall`) | Detector: `possible_fall` (score ~0.87, simulated). Sita gets *"Are you okay? Reply OK or HELP"*. Incident `checking`. |
| 7 | Sita | stays silent | After the timeout: incident `escalated`. Aarav gets an URGENT alert with emergency-services guidance. Asha gets an urgent check-on request. |
| 8 | Asha | *"YES 5"* | Incident `responder_en_route`; Aarav informed. |
| 9 | Asha | *"Done, she's okay"* | Incident `resolved`. Dashboard shows the full incident timeline. |
| 10 | Presenter | closing line over the dashboard | — |

Optional extras if time allows: Asha replies *"Sorry, I can't"* in step 5 to show FarCare
proposing Ram. Or Sita replies *"OK"* in step 7 to show a false alarm resolving with an FYI to
Aarav.

## Backup plans

| Failure | Do this |
| --- | --- |
| Photon down / phone has no signal | Use the dashboard's demo controls (or `POST /api/demo/inbound`) to send the same lines as each person. The UI and state machine behave identically. Say so honestly. |
| Webhook not arriving | Switch backend to `PHOTON_DELIVERY=poll` and restart. |
| Gemini quota/latency | Nothing to do. Fallback replies automatically (`/health` → `ai: fallback` after restart without key). |
| Backend host asleep/crashed | Run locally: `uv run uvicorn app.main:app` + `PHOTON_DELIVERY=poll`. Point the web app at it. |
| Timeout too slow on stage | Restart with a smaller `SAFETY_CHECK_TIMEOUT_SECONDS`. |

## Command crib sheet

```bash
API=https://<api-host>; ADMIN="Authorization: Bearer $DEMO_ADMIN_TOKEN"
curl -s $API/health | jq
curl -s -XPOST $API/api/demo/reset -H "$ADMIN"
curl -s $API/api/demo/people -H "$ADMIN" | jq
curl -s -XPOST $API/api/demo/fall -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"parent_id":"parent_001","trace":"fall"}' | jq
curl -s -XPOST $API/api/demo/inbound -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"person_id":"responder_001","text":"YES 5"}' | jq
# Edge-device style replay (detector runs locally, only the event is sent):
cd apps/api && INGEST_API_KEY=... uv run python scripts/replay_fall.py --api $API --trace fall
```

## What to say about limitations (judges respect honesty)

Health data shown is simulated unless labeled live. The fall detector runs on synthetic
accelerometer traces, because the Google Health API exposes no raw motion data. Its score is not
a clinical probability. FarCare never calls emergency services itself; it gets the right humans
moving and tells them when to call.
