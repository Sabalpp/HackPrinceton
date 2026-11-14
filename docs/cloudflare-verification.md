# Verification Report — Cloudflare preparation

## What was checked

The new deployment package, guided settings, same-origin proxy, container lifecycle,
secret separation, and API/worker supervision. Existing application screens,
clinical rules, contracts and database schema were not changed.

## Commands / evidence

| Check | Evidence | Result |
| --- | --- | --- |
| Deployment tests | `node --test deploy/cloudflare/test/*.test.mjs`: 14 passed | PASS |
| Supervisor tests | Python unittest discovery in `deploy/cloudflare`: 3 passed; no real processes/providers | PASS |
| Wrangler configuration | Synthetic configuration validated with AJV against the official npm `wrangler@4.162.0` Draft-07 JSON schema | PASS |
| Secret separation | Tests reject secret values in generated configuration, frontend environment, and public errors; private deployment `.env` is ignored | PASS |
| Proxy behavior | Raw webhook bytes, CSRF, cookies, separate Set-Cookie headers, bounded request bodies, streaming responses, no mutation replay | PASS |
| Deployment revisions | Config derives API revision from Git; deploy refuses modified/untracked code | PASS (code/tests; live revision unverified) |
| Python lint/format | Ruff on both new Python files | PASS |
| Dependency installation | This shell could not resolve the npm registry during npm install; Wrangler metadata/schema were fetched through the available network tool | BLOCKED |
| Docker build/start | Docker socket access returned permission denied in this session | BLOCKED |
| Cloudflare publication | No Cloudflare login/token/account ID or selected public origin available | NOT RUN |
| Live accounts/messages/health | No live deployment or provider message was created by this task | NOT RUN |

## Issues found and fixed

- Runtime secrets are separated from public variables and Docker build args.
- The API readiness probe requires a healthy standalone worker, not only a running server.
- Changed runtime settings/secrets trigger a bounded graceful container restart.
- Failed container startup clears the pending promise so a later request can retry.
- A failed API/worker/sync process stops its siblings and fails the container for recovery.
- Cloudflare build variables can override the private local settings through an explicit allowlist.
- The configurator validates HTTPS origins, TLS database connections, explicit sensor mapping,
  compute options and incompatible Photon/sleep settings before publishing.
- Initial lint/format findings were corrected before handoff.

## Remaining unverified areas

Docker images, Cloudflare resource creation, actual deployment, live worker recovery,
domain/TLS provisioning, authenticated browser flows on Cloudflare, and real Photon delivery.
The original local collector and database were not restarted or changed for this deployment task.

## Final status

PARTIAL: configuration and unit-level behavior are verified; cloud publication still
requires account settings/authentication and a working Docker/Workers Builds environment.
See [deployment instructions and choices](cloudflare-deployment.md).
