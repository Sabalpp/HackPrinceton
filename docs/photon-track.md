# Photon 2.0 track: requirements and how FarCare meets them

Source: HackPrinceton Photon 2.0 track guidelines (shared by the team, Nov 14).

## Must-haves to qualify

| Requirement | FarCare |
| --- | --- |
| Built on Photon 2.0 beta (free; dashboard at photon.codes/beta) | Python `photonhq-api` against `api.photon.codes/v1` (the 2.0 API). See CLAUDE.md. |
| Live on at least one Photon channel | SMS or iMessage using the same consent-aware agent and durable outbox. SMS setup and evidence checks below. |
| Voice, if entering Best Voice Agent | Must run on a Photon **SMS number** + Voice profile over SIP (Retell, ElevenLabs, LiveKit or Twilio). Not on iMessage. |

**Constraint:** iMessage **group chats and calls need a dedicated iMessage line**, which isn't
the hackathon setup. FarCare therefore coordinates people through **separate 1:1 threads**
(parent, son, neighbors) and connects them in the backend. Don't plan features that need a
group chat.

**To confirm with Photon (Discord):** the guidelines link the SDK reference to
`spectrum-ts`, which Photon's own docs index labels *Stable*, not 2.0. We use the 2.0 API.
Ask them to confirm this qualifies.

## SMS setup and release check

FarCare accepts one configured messaging platform per deployment. Existing iMessage setups
continue to work with `PHOTON_PLATFORM=imessage`. Set `PHOTON_PLATFORM=sms` to use an eligible
Photon SMS sender; a shared iMessage assignment is not an SMS sender.

