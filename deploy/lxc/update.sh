#!/usr/bin/env bash
# Poll-and-deploy: fast-forward origin/main and restart the service only if
# something changed. Outbound-only, no webhooks — mirrors home-hub's
# apps/dashboard/scripts/deploy.sh. Run by flighttrack-update.timer.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/flighttrack}"
BRANCH="${DEPLOY_BRANCH:-main}"
cd "$APP_DIR"
git fetch --quiet origin "$BRANCH"
local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse "origin/$BRANCH")
[ "$local_sha" = "$remote_sha" ] && exit 0
echo "$(date -u +%FT%TZ) $BRANCH moved ($local_sha -> $remote_sha) — updating"
git merge --ff-only "origin/$BRANCH"
.venv/bin/pip install -q -e ".[full]"
# config.yaml is yours; a git update never overwrites a modified copy because
# it is committed with placeholders — resolve conflicts by hand if it ever does.
systemctl restart flighttrack-serve.service
echo "$(date -u +%FT%TZ) updated to $remote_sha"
