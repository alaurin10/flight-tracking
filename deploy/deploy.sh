#!/usr/bin/env bash
# Poll-and-deploy for Flight Watch, run every 5 minutes by flightwatch-deploy.timer
# on the server (see docs/DEPLOYMENT.md, "Automatic updates").
#
# Fetches main, fast-forwards, and rebuilds only when app files changed.
# Outbound only: no webhook, no open port. A failed build leaves the running
# containers as they were; check `journalctl -u flightwatch-deploy`.
#
# The checkout is a deploy target, not a dev checkout: keep it on main with no
# local edits (git merge --ff-only refuses to overwrite anything).
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BRANCH="${DEPLOY_BRANCH:-main}"
WATCH=(backend frontend config docker docker-compose.yml)

cd "$REPO_DIR"
git fetch --quiet origin "$BRANCH"

local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse "origin/$BRANCH")
[ "$local_sha" = "$remote_sha" ] && exit 0

if git diff --quiet "$local_sha" "$remote_sha" -- "${WATCH[@]}"; then
  echo "$(date -u +%FT%TZ) $BRANCH moved, no app changes; fast-forwarding only"
  git merge --ff-only --quiet "origin/$BRANCH"
  exit 0
fi

echo "$(date -u +%FT%TZ) deploying $local_sha -> $remote_sha"
git merge --ff-only --quiet "origin/$BRANCH"
GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
docker image prune -f >/dev/null
echo "$(date -u +%FT%TZ) deployed $remote_sha"
