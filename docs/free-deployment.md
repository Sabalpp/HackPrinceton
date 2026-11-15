# Free hackathon deployment

[deploy/render/free.yaml](../deploy/render/free.yaml) runs the existing dashboard,
API and care worker in **one free Render web service**, without Docker. Tiger
remains the external database. The collector still runs on the Mac near the watch.
No application features, password hashing or permissions are replaced.

**This is a sleeping demo host, not an always-on care service.** Render Free sleeps
after 15 minutes without inbound traffic. While asleep, the worker cannot process
messages, run timers or check health trends. A new request wakes the service;
cold starts can delay a Photon webhook. Check `/backend/ready` before demonstrating
SMS. PostgreSQL retains queued work across restarts, but that does not guarantee
timely delivery. Do not use artificial keep-alive traffic as an availability plan.
[Render Free limits](https://render.com/docs/free)

Hosting at no charge does not make Tiger, Gemini or SMS free. Their quotas, credit
balances and account status remain separate. No provider or billing change is
made by committing this Blueprint.

## Create the service

1. Merge the deployment PR through the normal checks into `main`.
2. In Render, choose **New → Blueprint**, connect `Sabalpp/HackPrinceton`, and select
   **main**. Set **Blueprint Path** to **`deploy/render/free.yaml`**.
3. Confirm that Render proposes exactly **one Free web service**. The root
   `render.yaml` is a separate paid, three-service option; do not select it for this demo.
4. Set `DATABASE_URL` to the existing Tiger PostgreSQL URI with TLS enabled. Enter
   it in Render's secret form, never in Git or a `NEXT_PUBLIC_` variable.
5. Deploy, then open the assigned HTTPS URL at `/login`.

The automatic `RENDER_EXTERNAL_URL` supplies the browser's allowed origin. For a
custom domain, set `CORS_ORIGINS` to its exact HTTPS origin. Next.js forwards
`/backend/*` to the private API on `127.0.0.1:8000`; only the dashboard binds the
public port. The API initializes the database before the worker starts. If any
process exits, the supervisor stops the others and exits with an error so Render
can restart the service.

Render provides Node/npm alongside its native Python runtime. Both application
lockfiles are used during the build. [Native runtimes](https://render.com/docs/native-runtimes)

If configuring a Web Service manually instead of a Blueprint:

| Setting | Value |
| --- | --- |
| Branch | `main` |
| Root directory | Repository root; leave blank |
| Runtime | Python 3 |
| Instance type | Free |
| Build command | `bash deploy/render/build.sh` |
| Start command | `backend/.venv/bin/python deploy/render/run.py` |
| Health check path | `/backend/ready` |

Copy the non-secret environment settings from `deploy/render/free.yaml`, provide
the Tiger secret and generate a strong `INGEST_API_KEY`. The Blueprint handles
these settings automatically and is less error-prone.

Automatic deployments wait for checks. If the unused Cloudflare Containers build
still fails on `main`, disconnect that old Cloudflare build integration before
switching hosts. Keep GitHub Actions tests enabled; do not bypass them.

## Verify accounts and real data

```bash
curl -fsS https://YOUR-SERVICE.onrender.com/backend/health
curl -fsS https://YOUR-SERVICE.onrender.com/backend/ready
```

Require the expected Git revision, `environment: production`, `database: ok`,
`demo_mode: false`, and readiness HTTP 200. The readiness endpoint checks the
worker. A successful build alone is not evidence of a live database or SMS delivery.

The initial configuration is an invited pilot. Existing accounts work when this
deployment uses their existing Tiger database. If there is no family account,
use `app.manage_accounts create-owner` from your Mac with the API configured for
that same Tiger database; see [pilot setup](pilot-setup.md). The command prompts
privately for a password. **Render Free has no interactive Shell**, so do not
follow paid-host instructions that require one. If public family registration is
intended, deliberately change `REGISTRATION_ENABLED` to `true` in this Blueprint
and redeploy; it does not grant access to anyone else's family.

Invite the parent and helper using `/setup`. Link the correct collector subject
to the real parent only after identity and consent verification, using the
existing `app.health.sources` command from the Mac against the same Tiger database.
See [Tiger integration](tiger-health-pipeline.md). Readings should show source
timestamps and freshness. Missing Google cloud measurements remain missing.

## Activate Photon SMS

The default uses console messaging and sends **no real SMS**. Keep it that way
until the sender and consenting participants are configured. Add these secrets
in Render's Environment settings:

| Name | Purpose |
| --- | --- |
| `PHOTON_API_KEY` | Photon project key |
| `PHOTON_PROJECT_ID` | Matching project |
| `PHOTON_AGENT_USER_ID` | Provisioned, eligible SMS sender |
| `PHOTON_WEBHOOK_SECRET` | Signing secret for this webhook destination |
| `GEMINI_API_KEY` | Optional Gemini conversation support |

Set the Photon webhook URL to:

```text
https://YOUR-SERVICE.onrender.com/backend/webhooks/photon
```

Update the Blueprint's `MESSAGING_CHANNEL` to `photon` and `PHOTON_DELIVERY` to
`webhook`, then deploy through the normal PR/CI flow. Keep `PHOTON_PLATFORM: sms`.
Secrets added in the dashboard stay out of source. Register authorized participant
handles as described in [the Photon guide](photon-track.md). Account invitations
do not imply SMS consent. Keep proactive check-ins disabled until routing is verified.

Verify a real inbound message, the agent's reply and its signed delivery receipt
with a consenting tester. Then exercise the existing approval flow: parent asks
for help → parent confirms YES → authorized neighbor receives the request → neighbor
accepts → parent and permitted family see the update. HTTP 202 is acceptance by
Photon, not proof of delivery to the phone.

Photon's free base plan does not include unlimited SMS. Number, carrier, usage
and registration charges can apply; check eligible welcome credit in the account.
[Photon billing](https://photon.codes/docs/beta/plans-and-billing)

## Verification boundaries

CI exercises this supervisor with synthetic SQLite data and console messaging,
the production Next.js build, and browser care-flow tests. API tests also run on
PostgreSQL. Actual Render resource limits, cold starts, the live Tiger connection
and handset delivery must be checked after deployment. The three processes share
the free service's memory; do not infer capacity for multiple families from CI.

The existing Cloudflare Containers and paid native Render deployments remain
available. A direct free Python Worker was tested and lacks the `hashlib.scrypt`
function required by existing account passwords; moving this backend there would
require an explicit authentication and background-work migration.
