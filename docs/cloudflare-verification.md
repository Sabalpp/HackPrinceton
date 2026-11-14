# Verification Report — Cloudflare preparation

## What was checked

The new deployment package, guided settings, same-origin proxy, container lifecycle,
secret separation, and API/worker supervision. Existing application screens,
clinical rules, contracts and database schema were not changed.

## Commands / evidence

| Check | Evidence | Result |
| --- | --- | --- |
| Deployment tests | `npm --prefix deploy/cloudflare test`: 26 passed, including isolated CLI and complete gateway-to-container requests | PASS |
| Supervisor tests | Python unittest discovery in `deploy/cloudflare`: 3 passed; no real processes/providers | PASS |
| Wrangler configuration | Synthetic configuration validated with AJV against the official npm `wrangler@4.162.0` Draft-07 JSON schema | PASS |
| Secret separation | Tests reject secret values in generated configuration, frontend environment, and public errors; private deployment `.env` is ignored | PASS |
| Proxy behavior | Raw webhook bytes, CSRF, cookies, separate Set-Cookie headers, bounded request bodies, streaming responses, public redirects, edge client IP, no mutation replay | PASS |
| Pages failure regression | Install/test hooks and every CLI mode explicitly reject `CF_PAGES`; settings reject a `pages.dev` origin | PASS |
| Private configuration | Check does not create/change `.env`; temporary secret files are 0600, allowlisted and removed on success/failure | PASS |
| Publication verification | Requires matching API commit, healthy DB/worker and an HTML login page; rejects stale revisions, redirects and empty/404 output | PASS (synthetic) |
| Deployment revisions | Config derives API revision from Git; deploy refuses modified/untracked code | PASS (code/tests; live revision unverified) |
| Python lint/format | Ruff on supervisor, its tests, and synthetic Docker smoke runner | PASS |
| Dependency installation | GitHub installed Wrangler 4.162.0; its resolved dependency lock was downloaded, SHA-256 checked against the artifact metadata, reviewed and committed for `npm ci` | PASS (CI) |
| Worker and Docker builds | GitHub compiled the Worker and built both deployment images. Local Docker socket access is unavailable | PASS (CI) |
| Cloudflare publication | No Cloudflare login/token/account ID or selected public origin available | NOT RUN |
| Live accounts/messages/health | No live deployment or provider message was created by this task | NOT RUN |
| Production container acceptance | CI boots disposable PostgreSQL/API/web, verifies secure login and worker crash recovery. See the current [PR checks](https://github.com/Sabalpp/HackPrinceton/pull/6/checks) for execution results | CI gate |

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
- Secrets now upload with the Worker version; the first version no longer lacks credentials.
- Settings checks no longer save CI overrides or silently generate new ingestion credentials.
- Framework redirects stay on the public HTTPS origin and restore the backend path prefix.
- Rate limiting receives the client IP supplied by the Cloudflare edge instead of a shared proxy address.
- A provider `RangeError` is reported as temporary unavailability, not a false request-size error.
- Pages builds fail explicitly rather than succeeding with no usable static application.
- CI's first container acceptance run exposed Docker's internal-network port behavior;
  the test uses a disposable bridge with host ports bound only to loopback. PostgreSQL
  readiness now waits for the final TCP listener, not its temporary initialization server.

## Parallel review

Goal: fix the deployment and push code backed by executable checks. Root owns the
CLI, secret handling, documentation and integration; the runtime reviewer owns
gateway/lifecycle/settings tests; the CI reviewer owns image verification and
process supervision checks. Both reviews finished, with no overlapping edits.
The supervisor already trusts the isolated container proxy; the new gateway
sanitizes its forwarding headers. Real Wrangler and Docker image builds run on
GitHub, since Docker is unavailable in this session. Production startup,
authentication and recovery checks also run there.

## Remaining unverified areas

Cloudflare resource creation, actual deployment, live worker recovery,
domain/TLS provisioning, authenticated browser flows on Cloudflare, and real Photon delivery.
The original local collector and database were not restarted or changed for this deployment task.

## Final status

PARTIAL: configuration and unit-level behavior are verified; cloud publication still
requires account settings/authentication and a working Docker/Workers Builds environment.
See [deployment instructions and choices](cloudflare-deployment.md).
