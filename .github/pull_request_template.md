## What & why

<!-- One or two sentences. Link the task from docs/agents/<NAME>.md if relevant. -->

## Checklist

- [ ] I stayed within my owned paths (see AGENTS.md) or coordinated with the owner
- [ ] Tests added/updated and passing locally (`uv run pytest` / `npm run typecheck && npm run build`)
- [ ] Contract change? Re-exported (`uv run python scripts/export_contracts.py`), regenerated web types (`npm run gen:api`), updated docs/api-contracts.md, and told the team
- [ ] No secrets, real phone numbers, or personal data committed
- [ ] Simulated data is labeled `simulated`; no medical/diagnostic claims added
