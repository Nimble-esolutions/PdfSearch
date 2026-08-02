#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
project="${COMPOSE_PROJECT_NAME:-pdfsearch-dev-maintenance-smoke-$$}"
export COMPOSE_PROJECT_NAME="$project"
export WEB_PORT="${WEB_PORT:-$((18030 + $$ % 1000))}"
export REDIS_PORT="${REDIS_PORT:-$((16380 + $$ % 1000))}"

compose=(docker compose -f "$root/docker-compose.dev.yml")
build_args=(--build)
if [[ "${1:-}" == "--no-build" ]]; then
    build_args=()
elif [[ $# -gt 0 ]]; then
    echo "dev_smoke_unknown_argument" >&2
    exit 2
fi
export LOCAL_BUILD_REVISION="${LOCAL_BUILD_REVISION:-$(git -C "$root" rev-parse --short HEAD)}"

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach "${build_args[@]}" --wait --wait-timeout 300

web_container="$("${compose[@]}" ps --quiet web)"
maintenance_container="$("${compose[@]}" ps --quiet maintenance)"
web_image_id="$(docker inspect "$web_container" --format '{{.Image}}')"
maintenance_image_id="$(docker inspect "$maintenance_container" --format '{{.Image}}')"
[[ -n "$web_image_id" && "$web_image_id" == "$maintenance_image_id" ]] || {
    echo "dev_smoke_application_image_divergent" >&2
    exit 1
}
image_revision="$(docker image inspect "${PDFSEARCH_DEV_IMAGE:-pdfsearch-dev:local}" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
runtime_revision="$(docker inspect "$web_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^APP_RELEASE_VERSION=//p')"
[[ "$image_revision" == "$LOCAL_BUILD_REVISION" && "$runtime_revision" == "$LOCAL_BUILD_REVISION" ]] || {
    echo "dev_smoke_release_identity_mismatch" >&2
    exit 1
}

if "${compose[@]}" ps --services | grep -Eq '^minio(-init)?$'; then
    echo "dev_smoke_minio_service_present" >&2
    exit 1
fi

"${compose[@]}" exec --no-TTY web \
    python /app/scripts/ci/dev_maintenance_smoke.py

"${compose[@]}" ps
echo "dev_smoke_passed"
