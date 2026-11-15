#!/usr/bin/env bash
# Render native Python includes Node/npm. Existing lockfiles remain authoritative.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
(
  cd backend
  uv sync --frozen --no-dev --no-install-project
)
export NEXT_PUBLIC_API_URL=/backend
export FARCARE_API_ORIGIN=http://127.0.0.1:8000
export NEXT_PUBLIC_USE_MOCKS=false NEXT_PUBLIC_LOCAL_PREVIEW=false
export NEXT_TELEMETRY_DISABLED=1
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
