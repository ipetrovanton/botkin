#!/usr/bin/env bash
# Commit and push handoff files (.remember/ and habr/) to keep cross-machine state in sync.
set -euo pipefail

MESSAGE="${1:-docs(handoff): session checkpoint}"

# Move to repo root from scripts/.
cd "$(dirname "$0")/.."

# Only sync if there are changes in handoff paths (staged, unstaged or untracked).
if [ -z "$(git status --porcelain .remember/ habr/)" ]; then
    echo "No handoff changes to sync."
    exit 0
fi

CURRENT_BRANCH=$(git branch --show-current)

git add -A .remember/ habr/
git commit -m "$MESSAGE"
git push origin "${CURRENT_BRANCH}"

echo "Handoff synced to origin/${CURRENT_BRANCH}."
