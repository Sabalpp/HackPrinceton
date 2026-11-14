# FarCare API & event contracts

**Source of truth:** `apps/api/app/schemas/contracts.py`. Generated artifacts:

- `packages/contracts/schemas/*.schema.json`: JSON Schema per payload
- `packages/contracts/openapi.json`: full HTTP API
- `apps/web/lib/api-types.ts`: TypeScript types (`npm run gen:api`)
- `packages/contracts/fixtures/*.json`: canonical examples (validated in CI)

Live interactive docs: `http://localhost:8000/docs`. Change process: [AGENTS.md](../AGENTS.md#changing-a-shared-contract).

Conventions: JSON, snake_case, ISO-8601 UTC timestamps, IDs match `^[A-Za-z0-9_\-:.]{1,128}$`,
unknown fields are rejected on input (`extra=forbid`).

## Authentication

| Caller | How |
| --- | --- |
| Dashboard (child, parent, admin) | `Authorization: Bearer <token>` (SSE: `?access_token=`) |
| Ingestion (fall detector, health sync) | `X-FarCare-Ingest-Key: <INGEST_API_KEY>` |
| Photon | Standard Webhooks signature headers (`webhook-id`, `webhook-timestamp`, `webhook-signature`) |

A `parent_id` in a URL is only honored if the caller is that parent, a linked family member, or
the demo admin. Otherwise the API returns 404.

## Endpoints

| Method | Path | Auth | Returns |
| --- | --- | --- | --- |
| GET | `/health` | none | `SystemHealth` (status, db, messaging photon/console, ai gemini/fallback, commit) |
| GET | `/api/me` | bearer | `Me` (person + accessible `parent_ids`) |
| GET | `/api/parents/{id}/overview` | bearer | `ParentOverview` (one call for the main dashboard) |
| GET | `/api/parents/{id}/health?days=35` | bearer | `HealthSummary` or `null` if not shared |
| GET | `/api/parents/{id}/care-circle` | bearer | `CareCircleMember[]` |
| GET | `/api/parents/{id}/care-requests?limit=50` | bearer | `CareRequest[]` (newest first) |
| GET | `/api/parents/{id}/incidents?active_only=false` | bearer | `SafetyIncident[]` |
| POST | `/api/parents/{id}/incidents/{incident_id}/resolve` | bearer | `SafetyIncident` (family marks resolved; stands down responders) |
| GET | `/api/parents/{id}/timeline?after=0&limit=100` | bearer | `TimelineEvent[]` (newest first) |
| GET | `/api/parents/{id}/stream` | bearer (`?access_token=`) | SSE `event: timeline` with `TimelineEvent` JSON |
| POST | `/api/events/fall` | ingest key | `FallIngestResult` |
| POST | `/api/health/observations` | ingest key | `HealthIngestResult` (body: `HealthObservation[]`, max 1000, upsert by id) |
| POST | `/webhooks/photon` | signature | 204 (`message.received` handled, other events acknowledged) |
| POST | `/api/demo/fall` | admin + DEMO_MODE | detector result (+ ingest result if `possible_fall`) |
| POST | `/api/demo/inbound` | admin + DEMO_MODE | simulate a person texting FarCare; returns FarCare's outbound messages |
| GET | `/api/demo/outbox` · `/api/demo/people` | admin + DEMO_MODE | recent outbound messages / people + reachability |
| POST | `/api/demo/reset` | admin + DEMO_MODE | clears workflow state (keeps people + health data) |

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
`trigger`: `fall.suspected` | `parent_message`. Also includes `no_responder_available`,
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
