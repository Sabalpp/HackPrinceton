# Verification Report

## What was checked

- Separate parent, family and helper accounts; invitation consent and role boundaries.
- Routine approval/acceptance/completion, urgent-contact grants, revocation and replay.
- Cuff BP provenance, provisional references, qualified-day learning and safety guidance.
- Durable processing, retries, queued-message consent and source-data handling.
- Desktop family and mobile parent/helper browser workflow prepared for CI.

## Commands / evidence

| Check | Evidence | Result |
| --- | --- | --- |
| API lint/format | Ruff check and format check, 79 files | PASS |
| SQLite suite | `pytest -q`: 384 passed, one PostgreSQL-only test skipped | PASS |
| Personal algorithm cases | 81 synthetic tests with primary-source evidence notes | PASS |
| Account/role integration | 44 synthetic endpoint tests | PASS |
| Frontend lint/types | ESLint, Next route type generation, TypeScript | PASS |
| Contract generation | OpenAPI export and generated TypeScript refreshed together | PASS |
| Deployment template | Compose configuration validation; no resources started | PASS |
| PostgreSQL, build, browser, image, secret scan | Required GitHub CI on this feature revision | PENDING |

## Issues found and fixed

Cookie-session revocation and changing sharing now affect live SSE batches. Queued
messages recheck grants and assignments. Replaying a completed decline works after
reassignment, while new unauthorized actions fail. New proposed ordinary helpers need
fresh approval. Emergency-policy tasks remain distinct from per-request approval.
Demo BP cannot create live readings or real urgent alerts. Registered helpers can
receive tasks in the app without fabricated phone handles. Validation errors omit
submitted password and invitation input values.

## Remaining unverified areas

Live Photon/Gemini credentials, provider delivery/receipts, public HTTPS hosting,
operator identity verification, backup restoration and clinical validation have not
been established by automated tests. The local preview on port 8000 is still running
the previous API process and needs a normal-terminal restart after this update.

## Final status

PARTIAL — local code checks pass; hosted CI and real deployment gates remain.
