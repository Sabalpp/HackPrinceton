#!/usr/bin/env bash
# ONE-TIME repository setup. Must be run by a repo ADMIN (the repo owner).
#   scripts/setup-github.sh [owner/repo]
# Idempotent: safe to re-run. Configures:
#   - squash-only merges + native auto-merge
#   - read-only default GITHUB_TOKEN; Actions may NOT approve PRs
#   - a "main protection" ruleset: PR + 1 approval (+ code owners) + required CI checks,
#     no force-push/deletion, linear history; no bypass actors
#   - the three feature branches (created from main if missing)
set -euo pipefail
REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "Configuring $REPO"

gh api -X PATCH "repos/$REPO" \
  -F allow_auto_merge=true -F allow_squash_merge=true -F allow_merge_commit=false \
  -F allow_rebase_merge=false -F delete_branch_on_merge=false \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY > /dev/null

gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false

RULESET=$(cat <<'JSON'
{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "API (lint, test, contracts)", "integration_id": 15368 },
          { "context": "API image builds", "integration_id": 15368 },
          { "context": "Web (lint, typecheck, build)", "integration_id": 15368 },
          { "context": "Secret scan", "integration_id": 15368 } ] } }
  ]
}
JSON
)
existing=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="main protection") | .id' || true)
if [ -n "$existing" ]; then
  echo "$RULESET" | gh api -X PUT "repos/$REPO/rulesets/$existing" --input - > /dev/null
  echo "Updated ruleset $existing"
else
  echo "$RULESET" | gh api -X POST "repos/$REPO/rulesets" --input - > /dev/null
  echo "Created ruleset"
fi

main_sha=$(gh api "repos/$REPO/git/ref/heads/main" -q .object.sha)
for b in feature/sumit-core feature/saksham-ui feature/sabal-fitbit; do
  if gh api "repos/$REPO/git/ref/heads/$b" > /dev/null 2>&1; then
    echo "Branch $b exists"
  else
    gh api "repos/$REPO/git/refs" -f ref="refs/heads/$b" -f sha="$main_sha" > /dev/null
    echo "Created $b"
  fi
done
echo "Done. Optional: add an AUTOMERGE_TOKEN secret so auto-merged PRs trigger Deploy."
