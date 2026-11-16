#!/usr/bin/env bash
# The API and the built frontend on a database seeded by `flighttrack demo` (synthetic
# history, no network). Used by `npm run e2e` (playwright.config.ts starts two of them):
#   serve.sh 8019           the API alone: the read-only pages (app.spec.ts)
#   serve.sh 8020 --worker  plus a worker with FLIGHTTRACK_SOURCE=fake, which prices new
#                           trips with invented fares and sends no outside request
#                           (manage.spec.ts)
set -euo pipefail
port="${1:-8019}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="$(mktemp -d)"
worker_pid=""
api_pid=""
cleanup() {
	for pid in $worker_pid $api_pid; do kill "$pid" 2>/dev/null || true; done
	wait 2>/dev/null || true
	rm -rf "$data"
}
trap cleanup EXIT
cd "$here/../../backend"
# Every browser here is one client: lift the 60-a-minute read limit.
export DATA_DIR="$data" STATIC_DIR="$here/../build" OWNER_PASSWORD="e2e-owner-password" READS_PER_MINUTE=10000
uv run --frozen flighttrack demo --db "$data/flights.db" >/dev/null
if [ "${2:-}" = "--worker" ]; then
	# The shipped tunables with the allowance pinned high, so reruns never run it dry.
	mkdir -p "$data/config"
	sed 's/^collect:$/collect:\n  daily_request_budget: 1000/' ../config/flighttrack.yaml >"$data/config/flighttrack.yaml"
	grep -q '^  daily_request_budget: 1000$' "$data/config/flighttrack.yaml"
	export CONFIG_DIR="$data/config" FLIGHTTRACK_SOURCE=fake
	uv run --frozen flighttrack worker >"$data/worker.log" 2>&1 &
	worker_pid=$!
fi
uv run --frozen uvicorn --factory flighttrack.api.app:production_app --port "$port" &
api_pid=$!
wait "$api_pid"
