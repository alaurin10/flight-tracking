#!/usr/bin/env bash
# The API and the built frontend on port 8019, on a database seeded by `flighttrack demo`
# (synthetic history, no network). Used by `npm run e2e` (playwright.config.ts starts it).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="$(mktemp -d)"
trap 'rm -rf "$data"' EXIT
cd "$here/../../backend"
# Every browser here is one client: lift the 60-a-minute read limit.
export DATA_DIR="$data" STATIC_DIR="$here/../build" OWNER_PASSWORD="e2e-owner-password" READS_PER_MINUTE=10000
uv run --frozen flighttrack demo --db "$data/flights.db" >/dev/null
exec uv run --frozen uvicorn --factory flighttrack.api.app:production_app --port 8019
