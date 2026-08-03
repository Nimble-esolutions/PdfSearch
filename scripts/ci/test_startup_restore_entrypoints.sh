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
    for mutable_path in restore-quarantine runtime-generations; do
        if ! docker run --rm \
            --user 1000:1000 \
            --entrypoint sh \
            -v "$data_root:/app/data" \
            "$image" \
            -c "test -w /app/data/$mutable_path"; then
            echo "[$role] $mutable_path was not handed to appuser" >&2
            return 1
        fi
    done
}

run_newer_control_schema_probe() {
    local role="$1"
    local data_root="$probe_root/$role-newer-data"
    local control_root="$probe_root/$role-newer-control"
    local control_db="$control_root/control.sqlite3"
    local log="$probe_root/$role-newer.log"
    local -a docker_args=(
        --rm
        -e APP_ENV=development
        -e SECRET_KEY=ci-only-secret-key-with-more-than-fifty-characters
        -e RESTORE_POLICY=manual
        -e DATA_BOOTSTRAP_MODE=empty
        -v "$data_root:/app/data"
        -v "$control_root:/app/data-control"
    )
    mkdir "$data_root" "$control_root"
    python3 - "$control_db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    connection.execute(
        "CREATE TABLE django_migrations "
        "(id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)"
    )
    connection.execute(
        "INSERT INTO django_migrations(app, name, applied) "
        "VALUES('dataops', '9999_future_contract', '')"
    )
PY
    local before
    before="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$control_db")"
    if [ "$role" = "maintenance" ]; then
        docker_args+=(--entrypoint ./worker-entrypoint.sh)
    fi

    set +e
    docker run "${docker_args[@]}" "$image" >"$log" 2>&1
    local result=$?
    set -e

    if [ "$result" -eq 0 ]; then
        echo "[$role] newer control schema unexpectedly started" >&2
        return 1
    fi
    if ! grep -q "startup_schema_contract_older_than_control_database" "$log"; then
        echo "[$role] newer control schema did not emit the stable reason" >&2
        return 1
    fi
    if [ -e "$data_root/db.sqlite3" ]; then
        echo "[$role] newer control schema gate created an application database" >&2
        return 1
    fi
    local after
    after="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$control_db")"
    if [ "$before" != "$after" ]; then
        echo "[$role] newer control schema gate mutated the control database" >&2
        return 1
    fi
}

run_probe web
run_probe maintenance
run_newer_control_schema_probe web
run_newer_control_schema_probe maintenance
echo "[startup] web and maintenance restore/schema gates failed closed before database mutation"
