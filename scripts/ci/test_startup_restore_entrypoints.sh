#!/usr/bin/env bash
set -euo pipefail

image="${PDFSEARCH_IMAGE:?PDFSEARCH_IMAGE is required}"
probe_root="$(mktemp -d)"

cleanup() {
    docker run --rm --entrypoint chmod \
        -v "$probe_root:/probe" \
        "$image" -R a+rwX /probe >/dev/null 2>&1 || true
    rm -r "$probe_root"
}
trap cleanup EXIT

run_probe() {
    local role="$1"
    local data_root="$probe_root/$role-data"
    local control_root="$probe_root/$role-control"
    local log="$probe_root/$role.log"
    local -a docker_args=(
        --rm
        -e APP_ENV=development
        -e SECRET_KEY=ci-only-secret-key-with-more-than-fifty-characters
        -e RESTORE_POLICY=startup-latest
        -e DATA_BOOTSTRAP_MODE=empty
        -v "$data_root:/app/data"
        -v "$control_root:/app/data-control"
    )
    mkdir "$data_root" "$control_root"
    if [ "$role" = "maintenance" ]; then
        docker_args+=(--entrypoint ./worker-entrypoint.sh)
    fi

    set +e
    docker run "${docker_args[@]}" "$image" >"$log" 2>&1
    local result=$?
    set -e

    if [ "$result" -eq 0 ]; then
        echo "[$role] startup restore preflight unexpectedly succeeded" >&2
        return 1
    fi
    if [ -e "$data_root/db.sqlite3" ]; then
        echo "[$role] startup restore preflight created a database" >&2
        return 1
    fi
    if ! grep -q "startup_restore_required_but_unavailable" "$log"; then
        echo "[$role] stable startup restore reason was not emitted" >&2
        return 1
    fi
}

run_probe web
run_probe maintenance
echo "[startup-restore] web and maintenance failed closed before database creation"
