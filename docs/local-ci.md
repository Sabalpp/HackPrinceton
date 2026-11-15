# CI and dashboard verification

Existing check names remain unchanged, with two additional collector checks for Python 3.9 and 3.12. The **Web (lint, typecheck, build)** check runs on Ubuntu 24.04, matching the pinned browser driver's supported OS. It starts the actual FastAPI app with temporary synthetic SQLite data, starts the production Next.js build, and runs Chromium through the dashboard's `/backend` proxy used by native hosting. It checks session cookies, CSRF mutations, authenticated `/api/me` and overview responses, a live SSE connection, Health and Care Circle navigation, and browser errors.

`NEXT_PUBLIC_USE_MOCKS=false` means this checks the real API integration. All people and health observations used by this CI job are synthetic. Photon, Gemini, proactive messages, and background scheduling are disabled. The test starts and stops only its own processes and never reads the private local health exports or cloud credentials.

## Run the same check locally

Use Node 22, Python 3.12, and uv. The command requires permission to listen on loopback ports 18000 and 13000. If your agent sandbox blocks listening, run it in Terminal or let GitHub Actions execute it; a syntax check alone is not a browser pass.

From the repository root:

```bash
uv sync --frozen --project backend
npm ci --prefix frontend
NEXT_PUBLIC_API_URL=/backend FARCARE_API_ORIGIN=http://127.0.0.1:18000 NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_LOCAL_PREVIEW=false npm --prefix frontend run build
export PLAYWRIGHT_INSTALL_DIR="$(mktemp -d /tmp/farcare-playwright.XXXXXX)"
npm install --ignore-scripts --no-audit --no-fund --package-lock=false --prefix "$PLAYWRIGHT_INSTALL_DIR" playwright@1.56.1
node "$PLAYWRIGHT_INSTALL_DIR/node_modules/playwright/cli.js" install --with-deps chromium
node --check scripts/dashboard_smoke.mjs
SMOKE_API_PROXY=true node scripts/dashboard_smoke.mjs
```

Playwright is pinned to [Microsoft's published 1.56.1 package](https://github.com/microsoft/playwright/blob/v1.56.1/packages/playwright/package.json). Browser installation follows the [official CI instructions](https://playwright.dev/docs/ci). It is installed outside the app dependency tree, so the smoke job does not modify the app's package manifest or lockfile.

CI uploads `synthetic-dashboard-smoke` with Today/Health/Care Circle screenshots, API/web logs, and `result.json`, retaining them for seven days. These artifacts contain synthetic fixture data only. The public viewer token used here is a documented development fixture, not a production family credential; do not put a real private-family token in `NEXT_PUBLIC_VIEWER_TOKEN`.

## What deployment verification proves

The backend deployment gate requires `/health` to report healthy application/database status **and the exact GitHub commit being deployed**. Missing or old commit values keep retrying and eventually fail. Set `GIT_COMMIT`, `RENDER_GIT_COMMIT`, or `RAILWAY_GIT_COMMIT_SHA` on the host.

The hosted frontend check currently proves HTTP availability only. It does not identify the hosted frontend commit or prove that the hosted page is connected to the intended backend. The CI browser check proves the checked-out build works with the synthetic backend. Hosted frontend revision verification requires a build-time revision marker or a deployment-specific URL and is not claimed by this workflow.

The Deploy workflow re-runs CI on pushes to main. Hosted smoke checks and the optional
deploy hook run only when that workflow is manually dispatched after deployment.
This lets Render's `checksPass` trigger finish waiting for CI before it starts a new
deployment; waiting for the new live revision in that same automatic check would
deadlock. Configure the live URL variables only for the manual verification step.

No workflow bypasses branch protection. The host's Git integration publishes the
application after checks pass. Container image checks remain for the existing
Cloudflare deployment, but the native Render setup requires no Docker installation.
