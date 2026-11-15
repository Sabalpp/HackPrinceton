# FarCare API & event contracts

**Source of truth:** `backend/app/schemas/contracts.py` and `schemas/experience.py`. Generated artifacts:

- `packages/contracts/schemas/*.schema.json`: JSON Schema per payload
- `packages/contracts/openapi.json`: full HTTP API
- `frontend/lib/api-types.ts`: TypeScript types (`npm run gen:api`)
- `packages/contracts/fixtures/*.json`: canonical examples (validated in CI)

Live interactive docs: `http://localhost:8000/docs`. Change process: [AGENTS.md](../AGENTS.md#changing-a-shared-contract).

Conventions: JSON, snake_case, ISO-8601 UTC timestamps, IDs match `^[A-Za-z0-9_\-:.]{1,128}$`,
unknown fields are rejected on input (`extra=forbid`).

## Authentication

| Caller | How |
| --- | --- |
| Individual accounts (child, parent, responder) | HttpOnly session cookie; `X-CSRF-Token` on mutations; SSE uses credentials |
| Legacy local/demo callers | Bearer tokens allowed only in development/test; rejected in production |
| Ingestion (fall detector, health sync) | `X-FarCare-Ingest-Key: <INGEST_API_KEY>` |
| Photon | Standard Webhooks signature headers (`webhook-id`, `webhook-timestamp`, `webhook-signature`) |

A `parent_id` in a URL is only honored if the caller is that parent, a linked family member, or
the demo admin. Otherwise the API returns 404.

## Endpoints

| Method | Path | Auth | Returns |
| --- | --- | --- | --- |
| GET | `/health` | none | `SystemHealth` (status, db, messaging photon/console, ai gemini/fallback, commit) |
| GET | `/api/me` | session cookie | `Me` (person + accessible `parent_ids`) |
| GET | `/api/me/conversation?limit=60` | session cookie | `ChatThread` with only the signed-in person's messages, filtered by current sharing and task permissions |
| POST | `/api/me/conversation` | session cookie + CSRF | `ChatThread`; `ChatIn.text` runs the existing conversation and care-action rules, with replies in the app |
| GET | `/api/parents/{id}/overview` | session cookie | `ParentOverview` (one call for the main dashboard) |
| GET | `/api/parents/{id}/health?days=35` | session cookie | `HealthSummary` or `null` if not shared |
| GET | `/api/parents/{id}/care-circle` | session cookie | `CareCircleMember[]` |
| GET | `/api/parents/{id}/care-requests?limit=50` | session cookie | `CareRequest[]` (newest first) |
| GET | `/api/parents/{id}/incidents?active_only=false` | session cookie | `SafetyIncident[]` |
| POST | `/api/parents/{id}/incidents/{incident_id}/resolve` | session cookie | `SafetyIncident` (family marks resolved; stands down responders) |
| GET | `/api/parents/{id}/timeline?after=0&limit=100` | session cookie | `TimelineEvent[]` (newest first) |
| GET | `/api/parents/{id}/stream` | session cookie | SSE `event: timeline` with `TimelineEvent` JSON |
| POST | `/api/events/fall` | ingest key | `FallIngestResult` |
| POST | `/api/health/observations` | ingest key | `HealthIngestResult` (body: `HealthObservation[]`, max 1000, upsert by id). Receipt: `accepted`, `changed` (new or different values), `days_received`, `data_mode`, `suggest_checkin`, `notable_changes` |
| POST | `/webhooks/photon` | signature | 204 (`message.received` handled, other events acknowledged) |
| POST | `/api/demo/fall` | admin + DEMO_MODE | detector result (+ ingest result if `possible_fall`) |
| POST | `/api/demo/inbound` | admin + DEMO_MODE | simulate a person texting FarCare; returns FarCare's outbound messages |
| POST | `/api/demo/checkin` | admin + DEMO_MODE | send the proactive wellness check-in now (skips the daily window) |
| GET | `/api/demo/outbox` · `/api/demo/people` | admin + DEMO_MODE | recent outbound messages / people + reachability |
| POST | `/api/demo/reset?include_health=false` | admin + DEMO_MODE | clears workflow state (keeps people; health data too unless `include_health=true`) |

## Core payloads (fixtures in `packages/contracts/fixtures/`)

### HealthObservation (ingestion; Sabal produces)

```json
{"observation_id": "health_001", "parent_id": "parent_001", "source": "fitbit",
 "data_mode": "simulated", "timestamp": "2026-11-14T00:00:00Z",
 "steps": 1200, "resting_heart_rate": 72, "sleep_minutes": 390}
```

`source`: `fitbit` | `simulated`. `data_mode`: `live` | `simulated`. One observation per day
(`timestamp` = start of day UTC). Metrics are nullable. Ranges are validated (steps 0–200000, RHR
20–250, sleep 0–1440).

### FallSuspectedEvent (ingestion)

```json
{"event_id": "fall_001", "parent_id": "parent_001", "event_type": "fall.suspected",
 "source": "simulated_imu", "timestamp": "2026-11-14T15:30:00Z",
 "confidence": 0.91, "data_mode": "simulated"}
```

`confidence` is a **detector output score, not a validated clinical probability**. `source`:
`simulated_imu` | `replayed_dataset` | `device`. Resending the same `event_id` is idempotent.

### CareRequest

```json
{"request_id": "care_001", "parent_id": "parent_001", "request_type": "grocery_assistance",
 "status": "awaiting_approval", "responder_id": "responder_001", "responder_name": "Asha",
 "parent_approved": false, "approval_basis": null, "origin": "conversation",
 "details": "", "requested_time": "tomorrow", "estimated_arrival_minutes": null,
 "responder_note": null, "incident_id": null,
 "created_at": "2026-11-14T15:30:00Z", "updated_at": "2026-11-14T15:30:02Z"}
```

- `request_type`: `grocery_assistance`, `transportation`, `meal_assistance`,
  `medication_pickup`, `household_help`, `companionship`, `urgent_check_in` (emergency only),
  `other`.
- `status`: `created → awaiting_approval → approved → sent → accepted → completed`, plus
  `declined`, `cancelled`, `expired`, `failed`.
- `approval_basis`: `parent_reply` (ordinary) | `emergency_policy` (escalation; pre-approved).
- `origin`: `conversation` | `emergency`.
- `responder_note`: clock time the responder gave, e.g. `"4 PM"`.
- `requested_by_id` / `requested_by_name`: set when a family member asked on the parent's
  behalf. The parent still approved it (`parent_approved`). The UI can show "Requested by Aarav".

### ResponderReply (parsed from responder texts)

```json
{"request_id": "care_001", "responder_id": "responder_001", "response": "accepted",
 "estimated_arrival_minutes": 15}
```

`response`: `accepted` | `declined` | `completed` | `need_more_help`.

### SafetyIncident

`status`: `checking` → `escalated` → `responder_en_route` → `resolved`.
`escalation_reason`: `help_requested` | `no_response` | `parent_reported_emergency`.
`resolution`: `parent_ok` | `responder_confirmed` | `family_resolved`.
`trigger`: `fall.suspected` | `parent_message` | `family_concern` (a family member asked
FarCare to check on the parent, e.g. "Mom isn't answering"). Also includes `no_responder_available`,
`check_deadline`, `opened_at`, `escalated_at`, `resolved_at`, `confidence`, `data_mode`.

### TimelineEvent (REST + SSE)

```json
{"id": 42, "parent_id": "parent_001", "kind": "care_request.updated",
 "summary": "Asha accepted at 4 PM", "ref_id": "care_001",
 "data": {"from": "sent", "to": "accepted", "request_type": "grocery_assistance"},
 "created_at": "2026-11-14T15:35:00Z"}
```

`kind` values: `care_request.created`, `care_request.updated`, `care_request.delivery_failed`,
`care_request.more_help_needed`, `incident.opened`, `incident.escalated`,
`incident.check_undeliverable`, `incident.responder_en_route`, `incident.no_responder`,
`incident.resolved`, `fall.duplicate_suppressed`, `checkin.recorded` (needs `share_checkins`),
`checkin.prompted` (FarCare reached out proactively; needs `share_health`),
`health.synced` (a health-data delivery changed something; needs `share_health`),
`agent.replied` (parent-only). The UI should show `summary` and use `kind` + `ref_id` to refresh
the related card. Treat unknown kinds as generic.

SSE wire format:

```text
id: 42
event: timeline
data: {...TimelineEvent...}
```

### HealthSummary / MetricTrend

`trends[]` has one entry each for `steps`, `sleep_minutes`, `resting_heart_rate`, with
`baseline` (mean before the last 5 days, up to 30 days), `recent` (mean of the last 5 days),
`percent_change`, `direction` (`up`/`down`/`stable`/`insufficient_data`), `notable`, and a
plain-language **non-diagnostic** `observation`. Notable thresholds: steps ±25%, sleep ±12%,
RHR ±8%. `suggest_checkin` is true if any trend is notable. Always render `disclaimer` and the
`data_mode` label.

## Photon inbound (what the webhook accepts)

`type == "message.received"`, `data.projectId` equal to ours, `data.object` a Message with
`status: received`, `sentBy.kind: human`, `receivedBy.kind: agent`, `space.kind: direct`,
`content.type: text`. Messages are routed by `sentBy.handle` (E.164 or iMessage email) to a
known person. Unknown senders are ignored.

## Additive account and role endpoints

Schemas for this release are in `schemas/experience.py`; the generated OpenAPI includes
all request/response types. These endpoints never require a public shared token.

| Group | Endpoints and behavior |
| --- | --- |
| Accounts | GET `/api/auth/session`; POST `/api/auth/login`, `/register`, `/logout`, `/invitation`; `/demo` exists only outside production |
| Onboarding | POST `/api/onboarding/parents`, `/onboarding/invites`; create one-time role-bound invitations; both require an action_id |
| Parent | GET `/api/parent/home`; POST `/parent/check-in`, `/parent/requests`, `/parent/requests/{id}/decision` |
| Consent | POST `/api/parent/sharing`, `/parent/responders/{id}/approve`; parent-only |
| Helper | GET `/api/responder/tasks`; POST `/responder/tasks/{id}/reply`; current assigned/authorized tasks only |
| Personal health | GET `/api/parents/{id}/personal-health`; null if health sharing is off |
| Cuff BP | POST `/api/parent/blood-pressure`; recorded timestamp/context, manual cuff source, simulated for explicit demo sessions |
| Personal reference | POST `/api/parents/{id}/reference`; parent-only provisional statement, no safety override |
| Readiness | GET `/ready`; 503 if production worker has no recent successful cycle; no private counts |

Cookie mutations require the CSRF token returned by the authenticated session endpoint.
Clients include credentials. Personal responses are no-store; passwords/tokens are not
echoed in validation errors. Production requires a configured HTTPS origin and disables
demo data and all legacy bearer authentication.

Check-in, task, BP and onboarding mutations use an action identifier retained across
retries. Reusing one for different input yields 409; repeating the same completed action
returns its receipt. A new action still checks current consent and assignment. A helper
can replay their successful decline after reassignment, but cannot perform a new action
on the reassigned task. Consumed/expired invitation responses cannot be revived.

`PersonalMetric` exposes coverage, descriptive median/MAD, exclusions, a provisional
reference and an optional observational comparison. No comparison denotes a safe range.
BP guidance records its event time; it is separate from baseline learning. See
[health-evidence.md](health-evidence.md) for primary sources and engineering choices.
