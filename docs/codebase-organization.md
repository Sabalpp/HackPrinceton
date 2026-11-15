# Codebase organization

FarCare keeps the existing applications, collector and experiments in separate places.
This cleanup moves source and fixes references; it does not change health algorithms,
message workflows, API contracts or dashboard behavior.

## Repository and private workspace

The Git repository is the directory containing `apps/`, `packages/`, `scripts/` and this
`docs/` directory. On the original Mac it is checked out as `app_repo` inside a larger
private workspace. `ui_repo` is a separate checkout and is not a second source of truth
for this repository.

`FARCARE_WORKSPACE` selects the private data directory used by the collector and local
launchers. Without an override, the `app_repo` checkout uses its parent directory, keeping
the existing recordings and caches in place. Other checkout names default to the repository
root; use an explicit external workspace for new setups. Do not point a test at the live
outbox or production database.

The workspace contains `.google-health/`, `api_data/`, `heart_rate.jsonl`, `services.json`,
`ble_status.json`, `pipeline_data/` and generated reports. It also holds the existing local
Python environment. Credentials, health history, downloaded Android SDKs, emulator images,
third-party research checkouts and temporary verification artifacts are not source files.
The cleanup neither deletes nor publishes them.

## Code ownership and locations

| Location | Responsibility | Owner |
| --- | --- | --- |
| `apps/api/app/` | FastAPI, permissions, care workflows, Photon SMS/iMessage, Gemini and persistence | Sumit; Sabal owns `health/` |
| `apps/web/` | Next.js parent, family and helper dashboards | Saksham |
| `apps/collector/farcare_collector/integrations/` | BLE/Google connectors and Tiger delivery; SQL under `sql/` | Sabal |
| `apps/collector/farcare_collector/health/` | Normalization, descriptive features and offline reports | Sabal |
| `apps/collector/farcare_collector/services/` | Durable collector orchestration | Sabal |
| `apps/collector/tests/` | Synthetic collector Python and Node tests | Sabal |
| `apps/collector/runtime/` | Existing managed-Node publisher and Google refresh fallback | Sabal |
| `scripts/local/` | Mac app launcher, private preview setup and offline maintenance | Sabal; coordinate API/runtime changes with Sumit |
| `scripts/verification/` | Manual launcher and private-data handoff checks | Sabal |
| `research/fitbit_air/` | Existing proprietary BLE probe and candidate-key experiment, with separate tests | Sabal |
| `packages/contracts/` | Generated shared API contracts | Shared; Sumit coordinates |
| `deploy/`, `.github/` | Deployment assets and CI | Sumit with repo-owner review |
| `render.yaml` | Native Render API, worker and web deployment against existing Tiger | Sumit with repo-owner review |

The existing `apps/api/app/fall_detection/` experiment and its callers remain unchanged.
No new fall-detection module or functionality is part of this cleanup.

## Former root Python files

| Former file | New location | Purpose |
| --- | --- | --- |
| `fitbit_hr.py` | `apps/collector/farcare_collector/integrations/fitbit_hr.py` | Live BLE HR recording and diagnostics |
| `google_health.py` | `apps/collector/farcare_collector/integrations/google_health.py` | Google OAuth and original response/TCX exports |
| `tiger_sink.py` | `apps/collector/farcare_collector/integrations/tiger_sink.py` | Acknowledged numeric measurement delivery |
| `tiger_archive.py` | `apps/collector/farcare_collector/integrations/tiger_archive.py` | Original-record archive delivery |
| `health_normalize.py` | `apps/collector/farcare_collector/health/health_normalize.py` | Canonical source records, units and identity |
| `health_features.py` | `apps/collector/farcare_collector/health/health_features.py` | Source-separated descriptive features |
| `health_dashboard.py` | `apps/collector/farcare_collector/health/health_dashboard.py` | Standalone report HTML |
| `hr_analysis.py` | `apps/collector/farcare_collector/health/hr_analysis.py` | Offline HR snapshot analysis |
| `health_pipeline.py` | `apps/collector/farcare_collector/services/health_pipeline.py` | SQLite outbox, ingestion, replay, reports and watch loop |
| `run_farcare.py` | `scripts/local/run_farcare.py` | Local API/dashboard/collector supervisor |
| `prepare_farcare_preview.py` | `scripts/local/prepare_farcare_preview.py` | Isolated preview from existing cached records |
| `remove_chatoverflow.py` | `scripts/local/remove_chatoverflow.py` | Explicit offline workstation maintenance, not an app dependency |
| `air_probe.py` | `research/fitbit_air/air_probe.py` | Read-only proprietary BLE reconnaissance |
| `air_key_capture.py` | `research/fitbit_air/air_key_capture.py` | Experimental candidate-key collection; no proven decoder |
| `test_air_probe.py` | `research/fitbit_air/tests/test_air_probe.py` | Probe lifecycle and safe-target selection |
| Other 13 `test_*.py` files | `apps/collector/tests/`, retaining their names | Recorder, decoder, cloud, normalization, reports, replay and delivery tests |

