# Photon 2.0 track: requirements and how FarCare meets them

Source: HackPrinceton Photon 2.0 track guidelines (shared by the team, Nov 14).

## Must-haves to qualify

| Requirement | FarCare |
| --- | --- |
| Built on Photon 2.0 beta (free; dashboard at photon.codes/beta) | Python `photonhq-api` against `api.photon.codes/v1` (the 2.0 API). See CLAUDE.md. |
| Live on at least one Photon channel | iMessage (flagship). Go-live steps: `docs/agents/SUMIT.md`. |
| Voice, if entering Best Voice Agent | Must run on a Photon **SMS number** + Voice profile over SIP (Retell, ElevenLabs, LiveKit or Twilio). Not on iMessage. |

**Constraint:** iMessage **group chats and calls need a dedicated iMessage line**, which isn't
the hackathon setup. FarCare therefore coordinates people through **separate 1:1 threads**
(parent, son, neighbors) and connects them in the backend. Don't plan features that need a
group chat.

**To confirm with Photon (Discord):** the guidelines link the SDK reference to
`spectrum-ts`, which Photon's own docs index labels *Stable*, not 2.0. We use the 2.0 API.
Ask them to confirm this qualifies.

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
