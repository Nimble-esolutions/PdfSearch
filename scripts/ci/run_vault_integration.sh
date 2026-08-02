#!/usr/bin/env bash
set -euo pipefail

: "${PDFSEARCH_IMAGE:?PDFSEARCH_IMAGE is required}"
: "${REDIS_IMAGE:?REDIS_IMAGE is required}"
: "${OBJECT_STORE_IMAGE:?OBJECT_STORE_IMAGE is required}"
provider="${OBJECT_STORE_PROVIDER:-rustfs}"
case "$provider" in
  rustfs)
    export OBJECT_STORE_COMMAND="${OBJECT_STORE_COMMAND:-/data}"
    ;;
  minio)
    export OBJECT_STORE_COMMAND="${OBJECT_STORE_COMMAND:-server /data}"
    ;;
  *) echo "object_store_provider_invalid" >&2; exit 2 ;;
esac

compose_project="${COMPOSE_PROJECT_NAME:-pdfsearch-vault-ci}"
case "$compose_project" in
  pdfsearch-vault-*) ;;
  *)
    echo "COMPOSE_PROJECT_NAME must start with pdfsearch-vault-" >&2
    exit 2
    ;;
esac

export COMPOSE_PROJECT_NAME="$compose_project"
compose=(docker compose -f docker-compose.integration.yml)

cleanup() {
  "${compose[@]}" --profile process-death down \
    --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach objectstore redis
"${compose[@]}" run --rm lifecycle

"${compose[@]}" --profile process-death up \
  --detach --no-deps runtime-cutover
cutover_container="$("${compose[@]}" ps --quiet runtime-cutover)"
test -n "$cutover_container"

for attempt in $(seq 1 120); do
  if docker exec "$cutover_container" \
    test -f /app/data-control/process-death/cutover-reached; then
    break
  fi
  state="$(docker inspect --format '{{.State.Status}}' "$cutover_container")"
  if [[ "$state" != "running" ]]; then
    echo "runtime cutover container exited before crash checkpoint" >&2
    exit 1
  fi
  if [[ "$attempt" == 120 ]]; then
    echo "runtime cutover checkpoint timed out" >&2
    exit 1
  fi
  sleep 1
done

docker kill "$cutover_container" >/dev/null
test "$(docker inspect --format '{{.State.ExitCode}}' "$cutover_container")" != "0"

"${compose[@]}" --profile process-death run \
  --rm --no-deps runtime-recovery
"${compose[@]}" --profile process-death run \
  --rm --no-deps runtime-verify

echo "vault_integration_passed:$provider"
