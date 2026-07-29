#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="pdfsearch-maintenance-e2e-${CI_RUN_ID:-local}"
export PDFSEARCH_IMAGE="${PDFSEARCH_IMAGE:-pdfsearch-maintenance-e2e:local}"
export WEB_PORT="${WEB_PORT:-18020}"
if [ -z "${MINIO_IMAGE:-}" ]; then
  docker pull minio/minio:latest >/dev/null
  MINIO_IMAGE="$(
    docker image inspect minio/minio:latest \
      --format '{{ index .RepoDigests 0 }}'
  )"
fi
case "$MINIO_IMAGE" in
  *@sha256:*) ;;
  *) echo "MINIO_IMAGE must resolve to an immutable digest" >&2; exit 2 ;;
esac
export MINIO_IMAGE
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/docker-compose.maintenance-e2e.yml")

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${COMPOSE[@]}" run --rm fixture activation-evidence >&2 || true
    "${COMPOSE[@]}" logs --no-color --tail=120 \
      minio web maintenance >&2 || true
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
"${COMPOSE[@]}" up -d --wait minio redis web browser-proxy
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

# Each runtime cutover invalidates database-backed sessions and requires a fresh
# login. Reset only the disposable suite's isolated Redis database so the three
# deliberate reauthentications below exercise authentication instead of the
# aggregate login throttle accumulated by the earlier independent phases.
"${COMPOSE[@]}" exec --no-TTY redis redis-cli -n 7 FLUSHDB >/dev/null
"${COMPOSE[@]}" stop web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait --force-recreate web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait browser-proxy
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=activate \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-final-parent-custody
"${COMPOSE[@]}" run --rm fixture assert-final-control-evidence

# Publish the restored parent through the isolated MinIO Vault, restore that
# exact authoritative generation into a new runtime, then exercise the same
# real web/maintenance supervisor pair used above for signed activation.
"${COMPOSE[@]}" exec --no-TTY redis redis-cli -n 7 FLUSHDB >/dev/null
"${COMPOSE[@]}" stop web maintenance
vault_restore_output="$(
  LIFECYCLE_ACTIVATION_ENABLED=0 LIFECYCLE_WRITER_MODE=0 \
    "${COMPOSE[@]}" run --rm --user 1000:1000 \
      -e APP_ENV=production \
      -e BACKUP_ROLE=writer \
      -e VAULT_SYNC_ENABLED=1 \
      -e STAGING_RUNTIME_ACTIVATION_ENABLED=0 \
      -e MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=0 \
      -e EXTERNAL_SIDE_EFFECTS_MODE=enabled \
      fixture publish-and-restore
)"
printf '%s\n' "$vault_restore_output"
vault_identity="$(printf '%s\n' "$vault_restore_output" | tail -n 1)"
vault_generation_id="$(
  python3 -c \
    'import json,sys; print(json.loads(sys.stdin.read())["generation_id"])' \
    <<<"$vault_identity"
)"
vault_manifest_digest="$(
  python3 -c \
    'import json,sys; print(json.loads(sys.stdin.read())["manifest_digest"])' \
    <<<"$vault_identity"
)"
if [ -z "$vault_generation_id" ] \
  || [[ ! "$vault_generation_id" =~ ^[A-Za-z0-9._:-]+$ ]] \
  || [[ ! "$vault_manifest_digest" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Vault fixture did not return a bounded generation identity" >&2
  exit 1
fi
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait --force-recreate web maintenance
LIFECYCLE_ACTIVATION_ENABLED=1 LIFECYCLE_WRITER_MODE=0 \
  "${COMPOSE[@]}" up -d --wait browser-proxy
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${WEB_PORT}" \
  MAINTENANCE_E2E_PHASE=vault-activate \
  VAULT_E2E_TARGET_GENERATION_ID="$vault_generation_id" \
  VAULT_E2E_TARGET_MANIFEST_DIGEST="$vault_manifest_digest" \
  npx playwright test browser_tests/maintenance-lifecycle.spec.ts \
    --project=desktop --reporter=list
"${COMPOSE[@]}" run --rm fixture assert-vault-activation-evidence

web_container="$("${COMPOSE[@]}" ps -q web)"
web_state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$web_container")"
if [ "$web_state" != "running:0" ]; then
  echo "Expected a healthy supervisor after signed runtime handoffs; observed state=$web_state" >&2
  exit 1
fi

echo "Disposable maintenance lifecycle verified."
