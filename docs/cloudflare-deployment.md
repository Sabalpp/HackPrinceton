# Deploy FarCare on Cloudflare

This configuration hosts the complete app using Cloudflare Workers + Containers.
The frontend keeps its existing Next.js runtime; the Python API and standalone
care worker run as separate supervised processes in a second container. Tiger
remains the durable PostgreSQL database. No migration to D1 is required.

The gateway uses one HTTPS origin: browser requests under `/backend/` reach
FastAPI, and other paths reach Next.js. Cookies, CSRF headers, raw Photon webhook
bytes, and streaming SSE responses are preserved. Shared caching is disabled.
Only the API container receives database/provider secrets and outbound Internet
access. Frontend builds use `NEXT_PUBLIC_API_URL=/backend` and mocks disabled.

## Choose the settings

Run `npm run configure` in `deploy/cloudflare` for a guided terminal setup, or edit
its ignored `.env`. The example file lists all supported parameters.

| Setting | Default / choice | Effect |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Required; dashboard account ID | Account that owns the deployment |
| `CLOUDFLARE_API_TOKEN` | Optional with `wrangler login` | Deployment credential; never a browser variable |
| `WORKER_NAME` | `farcare` | Cloudflare Worker name |
| `PUBLIC_ORIGIN` | Required, exact HTTPS URL without trailing slash | Browser origin, CSRF policy and webhook base |
| `CUSTOM_DOMAIN` | `false` for `https://farcare.<your-subdomain>.workers.dev`; `true` for your own hostname | Custom domains must already belong to your Cloudflare account; this does not buy a domain |
| `API_INSTANCE_TYPE` / `WEB_INSTANCE_TYPE` | `basic` / `basic` | Each starts with 1 GiB; `standard-1` gives 4 GiB; `standard-2` gives 6 GiB |
| `CARE_AGENT_MODE` | `always_on` | Minute cron keeps/restarts the API/worker container. `on_demand` sleeps after five idle minutes and is only for demonstrations without active care or Photon |
| `MESSAGING_CHANNEL` | `console` or `photon` | Console mode labels provider messages simulated; Photon requires all three provider fields and participant opt-in |
| `SESSION_HOURS` | `24`; range 1–168 | Browser session lifetime |
| `PROACTIVE_CHECKINS_ENABLED` | `false` | Turn on only after validating opt-in, delivery and each parent's timezone |
| `TIGER_SYNC_ENABLED` | `false` | Enable after creating the parent and setting the two IDs below |
| `TIGER_SUBJECT_ID` / `TIGER_PARENT_ID` | Upstream subject / actual FarCare parent ID | Explicit authorized mapping; never guess from a sample person |
| `TIGER_SYNC_SECONDS` | `60` | Refresh app daily projections; raw BLE still arrives through the existing local one-second uploader |
| `TIGER_LOOKBACK_DAYS` | `35` | Historical projection window |

Worker batch size, retry bounds, heartbeat tolerance and the Gemini model are
also in the example. The configurator validates all values before deployment.

