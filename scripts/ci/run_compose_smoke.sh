#!/usr/bin/env bash
set -euo pipefail

: "${PDFSEARCH_IMAGE:?PDFSEARCH_IMAGE is required}"
: "${REDIS_IMAGE:?REDIS_IMAGE is required}"
: "${SECRET_KEY:?SECRET_KEY is required}"

compose_project="${COMPOSE_PROJECT_NAME:-pdfsearch-ci-smoke}"
web_port="${WEB_PORT:-18000}"
export COMPOSE_PROJECT_NAME="$compose_project" WEB_PORT="$web_port"
compose=(docker compose -f docker-compose.ci.yml)

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" config --quiet
"${compose[@]}" up --detach

for service in redis web; do
    container="$("${compose[@]}" ps --quiet "$service")"
    test -n "$container"
    for attempt in $(seq 1 90); do
        state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")"
        if [[ "$state" == "running healthy" ]]; then
            break
        fi
        if [[ "$state" == exited* || "$state" == dead* ]]; then
            echo "${service} container stopped unexpectedly" >&2
            exit 1
        fi
        if [[ "$attempt" == 90 ]]; then
            echo "${service} did not become healthy: $state" >&2
            exit 1
        fi
        sleep 2
    done
done

curl --fail --silent --show-error "http://127.0.0.1:${web_port}/livez" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${web_port}/readyz" >/dev/null

# Check for ungenerated migrations (fail if model changes lack migration file)
echo "=== Checking for ungenerated migrations ==="
"${compose[@]}" exec --no-TTY --user appuser web python /app/flowdocs/manage.py makemigrations --check --dry-run --noinput
MIGRATIONS_CHECK=$?
if [ $MIGRATIONS_CHECK -ne 0 ]; then
    echo "ERROR: Ungenerated migrations detected. Run 'makemigrations' locally."
    exit 1
fi

"${compose[@]}" exec --no-TTY --user appuser web python /app/scripts/ci/runtime_smoke.py
"${compose[@]}" exec --no-TTY --user appuser web python /app/flowdocs/manage.py check --deploy --fail-level ERROR

# Run admin UI smoke tests
echo "=== Running admin UI smoke tests ==="
"${compose[@]}" exec --no-TTY --user appuser web sh -lc "printf 'ci-only-password-not-for-production' > /tmp/codex-admin-password.txt"
"${compose[@]}" exec --no-TTY --user appuser web env ADMIN_SMOKE_USERNAME=ci-admin python /app/scripts/ci/admin_ui_smoke.py
PLAYWRIGHT_BASE_URL="http://127.0.0.1:${web_port}" \
    npx playwright test \
    browser_tests/operations-cockpit.spec.ts \
    browser_tests/dataops-workbench.spec.ts \
    browser_tests/classic-search.spec.ts \
    --project=desktop --project=mobile --workers=1
"${compose[@]}" exec --no-TTY --user appuser web python -m pip check

test_log="$(mktemp)"
# The retired Vault workbench contract is no longer the active UI surface. Its
# lifecycle tests remain available for the migration stack, while this release
# gate exercises the active core/Data Operations contracts instead.
if ! "${compose[@]}" exec --no-TTY --user appuser web python /app/flowdocs/manage.py test core.tests core.tests_search_themes core.test_public_search_routing core.test_startup_restore core.test_artifact_vault core.tests_recovery core.tests_candidate_cleanup core.test_custody_audit dataops --noinput --verbosity=2 >"$test_log" 2>&1; then
    cat "$test_log"
    rm -f "$test_log"
    exit 1
fi
cat "$test_log"
grep -q "core.test_artifact_vault" "$test_log"
grep -q "core.test_public_search_routing" "$test_log"
grep -q "dataops" "$test_log"
rm -f "$test_log"

"${compose[@]}" exec --no-TTY --user appuser web python /app/scripts/ci/data_release_gate.py
"${compose[@]}" --profile seed run --rm --no-deps seed
echo "[compose] actual image entrypoint, Redis dependency, core tests, runtime, data, and index gates passed"