Photon's current [Beta overview](https://photon.codes/docs/beta) says SMS needs an eligible
number and required sender registration. Its [published OpenAPI](https://api.photon.codes/openapi.json)
requires current Platforms authorization for the sender and every recipient, plus campaign
eligibility unless every recipient is in-network. Buying a number alone does not satisfy
these requirements. The Beta remains a preview; use this integration as a controlled pilot
until the end-to-end checks below pass for consenting recipients.

1. In the Photon Beta project, configure an eligible SMS line and finish the applicable
   sender registration/campaign flow. FarCare does not buy numbers or register campaigns.
2. With the project key and project ID in `backend/.env`, inspect existing SMS agents:
   `cd backend && uv run python scripts/photon_setup.py sms-status`. It prints sender user
   IDs, masked handles, and campaign eligibility. This command is read-only. If you have
   not chosen a sender yet, leave `PHOTON_PLATFORM=imessage` during this discovery step.
3. Configure the API and worker with the same values:

   ```dotenv
   MESSAGING_CHANNEL=photon
   PHOTON_PLATFORM=sms
   PHOTON_AGENT_USER_ID=pho_usr_...
   PHOTON_API_KEY=pho_sk_...
   PHOTON_PROJECT_ID=pho_prj_...
   PHOTON_DELIVERY=webhook
   PHOTON_WEBHOOK_SECRET=whsec_...
   ```

4. Register the public HTTPS endpoint `/webhooks/photon` for `message.received`,
   `message.dispatched`, `message.delivered`, and `message.failed`. The `photon_setup.py webhook`
   command creates a destination with all four event types and shows its signing secret once;
   keep that output private. Existing destinations must enable the three receipt event types
   too. Store the signing secret on the API; never in a browser environment or Git.
5. Create the real family accounts and consent links, with E.164 phone numbers. Each consenting
   recipient texts `JOIN` from their registered number to the configured SMS line. FarCare
   learns the actual direct Space from the signed event. Unknown numbers are ignored and do
   not create accounts. Creating an account alone does not opt its owner into SMS.
6. Run `python -m app.worker` continuously against the same PostgreSQL database as the API.
   Production webhook requests only commit incoming events. The worker processes the
   conversation and submits committed outbox jobs with stable provider idempotency keys.
7. Test a real consenting recipient: `JOIN`, `HELP`, an approved care request, a reply,
   `STOP`, then `START`. Check the handset and the matching provider message ID. No code
   test or `accepted` status is proof that an SMS reached the phone.

`STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, `REVOKE`, and `OPT OUT` pause SMS
and cancel pending outbox jobs. Ordinary messages do not reset opt-out. `START`/`UNSTOP`
reenables future messages without replaying cancelled alerts. FarCare sends no extra SMS
after STOP; provider/carrier opt-out acknowledgements are separate. `HELP` returns concise
instructions without calling Gemini. During an active parent safety check, `HELP` retains its
existing meaning: ask the authorized care circle for help. FarCare never dispatches emergency
services or treats a carrier receipt as human acknowledgement.

SMS does not send typing indicators. iMessage retains its shared-assignment and initial-inbound
rules. Changing a deployment from iMessage to SMS does not reuse old iMessage Spaces.

### What message status means

| Status | Evidence |
| --- | --- |
| `available` | Accessible through the authenticated app; no SMS submitted. |
| `queued` | A durable send intent exists; the worker has not completed submission. |
| `accepted` | Photon accepted the request, including its provider message ID. |
| `sent` | A signed Photon dispatch receipt was processed. |
| `delivered` | A signed Photon delivery receipt matched that message, Space, and sender. This is not a read receipt. |
| `failed` | Submission failed or Photon reported a terminal delivery failure. |
| `cancelled` | Consent, identity, routing, or task authorization changed before submission. |
| `unreachable` | No usable opted-in route exists for this channel. |
| `simulated` | Console/test channel; no provider send. |

Receipts are persisted and deduplicated independently of chat. A delayed dispatch receipt cannot
downgrade a delivery confirmation. Provider-reported recipient opt-out cancels further pending
messages. Unknown receipt IDs never attach to another person's conversation. Polling remains a
local inbound-text option; use signed webhooks to collect outbound delivery evidence.

Offline verification: `cd backend && uv run pytest tests/test_photon_sms.py
tests/test_photon_client.py tests/test_webhook_and_agent.py tests/test_care_flow.py tests/test_worker.py -q`.
The suite uses fictional numbers and replacement provider clients, and sends no real messages.

## Judging criteria → what we show

| Criterion | Where FarCare shows it |
| --- | --- |
| **Vision: hybrid intelligence** | Gemini understands people; humans decide (parent consents, neighbor accepts); deterministic rules own safety. The agent coordinates a whole care circle, not one user. |
| **Craft: cohesive, responsive** | Typing indicator while thinking, SMS-short replies, idempotent sends (no duplicate texts), live dashboard updates. |
| **Depth: real-world context and edge cases** | Neighbor declines → next person proposed. Parent says no → son told politely. "Mom isn't answering" → check-in, then escalation. Duplicate fall events merged. Parent unreachable → escalate. Gemini down → rule-based fallback. |
| **Traction: real users tomorrow** | Consent model, minimum-information messages to neighbors, sharing permissions, signed webhooks, production guards, Postgres. |

## "What great looks like" → features

| Photon wants | FarCare |
| --- | --- |
| Real human-in-the-loop collaboration | Parent YES before anyone is contacted; son's requests need Mom's consent; neighbor YES/NO/DONE. |
| Context that persists across time, channels, teammates | Conversation memory per person; proactive check-in uses her activity history; the son's request flows into Mom's thread and the neighbor's thread. |
| A delightful default that doesn't demand attention | At most one gentle check-in a day, only when activity changes, only in her daytime, never mid-conversation. |
| Thoughtful safety and fallback behaviors | Deterministic escalation the LLM can't suppress; console channel and demo controls if Photon fails; Gemini fallback. |
| Voice: remembers the call back in text | Planned: ElevenLabs post-call transcript → conversation memory, so the next iMessage continues the call. |

## Voice: what exists now

**Browser voice (free, built):** the Demo Console's **Call** button runs a hands-free spoken
conversation (browser speech recognition + speech synthesis) through the same backend, the same
deterministic rules, and real Gemini. Spoken turns are stored as `(by voice call)` turns, so the
next text continues the call. It **does not qualify** for Best Voice Agent, which needs real
phone calls on a Photon SMS number.

**Cost of the qualifying path (Photon pricing, checked Nov 14):** a local US number is
$2/month and voice is $0.013/min on the Free plan. ElevenLabs' free tier reportedly includes
about 15 agent minutes per month. Check that its free plan allows importing a SIP number.

## Voice plan for real phone calls (optional, after text is live)

1. In the Photon dashboard, add an SMS number and set up its default Voice profile with SIP.
2. Create an ElevenLabs agent and import the Photon number via **Deploy → Phone Numbers →
   Import number → From SIP Trunk**. Point Photon's inbound destination at
   `sips:sip.rtc.elevenlabs.io:5061`.
3. Have the ElevenLabs agent call FarCare server tools for `request_assistance`, and send its
   post-call transcript to a new FarCare endpoint that stores it as conversation turns for the
   caller. The next text message then has the call's context.
4. Verify audio in both directions and that the call ends normally.
