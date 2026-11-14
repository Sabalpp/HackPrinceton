#!/usr/bin/env bash
# Post-deploy smoke test. Usage: scripts/smoke_test.sh https://api.example.com
set -euo pipefail
API="${1:?usage: smoke_test.sh <api-base-url>}"
fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

health=$(curl -fsS "$API/health") || fail "/health unreachable"
echo "$health" | jq -e '.status == "ok" and .database == "ok"' > /dev/null || fail "unhealthy: $health"

c=$(code "$API/api/parents/parent_001/overview")
[ "$c" = 401 ] || fail "dashboard API not protected (got $c, want 401)"

c=$(code -X POST "$API/webhooks/photon" -H 'content-type: application/json' -d '{}')
[[ "$c" = 400 || "$c" = 503 ]] || fail "webhook accepted an unsigned payload (got $c)"

c=$(code -X POST "$API/api/events/fall" -H 'content-type: application/json' -d '{}')
[[ "$c" = 401 || "$c" = 422 ]] || fail "fall ingestion not protected/validated (got $c)"

echo "Smoke OK: $(echo "$health" | jq -c .)"
