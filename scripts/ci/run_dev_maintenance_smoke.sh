#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
project="${COMPOSE_PROJECT_NAME:-pdfsearch-dev-maintenance-smoke-$$}"
export COMPOSE_PROJECT_NAME="$project"
export WEB_PORT="${WEB_PORT:-$((18030 + $$ % 1000))}"
export REDIS_PORT="${REDIS_PORT:-$((16380 + $$ % 1000))}"

compose=(docker compose -f "$root/docker-compose.dev.yml")

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach --build --wait --wait-timeout 240

"${compose[@]}" exec --no-TTY web \
    python /app/scripts/ci/dev_maintenance_smoke.py

"${compose[@]}" ps
echo "[compose] normal development maintenance worker consumed a queued job"