Cloudflare Containers require the Workers Paid plan and charge separately for
running compute. The plan fee is not the full hosting cost. Always-on API compute
continues while no browser is open; the web container can sleep. Size and workload
affect the bill. Review [Cloudflare's current pricing](https://developers.cloudflare.com/containers/platform/pricing/).
No billing plan, domain purchase or resource deployment is performed by configure/check.

## Secrets

| Secret | Where it comes from |
| --- | --- |
| `DATABASE_URL` | Existing Tiger PostgreSQL URL, with `sslmode=require` or stronger |
| `INGEST_API_KEY` | Existing private ingestion key; the configurator generates one if absent |
| `PHOTON_API_KEY`, `PHOTON_PROJECT_ID` | Photon 2.0 project |
| `PHOTON_WEBHOOK_SECRET` | Signing secret for the configured Photon webhook; not the API key |
| `GEMINI_API_KEY` | Existing Gemini key; optional if using deterministic fallback |

`npm run configure -- --import-local` copies missing application values from
`apps/api/.env` to the private deployment file without printing them. It does not
upload anything. The file is mode 0600 and ignored by Git. Cloudflare credentials
stay with the operator and are not forwarded to a container. API credentials
become Worker secrets via stdin at deploy time and never become Docker build args.

Do not paste the `.env` into GitHub, a chat, or a frontend environment variable.
Changing a runtime setting or secret updates a fingerprint; a running container
gets a bounded graceful restart so it actually receives the new values.

## Local deployment

Requirements: Node 22.12+ (24 recommended), Docker running with linux/amd64 build
support, a Cloudflare account with Containers available, and an available Tiger
service. An expired Tiger trial can pause the database independently of hosting.

```sh
cd deploy/cloudflare
npm install --no-package-lock
npm run configure -- --import-local
npm run check
npm test
npx wrangler login
```

Review and commit the deployment files on your feature branch before publishing.
The deploy script refuses a dirty Git tree so `/backend/health.commit` identifies
the code actually shipped. It builds/uploads both images, publishes the Worker,
uploads secrets, then waits for `/login`, the expected API revision and database,
and `/backend/ready` (including the care-worker heartbeat) to pass:

```sh
npm run deploy
```

First provisioning can take several minutes. A published Worker URL alone does
not prove that its containers, database, or care worker are ready. A failure after
publishing is reported as unverified; it is not silently treated as success.

Photon webhook URL: `PUBLIC_ORIGIN/backend/webhooks/photon`. Register this with
Photon only after selecting Photon mode and supplying the signing secret. Each
shared-line participant still needs an assignment and must message the line first.
No deployment command creates a Photon assignment, purchases a line, or sends a
test message. Gemini does not decide emergency escalation.

## Cloudflare dashboard / automatic deploy settings

Use **Workers & Pages → Create → Import repository**, choose
`Sabalpp/HackPrinceton`, then configure Workers Builds. This is a Workers/Containers
deployment, not a static Pages upload.

| Dashboard field | Value |
| --- | --- |
| Production branch | `main` after the reviewed deployment change is merged |
| Root directory | Repository root (`/`), since images need both apps |
| Build command | `npm --prefix deploy/cloudflare install --no-package-lock && npm --prefix deploy/cloudflare test && python3 -m unittest discover -s deploy/cloudflare -p 'test_*.py'` |
| Deploy command | `npm --prefix deploy/cloudflare run deploy` |
| Build environment | Node 24; allow Docker image builds |
| Public build variables | Non-secret settings listed in `.env.example` |
| Build secrets | The six app secrets above, plus deployment token if using one |
| Feature-branch deployment | Disable for the production Worker; use a separate staging Worker/database to test |

The script reads allowlisted environment variables as overrides, so CI needs no
committed `.env`. Cloudflare's build environment can build the Dockerfiles; see
[deploying Containers with Workers Builds](https://developers.cloudflare.com/containers/guides/deploy/).
Do not change a production Worker's feature-branch command to a full deploy.

Keep the existing GitHub required checks. Set repository variables `WEB_BASE_URL`
to `PUBLIC_ORIGIN` and `API_BASE_URL` to `PUBLIC_ORIGIN/backend` for the existing
deployment smoke checks. GitHub's existing workflow checks the deployed revision;
Cloudflare Workers Builds performs the actual publication.

## First account and sensor setup

Public registration, demo bearer tokens and sample-person seeding remain disabled.
Provision the first family owner using the existing operator CLI against the same
database. A local Python 3.12 environment is enough; it connects directly to Tiger
and prompts for the password instead of putting it in command arguments:

```sh
cd apps/api
.venv/bin/python -m app.manage_accounts create-owner --email YOUR_EMAIL --name YOUR_NAME
```

Use your private local configuration for the same database; do not run this
against the saved-data SQLite preview. Log in at `PUBLIC_ORIGIN/login`, create
the parent, complete sharing permissions, and invite family/helper accounts.
Then set the real `TIGER_PARENT_ID`, enable Tiger sync, and redeploy. Roles use
`/parent`, `/family` and `/respond`, with server-side permissions as before.

The Fitbit BLE collector still needs a Mac/phone near the band. Cloudflare cannot
connect to a Bluetooth radio in your house. Keep the existing recorder and the
documented durable one-second uploader running there; they write to Tiger.
Google refresh also stays with the existing authenticated connector. Neither
Google OAuth tokens nor recorded health JSONL files are copied into these images.

## Operations and verification limits

- No high-availability or uninterrupted monitoring guarantee: initial cold starts,
  maintenance, database outages, and up-to-a-minute restarts still exist.
- Deployment validates the API, database, revision and worker readiness. Account
  isolation, parent consent and actual message receipt still need live acceptance
  checks with consenting testers.
- Application data is durable in Tiger; container disk is ephemeral. Back up Tiger.
- Request and container logs are disabled by default to avoid exporting identifiers,
  provider payloads and health records to logs. Enable scoped diagnostics only when
  needed; inspect both the worker queue and heartbeats through authenticated tools.
- Docker and live Cloudflare publication must be verified on a connected build
  host. The local preparation report records what was actually exercised.

References: [Container startup and routing](https://developers.cloudflare.com/containers/get-started/),
[runtime environment and secrets](https://developers.cloudflare.com/containers/examples/env-vars-and-secrets/),
[Durable Object Container API](https://developers.cloudflare.com/containers/api/durable-object-container/).
