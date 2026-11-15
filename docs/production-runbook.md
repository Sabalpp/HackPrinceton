# FarCare deployment and worker runbook

This is a prepared deployment path, not evidence that FarCare is deployed or
that a real message has been delivered. No provider account, number, assignment,
public endpoint, or paid resource was created by preparing these files.

## What Photon provides

Photon 2.0 Beta provides messaging/voice transport and agent integration tools.
FarCare's Gemini integration supplies language reasoning; deterministic code
owns consent and safety transitions. Neither a Photon SDK nor an MCP connection
hosts an always-running FarCare agent.

The [official Beta introduction](https://photon.codes/docs/beta) calls the product
a preview and recommends Stable for production agents. We keep this repository
on its existing 2.0 API for the hackathon and an explicitly evaluated pilot. A
general availability claim requires a separate provider-readiness decision;
do not silently substitute Spectrum packages or credentials.

The team's [track checklist](photon-track.md) records a 2.0 integration and a live
Photon channel as qualifying requirements. The supplied
[sponsor guidelines](https://somethingreat.notion.site/Hackathon-Guidelines-for-Photon-2-0-3f8e443f36bb81f9893fed0d08a4c01d)
returned an unreadable Notion shell during this verification, so the copied
checklist remains the available event-specific evidence. Browser speech alone
does not establish a Photon Voice integration; that requires an eligible number
and SIP voice setup, followed by a real call test.

## Runtime topology

- API: authentication, permission checks, durable webhook intake, dashboard SSE.
  Production disables demo data and API-owned background loops.
- Worker: `python -m app.worker`, in a process supervised independently of HTTP
  requests. It shares the API's PostgreSQL database and deployment version.
- Web: Next frontend, built with `NEXT_PUBLIC_API_URL=/backend` and mocks off.
- Gateway: strips `/backend` before proxying to the API, preserving frontend
  `/health` separately from API `/health`. API and web are not published directly.
  It applies a 2 MB [request-body limit](https://caddyserver.com/docs/caddyfile/directives/request_body).
- HTTPS ingress: supplied by the operator. The example gateway publishes HTTP
  only on `127.0.0.1:8080`; this is a private upstream, not the public application.

Use one worker initially. The database mutex supports an accidental second
worker or standby, but throughput is serialized. It is not a parallel job queue.
Workers do not depend on the Mac, a terminal window, or an open browser once
deployed on an always-running host. No particular host has been selected here.

## Durable processing and its limits

`agent_leases` holds a singleton `farcare-worker` row. PostgreSQL `FOR UPDATE
NOWAIT` is held through each complete unit and its commit. The database releases
the lock on transaction rollback/disconnect; heartbeat age never authorizes
stealing an in-flight operation. SQLite `BEGIN IMMEDIATE` supports local tests,
but the production worker refuses SQLite. API mutations can use the same lock
with `track_heartbeat=False`, so HTTP activity cannot impersonate worker health.

The worker processes persisted inbound messages before deadlines. Handler
failure rolls back its business changes and outbound intents using a savepoint,
then records a sanitized error and bounded retry schedule. Whole-process failure
rolls the transaction back to pending. Three handler failures require operator
review. A pending/legacy-processing backlog defers deadline work to avoid
overtaking an already-arrived OK/HELP; monitor queue age because sustained backlog
also delays genuine timeout handling.

`send_to_person` stores a Photon `DeliveryJob` in the same transaction as the
business change. It does not perform a network send. A separate unit submits the
already-committed recipient/body snapshot using a stable hashed idempotency key.
After an ambiguous network result or a crash before the local acceptance commit,
the next worker submits the identical request with the same key. A changed
recipient route stops the old job instead of silently retargeting it.

Every scoped intent snapshots its parent, visibility, recipient role/account
generation, membership and any task assignment. Immediately before a provider
send, current database permissions are checked under the shared mutation lock.
Revoked health/check-in/emergency permissions, deleted links/accounts, inactive
care membership, removed task categories, reassignment or cancelled offers mark
the intent `cancelled` without contacting Photon. Generic family notifications
also require their original link. Ordinary help needs explicit parent approval;
emergency-policy requests require the current emergency-contact authorization.
Approval to contact one ordinary responder does not transfer to a replacement.

Registered account holders can receive assignments without a phone handle:
`OutboundMessage.channel='in_app'`, `status='available'`. This records state
available through the authenticated app; it is neither a push notification nor a
read/delivery receipt. Role-specific task APIs recheck active membership,
assignment and consent when read or acted upon. Photon is used only when routing
and the first-contact opt-in exist. Responder text uses the parent's first name
and task details, not full names or health-history exports.

Conversation turns currently lack parent/consent versions. Family-model calls
therefore omit prior turns and use the current request with freshly authorized
tools. This conservative rule also covers a removed parent link while another
link remains valid. Stored conversation history is not retroactively erased from
providers that already received it; consent-scoped history is a future feature.

Retries use persisted due times, exponential delay and a configured attempt
limit. Retryable provider errors honor `Retry-After`; an interval above the
configured maximum stops for operator review rather than retrying too early.
Missing credentials leave real jobs queued; they never become simulated success.
Error bodies, tokens, phone numbers and message text are excluded from runtime
error categories and the status report.

Photon acceptance is recorded as `accepted`, **not delivered or read**. The
legacy `delivered()` helper means accepted for coordination (including queued
intent); callers must not use it as a device receipt. Existing care state names
such as `sent` are workflow states, not delivery proof. Provider idempotency has
its own retention and guarantees; this worker does not promise exactly-once
network delivery. Automated recovery after a long provider outage needs a review
of that retention window before replay. Signed Photon receipts now update the
durable outbox to `delivered` or `failed`, with matching sender, channel, Space and
message identity. A delivered receipt still does not prove that a human read the
message or can respond. Additional dispatch-failure escalation remains separate.

Photon's [webhook guide](https://photon.codes/docs/beta/message-delivery/webhook)
requires signature verification, prompt durable acceptance and duplicate
handling. Its documented defaults include a 20-second delivery timeout and
retry limits of 20 failures or four days. FarCare commits intake before 204 and
returns 503 if persistence fails. See the [client retry guide](https://photon.codes/docs/beta/api-client/retries-and-timeouts)
for provider-level attempts and idempotency; the durable dispatcher owns the
application's retry schedule.

## Prepare an invited pilot

1. Finish the account/session and per-family authorization acceptance tests.
   Keep public registration disabled; onboard only consenting invited users.
   Never ship a `NEXT_PUBLIC_VIEWER_TOKEN` or development bearer token as auth.
2. Back up the PostgreSQL database and verify restore access. API and worker must
   use the same database/schema. `init_db` creates new tables and applies additive
   upgrades for the SMS columns and delivery context under a PostgreSQL advisory
   lock. Old pending jobs have no trustworthy consent context and remain
   `unscoped`; they cancel instead of guessing grants. Existing people and
   business records are retained. Stop the previous API and worker during rollout
   so their older routing behavior cannot race the new processor.
3. Stop legacy API background processing during rollout. Inspect pre-upgrade
   `inbound_messages.status='processing'` rows. They lack a trustworthy claim
   timestamp and are deliberately not stolen. Requeue only after confirming the
   old processor is stopped and reviewing previous side effects. Do not bulk
   reset failed outbound records: historical rows lack an immutable request
   snapshot, and an earlier network send may have succeeded.
4. Configure real Photon project credentials and a signing secret server-side.
   For SMS, select `PHOTON_PLATFORM=sms` and a provisioned `PHOTON_AGENT_USER_ID`;
   check the eligible sender/campaign with `photon_setup.py sms-status` and have
   registered participants text first. For iMessage, assign the shared line and
   have each participant message it, as required by the
   [official iMessage guide](https://photon.codes/docs/beta/get-started/imessage).
   Configure the public HTTPS webhook as `/backend/webhooks/photon` through this
   gateway. Do not register the internal HTTP listener with Photon.
5. Configure Gemini if AI replies are intended. Otherwise label deterministic
   fallback accurately. Configure the same supported model in API and worker.
6. Set a real HTTPS `PUBLIC_ORIGIN`, matching CORS and browser cookie/CSRF policy.
   Install an HTTPS ingress in front of loopback port 8080. No TLS setup or
   public hostname is supplied by the private template.
7. Copy `deploy/.env.example` to ignored `deploy/.env.production`, supply secrets
   through the chosen host's secret store, and review the production guards.
   Keep proactive messaging off until consent, timezone and delivery are tested.

The initial presence-only check found no Photon API key, project ID, signing
secret, or Gemini API key. Their values were not printed or copied. A database
URL was present. This is a live-integration gate, not a setup success.

## Build and start after host/credentials are chosen

From the repository root, validate without printing resolved secret values:

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml config --quiet
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml build
```

Starting the reviewed deployment is a separate operational action:

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml up -d
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml exec worker python -m app.worker --check
```

These commands have not been executed against a production host. Pin the built
application images and gateway image digest for a release; the template uses
maintained major image tags as a starting point. The Next image retains build
dependencies for its TypeScript configuration loader; a smaller standalone image
is a later packaging improvement, not a prerequisite for the worker semantics.

Preparation verification on 2026-11-14: the worker and related webhook, care,
safety and family tests passed locally; the latest counts are recorded in the
implementation handoff. The separate
PostgreSQL mutex test was skipped because this local run uses isolated SQLite;
CI must run it with a disposable `TEST_DATABASE_URL`. Ruff checks and formatting
passed for the runtime files. `docker compose config` validated all four services
without resolving secret env files and confirmed that the only published binding
is `127.0.0.1:8080`. Images have not been built or containers started in this task.

## Verify, observe, recover

- Run the full API suite on SQLite and disposable PostgreSQL in CI. The worker
  tests include rollback, duplicate intake, identical replay after simulated
  provider acceptance, bounded retry, recipient changes and competing PG locks.
- Test authentication, family isolation, consent rejection, session expiry,
  CSRF protection, permission revocation, SSE reconnect and missing health data.
- With consenting testers, verify actual inbound and received outbound messages
  on devices. Restart the API/worker between receipt and processing, then confirm
  one business transition and no repeated visible message. Never use vulnerable
  people or real emergency events for this acceptance test.
- Check `python -m app.worker --check` and private operational status. Heartbeat
  freshness alone is insufficient: inspect pending/failed counts, oldest queue
  age and legacy-processing rows. Failed delivery needs human attention; it is
  not proof a care request reached anyone. Configure external monitoring and an
  operator notification channel independently of the potentially broken Photon
  path. Container restart policies do not themselves page an operator, and an
  unhealthy status does not automatically restart a running container.
- Monitor the shared database, account/credential expiry, provider failures and
  health-sync freshness. Preserve source timestamps and missing-data markers.
- For transient failure, normal pending jobs retry automatically. For terminal
  jobs, inspect the sanitized category and provider logs privately, confirm the
  same request/key is still safe to replay, then authorize a controlled requeue.
  There is intentionally no unauthenticated replay/reset endpoint.

The policy timer pass currently evaluates the existing service queries as a
single transaction. Large user populations need sharded/bounded timer queries and
load testing before widening the pilot. Pending message contents and routing
remain sensitive data even though operational status is sanitized; host access,
retention and backup policy need an explicit operational owner before broad
availability. Cancellation protects jobs that have not yet been accepted by the
provider; it cannot retract a message already sent to someone's device.
