#!/usr/bin/env bash
# =====================================================================
# Run INSIDE the LXC (as root), once. Idempotent — safe to re-run.
#
#   git clone https://github.com/alaurin10/flight-tracking /opt/flighttrack
#   bash /opt/flighttrack/deploy/lxc/install.sh
#
# Installs Python + a venv, a `flighttrack` system user, the config and
# secrets file, the `flighttrack-serve` service (scheduler + report on
# :8080), and the poll-and-deploy timer that keeps the checkout on
# origin/main (same pattern as home-hub's dashboard-deploy.timer).
# =====================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flighttrack}"
ENV_FILE=/etc/flighttrack.env
SERVICE_USER=flighttrack

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends git python3 python3-venv ca-certificates curl sqlite3 >/dev/null

echo "== user"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"

echo "== checkout at $APP_DIR"
[ -d "$APP_DIR/.git" ] || git clone https://github.com/alaurin10/flight-tracking "$APP_DIR"
git -C "$APP_DIR" config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "== virtualenv"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR[full]"

echo "== data directories"
mkdir -p "$APP_DIR/data" "$APP_DIR/out"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "== secrets"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'ENV'
# flighttrack secrets — read by the systemd units. Never commit this file.
NTFY_TOPIC=replace-with-a-long-random-topic
# SERPAPI_KEY=
ENV
  chmod 600 "$ENV_FILE"
  echo "   wrote $ENV_FILE — EDIT IT: set NTFY_TOPIC"
fi

echo "== systemd"
install -m 644 "$APP_DIR/deploy/lxc/flighttrack-serve.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/lxc/flighttrack-update.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/lxc/flighttrack-update.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now flighttrack-serve.service
systemctl enable --now flighttrack-update.timer

echo "== timezone (the 03:15 schedule is local time)"
echo "   current: $(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone)"
echo "   change with: timedatectl set-timezone America/Los_Angeles && systemctl restart flighttrack-serve"

echo
echo "installed. Now:"
echo "  1. nano $ENV_FILE                      # set NTFY_TOPIC"
echo "  2. nano $APP_DIR/config.yaml           # your trips, patterns, watches"
echo "  3. systemctl restart flighttrack-serve"
echo "  4. sudo -u $SERVICE_USER $APP_DIR/.venv/bin/flighttrack --config $APP_DIR/config.yaml doctor"
echo "  5. open http://$(hostname -I | awk '{print $1}'):8080/"
