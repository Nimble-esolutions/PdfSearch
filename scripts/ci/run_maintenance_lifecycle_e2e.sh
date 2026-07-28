#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="pdfsearch-maintenance-e2e-${CI_RUN_ID:-local}"
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/docker-compose.maintenance-e2e.yml")
export PDFSEARCH_IMAGE="${PDFSEARCH_IMAGE:-pdfsearch-maintenance-e2e:local}"
export WEB_PORT="${WEB_PORT:-18020}"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${COMPOSE[@]}" run --rm fixture activation-evidence >&2 || true
    "${COMPOSE[@]}" logs --no-color --tail=120 web maintenance >&2 || true
  fi
  LIFECYCLE_ACTIVATION_ENABLED=0 "${COMPOSE[@]}" down --volumes --remove-orphans
  leftovers="$(
    {
      docker ps -aq --filter "label=com.docker.compose.project=$PROJECT"
      docker network ls -q --filter "label=com.docker.compose.project=$PROJECT"
      docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT"
    } | sed '/^$/d'
  )"
  if [ -n "$leftovers" ]; then
    echo "Disposable lifecycle teardown left project artifacts" >&2
    exit 1
  fi
  return "$status"
}
trap cleanup EXIT

cd "$ROOT"
if [ "${SKIP_MAINTENANCE_E2E_BUILD:-0}" != "1" ]; then
  docker build -t "$PDFSEARCH_IMAGE" .
fi
"${COMPOSE[@]}" up -d --wait redis web browser-proxy
"${COMPOSE[@]}" stop web
"${COMPOSE[@]}" run --rm fixture seed-and-freeze
"${COMPOSE[@]}" up -d --wait web maintenance browser-proxy

PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=queue \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-parent-tree

"${COMPOSE[@]}" run --rm fixture remove-retry-file
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=fail \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-parent-tree

"${COMPOSE[@]}" run --rm fixture repair-retry-file
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=retry \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-maintenance-evidence
"${COMPOSE[@]}" run --rm fixture assert-parent-tree

"${COMPOSE[@]}" stop web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait --force-recreate web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait browser-proxy
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=activate \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-parent-tree
"${COMPOSE[@]}" run --rm fixture assert-final-control-evidence

web_container="$("${COMPOSE[@]}" ps -q web)"
web_restarts="$(docker inspect --format '{{.RestartCount}}' "$web_container")"
web_state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$web_container")"
if [ "$web_restarts" -lt 2 ] || [ "$web_state" != "running:0" ]; then
  echo "Expected two clean orchestrator handoffs; observed restarts=$web_restarts state=$web_state" >&2
  exit 1
fi

echo "Disposable maintenance lifecycle verified."
