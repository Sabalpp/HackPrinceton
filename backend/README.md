# FarCare backend

This folder handles accounts, health records, care requests and messaging. The API
uses FastAPI and Tiger/PostgreSQL. The care worker runs separately from API requests.
Sumit owns the API and workflows; Sabal owns health integration and the collector.
See [team rules](../AGENTS.md).

| Start here | What it does |
| --- | --- |
| `app/main.py` | Starts the API and connects its routes |
| `app/routes/` | HTTP endpoints used by the frontend and provider webhooks |
| `app/auth.py`, `app/models.py` | Accounts, sessions, sharing permissions and database models |
| `app/health/` | Reads Tiger measurements and builds consented health views |
| `app/services/` | Care requests, approvals, check-ins and escalation rules |
| `app/integrations/` | Photon messaging and Gemini conversation adapters |
| `app/worker.py` | Runs queued work, retries and care timers |
| `app/schemas/contracts.py` | Defines the shared API payloads |
| `tests/` | API, privacy, health and care-workflow checks |
| `collector/` | Separate local wearable collector; see below |

From this folder, run the API with Python 3.12:

```bash
test -f .env || cp .env.example .env
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --reload
```

For a deployment, also run `uv run python -m app.worker` as a separate process and
use the production settings in [the runbook](../docs/production-runbook.md).
Credentials belong in this folder's private `.env` or host environment settings.

The local collector uses its own Python 3.9+ environment. Its code is organized as:

```text
collector/farcare_collector/integrations/   Fitbit BLE, Google Health, Tiger and SQL
collector/farcare_collector/services/       Durable ingestion and delivery loop
collector/farcare_collector/health/         Normalization and descriptive reports
collector/tests/                           Collector checks
collector/runtime/                         Managed-session Node fallback
```

It runs on the Mac near the watch and sends real measurements to Tiger. Its private
recordings, tokens and outbox remain outside source via `FARCARE_WORKSPACE`.
It is excluded from the API image and uses a separate dependency file.
[Collector commands and data paths](../docs/codebase-organization.md#running-the-existing-tools)

The flow is: **collector → Tiger → authenticated API → frontend**. The care worker
uses the same data and permissions for configured messaging. Deploy the full Vercel
project from the repository root using [these settings](../docs/vercel-deployment.md).
