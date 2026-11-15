#!/usr/bin/env bash
# After your PR is merged, include main in your long-lived feature branch.
# Preserve both histories. Conflicts and remote divergence stop safely.
# Refuses if you have commits that were not part of the merged PR.
#   scripts/sync-after-merge.sh
set -euo pipefail
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" != "main" ] || { echo "Run this on your feature branch."; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "Commit or stash your changes first."; exit 1; }
git fetch origin --quiet

pr=$(gh pr list --head "$branch" --state merged --limit 1 --json number,headRefOid -q '.[0]')
[ -n "$pr" ] || { echo "No merged PR found for $branch."; exit 1; }
merged_head=$(echo "$pr" | jq -r .headRefOid)
number=$(echo "$pr" | jq -r .number)

if [ "$(git rev-parse HEAD)" != "$merged_head" ]; then
  echo "Your branch has commits after PR #$number was merged:"
  git log --oneline "$merged_head"..HEAD
  echo "Move them first, e.g.: git switch -c $branch-wip && git switch $branch, then re-run,"
  echo "then cherry-pick the WIP commits onto the fresh branch."
  exit 1
fi
git merge --no-edit origin/main
git push origin "HEAD:refs/heads/$branch"
echo "$branch includes main (PR #$number is merged)."