No root Python source was proven redundant. In particular, `test_decoder.py` has payload
edge cases absent from the recorder lifecycle tests. The API's `health/google_health.py`
is a retained direct-provider scaffold, not the working collector export client. Neither
matching names nor similar responsibilities justify deletion.

The four `pipeline_sql/*.sql` assets move with the collector's Tiger integrations. The
managed-Node code and its tests remain available; they share the acknowledged outbox but
run in a different environment from the normal Python collector.

## Running the existing tools

Use the collector's Python environment (3.9+) for BLE and ingestion, and the API's environment
(3.12) for API and private-preview code. Do not merge their dependency files. From the
repository root, with the collector environment active:

```bash
export PYTHONPATH="$PWD/apps/collector${PYTHONPATH:+:$PYTHONPATH}"
# Set FARCARE_WORKSPACE to your existing private directory when needed.
python -m farcare_collector.integrations.fitbit_hr --help
python -m farcare_collector.integrations.google_health --help
python -m farcare_collector.services.health_pipeline --help
```

The equivalent live commands are:

```bash
python -m farcare_collector.integrations.fitbit_hr
python -m farcare_collector.integrations.google_health
python -m farcare_collector.services.health_pipeline watch --credentials /path/to/tiger-credentials.env
```

The BLE recorder remains a separate process near the watch. Run only one BLE central
connection; do not run the research probe alongside the recorder. Google refresh still
requires existing authorized OAuth setup. Research tools do not run on app startup.

Local app commands, also from the repository root:

```bash
python scripts/local/run_farcare.py --check
python scripts/local/run_farcare.py --web --open
apps/api/.venv/bin/python scripts/local/prepare_farcare_preview.py --help
```

Existing outer-workspace `.command` launchers point to these new paths. API/web container
entry points and Cloudflare build/deploy roots are unchanged. Do not change Cloudflare to
static Pages or copy the Mac collector into the backend image as part of this cleanup.

For the native hosting route, [the Render Blueprint](render-deployment.md) starts those same
API, worker and web applications without Docker. The existing Docker assets stay because
Cloudflare and CI still use them; collector and research code are excluded from hosted services.

## Verification

Run the existing application lint, tests, type checks, builds and contract checks described
in [AGENTS.md](../AGENTS.md) and [local CI](local-ci.md), plus the relocated collector tests.
From the repository root with the collector environment active:

```bash
PYTHONPATH=apps/collector python -m unittest discover -s apps/collector/tests -p 'test_*.py'
PYTHONPATH=apps/collector:. python -m unittest discover -s research/fitbit_air/tests -p 'test_*.py'
node apps/collector/tests/test_tiger_publisher.cjs
node apps/collector/tests/test_google_refresh.cjs
```

The Node publisher uses `node:sqlite`; use the existing Node 24 runtime for these checks.
Synthetic tests do not need the wearable, Google account, Tiger credentials or SMS delivery.
Manual scripts in `scripts/verification/` that depend on cached personal records stay separate
from CI; do not publish their health-data artifacts. The launcher check uses disposable fixtures.

Before deleting anything else, check imports, string-based mock targets, subprocess commands,
file-relative SQL paths, launchers and docs. Preserve the three developers' branch history:
work on the assigned feature branch, open a focused PR, and merge to `main` after checks and
required reviews. Never force-push another developer's work.
