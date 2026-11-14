# AGENTS.md — engineering rules for every human and Claude session

Three developers run parallel Claude Code sessions on this repo. These rules keep us from
breaking each other. Context lives in [CLAUDE.md](CLAUDE.md).

## The 17 rules

1. Read `CLAUDE.md` and this file before editing anything.
2. Read your developer file in `docs/agents/` and follow its priorities.
3. Work only on your assigned branch (below). Never commit directly to `main`.
4. Respect module ownership (table below).
5. Don't modify another developer's files without coordinating with them first.
6. Use the shared schemas and generated types; never redefine contract shapes by hand.
7. Never silently change an API contract (see "Changing a shared contract").
8. Never overwrite, revert, or force-push over another developer's work.
9. Keep changes small and focused: one concern per PR.
10. Run the relevant tests before committing (commands below).
11. Open pull requests into `main`.
12. Resolve failing checks before merging (see "When CI fails").
13. Never commit credentials, tokens, real phone numbers or personal data. The repo is public.
14. Never fabricate a completed integration. If it's stubbed or simulated, say so in code and docs.
15. Label simulated data `simulated` everywhere (payloads, UI, messages).
16. Don't add dependencies without a clear need. Note why in the PR.
17. Record meaningful architecture decisions in `CLAUDE.md` ("Key technical decisions") or
    `docs/architecture.md`.

## Ownership

| Path | Owner | Others may |
| --- | --- | --- |
| `apps/api/**` (except below) | Sumit | open an issue / ask first |
| `apps/api/app/health/**`, `apps/api/tests/test_health*.py` | Sabal | ask first |
| `apps/api/app/models.py` → `HealthObservationRow` | Sabal (coordinate with Sumit) | ask first |
| `apps/web/**` | Saksham | ask first |
| `apps/api/app/schemas/contracts.py`, `packages/contracts/**` | Shared (Sumit coordinates) | contract-change process |
| `.github/**`, `scripts/setup-github.sh` | Sumit (+ repo owner review) | ask first |
| `docs/**`, `CLAUDE.md`, `AGENTS.md` | Everyone (own sections) | small fixes OK |

CODEOWNERS enforces review for `.github/` and the contracts.

## Branches

| Developer | Branch |
| --- | --- |
| Sumit | `feature/sumit-core` |
| Saksham | `feature/saksham-ui` |
| Sabal | `feature/sabal-fitbit` |

Flow: commit on your branch → push → open PR into `main` → CI runs → a teammate approves →
auto-merge squashes it in once all required checks pass.

**After your PR is squash-merged**, run `scripts/sync-after-merge.sh` before continuing. Squash
merges rewrite history, so the old branch would conflict. The script refuses if you have commits
that weren't part of the merged PR.

## Commands (run before every commit)

```bash
# Backend (apps/api)
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
# Frontend (apps/web)
npm run lint && npm run typecheck && npm run build
```

## Changing a shared contract

Contracts are the Pydantic models in `apps/api/app/schemas/contracts.py`. Everything else is
generated from them.

1. Tell the team which field/endpoint changes and why. Prefer **additive** changes: new
   optional fields, new endpoints. Renames and removals need everyone's agreement.
2. Edit `contracts.py`. Update `packages/contracts/fixtures/*.json` if examples change.
3. `cd apps/api && uv run python scripts/export_contracts.py`
4. `cd apps/web && npm run gen:api && npm run typecheck`. Fix `lib/mocks.ts` if it no longer
   type-checks.
5. Update `docs/api-contracts.md`.
6. One PR containing the contract, the regenerated files, and the consumer fixes.

CI fails if `packages/contracts/` or `apps/web/lib/api-types.ts` are stale.

## When CI fails (the correction loop)

1. Open the failed job: `gh pr checks` then `gh run view <run-id> --log-failed`.
2. Find the failing command and read the exact error. Reproduce it locally with the same command.
3. Fix the root cause on **your feature branch**. Don't skip tests, loosen lint rules, or edit
   workflows to get green.
4. Re-run the local commands, commit, push. CI re-runs automatically.
5. Repeat until green. If the failure is in someone else's area, tell them; don't patch their code.

Agents may run this loop on their own branch. Agents must **not**: push to `main`, approve PRs,
modify `.github/workflows/` without the integration owner, bypass rulesets, or disable checks.
GitHub Actions does not auto-fix code. Fixes come from a developer or their agent.

## Safety and privacy guardrails (code review checks these)

- Emergency escalation never depends on an LLM decision. Deterministic rules run first.
- Ordinary help requests contact nobody until the parent replies YES.
- Responders receive only: the parent's first name, the task, the time, and task details.
- Children see health data and check-ins only if the parent's `FamilyLink` permissions allow it.
  Enforce this server-side.
- Never trust a client-supplied `parent_id`. Always go through `auth.resolve_parent_access`.
- Webhooks: verify signatures on the raw body, dedupe redeliveries.
- No medical claims, no auto-dialing emergency services.
