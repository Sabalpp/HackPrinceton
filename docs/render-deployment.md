# Deploy FarCare on Render

[render.yaml](../render.yaml) runs the existing API, durable worker and Next.js dashboard
as three native services. Docker is not needed for this route. Tiger stays the existing
external PostgreSQL database; no new database, queue or storage service is provisioned.
The Mac collector continues sending real Fitbit and Google Health data to Tiger.

## 1. Import the Blueprint

After the deployment files reach `main`, open Render → **New → Blueprint**, connect
`Sabalpp/HackPrinceton`, choose **main**, and use the repository-root `render.yaml`.
The Blueprint selects three paid `0.5c-512mb` services in Virginia. Review Render's displayed
price and region before creating them. Change the region to one appropriate for your Tiger
service if needed. No cloud resource is created by merely committing this file.

Render supports the native runtimes, service references and CI-gated deployment settings used
here. [Blueprint reference](https://render.com/docs/blueprint-spec)

Enter these two values for **farcare-api**:

| Setting | Value |
| --- | --- |
| `DATABASE_URL` | Your existing Tiger PostgreSQL URI, including `sslmode=require`; keep it in Render's secret configuration |
| `CORS_ORIGINS` | The exact HTTPS dashboard origin, such as `https://your-farcare-web.onrender.com`; no trailing path or wildcard |

Use the web service's actual assigned URL, or your chosen dashboard custom domain. If Render
has not assigned a URL when the form opens, `https://pending.invalid` lets the API start
but does not authorize dashboard login. Replace it on the API with the exact assigned HTTPS
web origin immediately after creation, then sync the Blueprint and redeploy the API/worker.
Do not put a
database URI, provider key or real phone number in `render.yaml` or a browser variable.

The API and worker share a generated ingestion key and production settings through the
`farcare-runtime` environment group. The worker receives the API's Tiger URI and CORS value
through service references. The web service receives only the API's public HTTPS origin,
not the backend environment group. That API origin is available during the Next.js build,
when its forwarding route is generated. Rebuild the web service if the API origin changes.
Service references refresh on a Blueprint sync.
[Render service references](https://render.com/docs/blueprint-spec#referencing-service-properties)

## 2. Verify the app and connect the wearer

Open the **farcare-web** URL at `/login`. Browser API requests use `/backend`; Next.js forwards
them to `FARCARE_API_ORIGIN`. This keeps browser sessions on the dashboard origin. Render
supplies the API's HTTPS URL through `RENDER_EXTERNAL_URL` and the deployed revision through
`RENDER_GIT_COMMIT`. [Render environment variables](https://render.com/docs/environment-variables)

Confirm all three services are running. Then check the API:

```bash
curl -fsS https://YOUR-API.onrender.com/health
curl -fsS https://YOUR-API.onrender.com/ready
```

`/health` must report `environment: production`, `database: ok`, `demo_mode: false` and the
expected Git SHA. `/ready` must return HTTP 200 and `ready: true`; that checks the worker as
well as the database. The Blueprint uses `/health` for API boot so it does not wait on a
worker that is still being created. A green Render deployment alone does not prove worker
readiness or delivery to a phone.

In the API service's **Shell**, create the first authorized family account:

```bash
.venv/bin/python -m app.manage_accounts create-owner --email OWNER_EMAIL --name "Family member"
```

The command prompts for the password. Sign in, create the parent's profile and invite the
parent/helper through `/setup`. The parent controls sharing. Registration and sample data
are disabled in this Blueprint. Follow [the invited-pilot setup](pilot-setup.md) for account
and consent details.

After verifying the wearer's identity and permission, link their existing collector subject
from the API shell:

```bash
.venv/bin/python -m app.health.sources link \
  --parent-id REAL_PARENT_ID \
  --actor-id REGISTERED_ACCOUNT_OWNER_ID \
  --subject-id COLLECTOR_WEARER_SUBJECT_ID \
  --consent-confirmed
```

The API and worker must access the same Tiger database and `air_health` schema as the Mac
collector. This command rejects demo profiles and conflicting wearer mappings. Verify the
dashboard's timestamps and source labels against new collected readings. Google metrics
appear only after successful cloud synchronization. [Collector and Tiger details](tiger-health-pipeline.md)

## 3. Enable real Photon messaging and Gemini

The initial deployment uses **console messaging** and deterministic conversational fallback.
It can run without provider keys, but it does not send real SMS. There is no blank-secret
prompt blocking the initial deployment.

When your Photon SMS sender and authorized participants are ready, add the following secrets
once to Render's **farcare-runtime** environment group. Both API and worker already use it:

| Secret | Purpose |
| --- | --- |
| `PHOTON_API_KEY` | Photon project API key |
| `PHOTON_PROJECT_ID` | Matching project ID |
| `PHOTON_AGENT_USER_ID` | Eligible Photon SMS sender user ID |
| `PHOTON_WEBHOOK_SECRET` | Signing secret for this webhook destination |
| `GEMINI_API_KEY` | Optional Gemini key; the existing fallback remains available without it |

Configure Photon to send its signed webhook events to:

```text
https://YOUR-API.onrender.com/webhooks/photon
```

Then change the existing **farcare-runtime** entries in `render.yaml` to
`MESSAGING_CHANNEL: photon` and `PHOTON_DELIVERY: webhook`, merge through the normal PR/CI
flow, sync the Blueprint and deploy the API and worker. `PHOTON_PLATFORM` is already `sms`.
Keep secrets out of YAML. Storing the non-secret mode change in the Blueprint prevents a
later sync from restoring console mode. Manually added secret keys are omitted from YAML
and preserved by Render. [Environment configuration](https://render.com/docs/blueprint-spec#setting-environment-variables)

Keep automatic proactive check-ins off until the consenting test family's routing is
verified. Use the existing [Photon setup guide](photon-track.md) to register participant
handles and confirm sender eligibility. Test an actual inbound SMS, response and signed
delivery receipt. HTTP 202 means accepted, not delivered. Web accounts do not automatically
authorize SMS, and no test message is sent by this Blueprint.

## Build and start settings

These are already in `render.yaml`; do not create duplicate services manually:

| Service | Type / runtime | Root | Build | Start |
| --- | --- | --- | --- | --- |
| `farcare-api` | Web / Python 3.12 | `apps/api` | `uv sync --frozen --no-dev --no-install-project` | `.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 5` |
| `farcare-worker` | Background worker / Python 3.12 | `apps/api` | Same API build | `.venv/bin/python -m app.worker` |
| `farcare-web` | Web / Node 24 | `apps/web` | `npm ci && npm run build` | `npm run start -- --hostname 0.0.0.0 --port $PORT` |

The checked-in Python and Node dependency locks remain authoritative. The existing
`apps/api/.python-version` selects Python 3.12; no runtime upgrade is needed.
[Render Python version selection](https://render.com/docs/python-version)

Render recognizes `uv.lock` in the API service root and supports an explicit `UV_VERSION`.
[Render uv support](https://render.com/docs/uv-version)

The worker is a continuously running process and has no public HTTP port. Do not replace it
with a scheduled job or enable a second API-owned background scheduler.
[Render background workers](https://render.com/docs/background-workers)

## CI and deployment checks

All three services track `main` with `autoDeployTrigger: checksPass`. Keep normal PR checks
and branch reviews. CI verifies source and builds before Render deploys. Hosted smoke checks
run separately after deployment, so a job waiting for a new live revision does not block
Render from deploying that revision. Once Render reports the services live, use the manual
`/health` and `/ready` checks above, then run **Actions → Deploy → Run workflow** on `main`.
Set the repository variables `API_BASE_URL` and `WEB_BASE_URL` to the actual HTTPS service
origins to enable those manual hosted smoke checks. They are skipped on automatic pushes.

This file is a deployable configuration, not evidence that the services have been created.
Account permissions, billing, the live Tiger connection, HTTPS routing, worker readiness and
real SMS delivery require verification on the deployed services.

The existing Docker and Cloudflare files remain because their workflows still reference
them. They are an optional deployment route, not a requirement for this native Render setup.
