# Vercel: dashboard, Tiger API and care worker

The root [vercel.json](../vercel.json) deploys the existing Next.js dashboard,
FastAPI backend and durable care worker as one Vercel project. This is a hosting
adapter; authentication, health queries, consent and messaging workflows stay in
their existing modules. The Bluetooth collector stays on the Mac near the watch.

Vercel Services and Daemons are currently beta features available on Hobby. The
daemon uses the existing API Dockerfile, built remotely by Vercel; deploying does
not require installing Docker on the Mac. Hobby includes 5 active CPU hours and
420 GB-hours of daemon memory per month, shared across the team. This configuration
requests 1 GiB, so memory alone allows roughly 17 days of continuous operation if
the entire quota is available. Usage limits can stop the worker. This is a
bounded hackathon hosting option, not unlimited free, always-on care monitoring.
[Services](https://vercel.com/docs/services),
[Daemon limits](https://vercel.com/docs/daemons/limits)

## Import settings

| Setting | Value |
| --- | --- |
| Git repository | `Sabalpp/HackPrinceton` |
| Production branch | `main` |
| Root directory | Repository root (`./`) |
| Framework preset | Other; the Services configuration specifies each framework |
| Build, install and output overrides | Leave unset; `vercel.json` owns service-specific commands |
| Node.js | 22.x |
| Python | 3.12, selected by `backend/.python-version` |

If the existing project uses `backend/collector` or `frontend` as its root, change it
to the repository root before redeploying. The collector has no web entrypoint.
Selecting `frontend` alone only deploys the dashboard. Do not add a fake server to
the collector or put Tiger credentials in frontend environment variables.

Services routes `/backend/*` to `app.vercel_entry:app` and all other paths to the
dashboard. The mounted API preserves that prefix, cookies and the existing app
lifecycle. The daemon runs `python -m app.vercel_worker` and is production-only;
preview deployments do not have an independent worker. Use a separate test database
for previews instead of pointing branch deployments at the production family data.
[Services routing](https://vercel.com/docs/services/routing),
[Daemon configuration](https://vercel.com/docs/daemons/config-reference)

## Environment variables

Use [deploy/vercel/.env.example](../deploy/vercel/.env.example) as a field list;
enter real values only in Vercel's **Production** environment settings.

| Required name | Value |
| --- | --- |
| `DATABASE_URL` | Existing Tiger PostgreSQL connection URI, with TLS (`sslmode=require`) |
| `INGEST_API_KEY` | A strong private machine-ingestion secret; preserve the existing key if used |
| `CORS_ORIGINS` | Exact public HTTPS origin, e.g. `https://farcare.vercel.app`; comma-separated for multiple domains |

The Vercel entrypoints default to production mode with demo data, public registration
and API-owned background tasks disabled. Missing production configuration fails
startup. Do not copy a development `.env` with demo switches enabled. The web build
uses `/backend` and explicitly disables mocks and local preview. Leave
`FARCARE_API_ORIGIN` unset on Vercel: Services performs the routing, so there is no
localhost backend URL to configure. No secret uses a `NEXT_PUBLIC_` prefix.

Vercel supplies `VERCEL_GIT_COMMIT_SHA`; the entrypoints expose that revision through
the existing health endpoint. If system environment variables were disabled in
the project, enable them or explicitly set `GIT_COMMIT` to the deployed revision.

After deploying, verify both endpoints:

```bash
curl -fsS https://YOUR-PROJECT.vercel.app/backend/health
curl -fsS https://YOUR-PROJECT.vercel.app/backend/ready
```

Require the expected revision, `environment: production`, `database: ok`,
`demo_mode: false`, and readiness HTTP 200. Readiness checks the worker heartbeat;
a rendered login page alone does not prove Tiger connectivity or active messaging.

## Connect real watch readings

```text
Fitbit → Mac BLE collector → durable local outbox → Tiger
Google Health sync → Mac cloud collector ───────────→ Tiger
Tiger → authenticated /backend API → dashboard (visible-page refresh every 2 s)
Tiger → care worker → consented check-ins and configured Photon delivery
```

The API reads recent raw records directly; it does not wait for the one-minute
aggregate to refresh. The collector preserves each sensor timestamp and retries
failed uploads. Turning off the Mac stops new BLE collection, while existing
history remains in Tiger. Google measurements arrive when the cloud source syncs;
they are not per-second sensor samples. Missing data stays missing.

Use the existing Tiger database containing both the collector's `air_health`
schema and the application's accounts. From `backend` on the Mac, configured
for that same database, follow [pilot setup](pilot-setup.md) to create an owner
account if needed. Create the parent and invitations through `/setup`, then use
the consent-checked `app.health.sources link` command in
[Tiger integration](tiger-health-pipeline.md) to bind the correct collector subject
to the real parent. There is no automatic mapping to demo people. An authorized
family account sees readings only while the parent grants health sharing.

## Enable Photon after the app is ready

Initial messaging uses the console; no real SMS is sent. To activate the existing
SMS integration, configure these server-side variables:

- `MESSAGING_CHANNEL=photon`, `PHOTON_PLATFORM=sms`, `PHOTON_DELIVERY=webhook`
- `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_USER_ID`
- `PHOTON_WEBHOOK_SECRET`
- Optional `GEMINI_API_KEY` for conversation support

Set the Photon destination to
`https://YOUR-PROJECT.vercel.app/backend/webhooks/photon`. A deployment-protection
login screen must not intercept that webhook; use the public production domain
and verify provider access without removing application authentication or signature
checks. Follow [Photon setup](photon-track.md) to configure an eligible sender and
authorized, opted-in participants. Keep proactive check-ins disabled until tested.

Verify a signed inbound message, a reply and its delivery receipt with a consenting
tester. The normal parent-approval and helper-response flow is unchanged. Provider
acceptance is not handset delivery. Tiger, Photon and Gemini retain their own
account limits and charges independently of Vercel Hobby hosting.

## Verification boundaries

The API tests cover the mounted route, private responses, new sensor readings and
revoked sharing. PostgreSQL tests query synthetic sensor revisions directly. CI
builds the frontend and runs browser care-flow checks through this mounted API
with synthetic records and console messaging, alongside the existing native-host
checks. These checks do not deploy to Vercel or send messages.

Vercel's remote build, actual service routing, daemon startup, quotas, live Tiger
connectivity, event-stream reconnection and real handset delivery still require
hosted acceptance checks. The native Render and Cloudflare configurations remain
available; this change does not migrate or delete their data.
