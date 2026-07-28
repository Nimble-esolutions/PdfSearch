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
    "${COMPOSE[@]}" logs --no-color --tail=120 web maintenance >&2 || true
  fi
  LIFECYCLE_ACTIVATION_ENABLED=0 "${COMPOSE[@]}" down --volumes --remove-orphans
}
trap cleanup EXIT

cd "$ROOT"
if [ "${SKIP_MAINTENANCE_E2E_BUILD:-0}" != "1" ]; then
  docker build -t "$PDFSEARCH_IMAGE" .
fi
"${COMPOSE[@]}" up -d --wait redis web
"${COMPOSE[@]}" stop web
"${COMPOSE[@]}" run --rm fixture seed-and-freeze
"${COMPOSE[@]}" up -d --wait web maintenance

PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=queue \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list

"${COMPOSE[@]}" run --rm fixture remove-retry-file
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=fail \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list

"${COMPOSE[@]}" run --rm fixture repair-retry-file
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=retry \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list

"${COMPOSE[@]}" stop web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait --force-recreate web maintenance
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=activate \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list

echo "Disposable maintenance lifecycle verified."
