#!/bin/bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/app/data}"
DATA_CONTROL_ROOT="${DATA_CONTROL_ROOT:-/app/data-control}"
CONTROL_DB_PATH="${CONTROL_DB_PATH:-$DATA_CONTROL_ROOT/control.sqlite3}"
DB_PATH="${SQLITE_DB_PATH:-$DATA_ROOT/db.sqlite3}"
MEDIA_DIR="${MEDIA_ROOT:-$DATA_ROOT/media}"
PDF_CACHE_DIR="${PDF_CACHE_DIR:-$DATA_ROOT/pdf_cache}"
FAISS_DIR="${FAISS_INDEX_DIR:-$DATA_ROOT/faiss_indexes}"
CHROMA_DIR="${CHROMA_DIR:-$DATA_ROOT/chroma_db}"
STATIC_DIR="${STATIC_ROOT:-$DATA_ROOT/staticfiles}"
VAULT_RESTORE_ROOT="${VAULT_RESTORE_ROOT:-$DATA_ROOT/restore-quarantine}"
RUNTIME_GENERATIONS_ROOT="${RUNTIME_GENERATIONS_ROOT:-$DATA_ROOT/runtime-generations}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_ROOT/backups}"
LEGACY_DATA="${LEGACY_DATA_ROOT:-/mnt/legacy}"
INIT_DB="${DECLARED_SEED_DB:-/app/init/db.sqlite3}"
INIT_MEDIA="${DECLARED_SEED_MEDIA:-/app/init/media}"
INIT_FAISS="/app/init/faiss_indexes"
MIGRATION_MARKER="$DATA_ROOT/.legacy_migration_complete"
SEED_VALIDATION_MARKER="$DATA_ROOT/.declared_seed_validation.pending"
DATA_BOOTSTRAP_MODE="${DATA_BOOTSTRAP_MODE:-strict}"
SEED_DB_COPIED=0
RUNTIME_START_MODE="disabled"

if [ "${STAGING_RUNTIME_ACTIVATION_ENABLED:-0}" = "1" ]; then
    for mutation_switch in IMPORT_LEGACY_DATA RUN_JSON_MIGRATIONS CREATE_SUPERUSER; do
        if [ "${!mutation_switch:-0}" = "1" ]; then
            echo "[activation] ERROR: $mutation_switch cannot mutate an immutable runtime" >&2
            exit 1
        fi
    done
    RUNTIME_START_MODE="$(
        python /app/flowdocs/runtime_paths_cli.py generation \
            --allow-initial-bootstrap
    )"
    if [ "$RUNTIME_START_MODE" = "initial-bootstrap" ]; then
        if [ "${STAGING_INITIAL_ACTIVATION_ENABLED:-0}" != "1" ]; then
            echo "[activation] ERROR: initial bootstrap requires explicit opt-in" >&2
            exit 1
        fi
        echo "[activation] No runtime authority exists; serving the restored staging database until signed first activation"
    else
        DB_PATH="$(python /app/flowdocs/runtime_paths_cli.py database)"
        MEDIA_DIR="$(python /app/flowdocs/runtime_paths_cli.py media)"
        PDF_CACHE_DIR="$(python /app/flowdocs/runtime_paths_cli.py pdf_cache)"
        FAISS_DIR="$(python /app/flowdocs/runtime_paths_cli.py faiss)"
        CHROMA_DIR="$(python /app/flowdocs/runtime_paths_cli.py chroma)"
    fi
fi

export SQLITE_DB_PATH="$DB_PATH"
export DATA_ROOT DATA_CONTROL_ROOT CONTROL_DB_PATH DB_PATH MEDIA_DIR PDF_CACHE_DIR FAISS_DIR CHROMA_DIR STATIC_DIR BACKUP_DIR VAULT_RESTORE_ROOT RUNTIME_GENERATIONS_ROOT
export DATA_BOOTSTRAP_MODE
export SECRET_KEY="${SECRET_KEY:-}"
export DEBUG="${DEBUG:-False}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-flowdocs.settings}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-}"
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-}"

case "$DATA_BOOTSTRAP_MODE" in
    strict|empty|bootstrap) ;;
    *) echo "[config] ERROR: DATA_BOOTSTRAP_MODE must be strict, empty, or bootstrap" >&2; exit 1 ;;
esac

printf '%s\n' '============================================================' '  FlowDocs - PDF Search Utility' '============================================================'
printf 'DEBUG=%s\nALLOWED_HOSTS=%s\nDATA_ROOT=%s\n' "$DEBUG" "$ALLOWED_HOSTS" "$DATA_ROOT"
printf '%s\n' '============================================================'

if [ -z "$SECRET_KEY" ] && [ "${ALLOW_INSECURE_DEFAULTS:-0}" != "1" ]; then
    echo "[config] ERROR: SECRET_KEY is required" >&2
    exit 1
fi

mkdir -p "$DATA_ROOT" "$DATA_CONTROL_ROOT" "$MEDIA_DIR/pdfs" "$PDF_CACHE_DIR" "$CHROMA_DIR" "$FAISS_DIR" \
    "$BACKUP_DIR/json_backups" "$BACKUP_DIR/chroma_backup" \
    "$BACKUP_DIR/control_backups" "$STATIC_DIR" \
    "$VAULT_RESTORE_ROOT" "$RUNTIME_GENERATIONS_ROOT"
test -w "$DATA_ROOT" || { echo "[data] ERROR: $DATA_ROOT is not writable" >&2; exit 1; }
test -w "$DATA_CONTROL_ROOT" || { echo "[control] ERROR: $DATA_CONTROL_ROOT is not writable" >&2; exit 1; }

# Startup restore modes are declarations, not permission to create an empty
# database. Both web and maintenance run this DB-free guard before any import,
# seed, backup, migration, queue, remote Vault call, or activation.
(cd /app/flowdocs && python manage.py startup_restore_preflight)

# Import only mutable production data from the legacy volume. Application code
# remains in the image and is never copied from the legacy volume.
if [ "${IMPORT_LEGACY_DATA:-0}" = "1" ] && [ ! -f "$MIGRATION_MARKER" ] && [ -f "$LEGACY_DATA/db.sqlite3" ]; then
    if [ -s "$DB_PATH" ]; then
        echo "[legacy] Refusing to overwrite existing database; migration marker is absent" >&2
        exit 1
    fi

    echo "[legacy] Importing production data from $LEGACY_DATA"
    cp "$LEGACY_DATA/db.sqlite3" "$DB_PATH"

    if [ -d "$LEGACY_DATA/media" ]; then
        cp -a "$LEGACY_DATA/media/." "$MEDIA_DIR/"
    elif [ -d "$LEGACY_DATA/pdfs" ]; then
        mkdir -p "$MEDIA_DIR/pdfs"
        cp -a "$LEGACY_DATA/pdfs/." "$MEDIA_DIR/pdfs/"
    fi
    [ -d "$LEGACY_DATA/faiss_indexes" ] && cp -a "$LEGACY_DATA/faiss_indexes/." "$FAISS_DIR/"
    [ -d "$LEGACY_DATA/chroma_db" ] && cp -a "$LEGACY_DATA/chroma_db/." "$CHROMA_DIR/"
    [ -d "$LEGACY_DATA/staticfiles" ] && cp -a "$LEGACY_DATA/staticfiles/." "$STATIC_DIR/"
    touch "$MIGRATION_MARKER"
    echo "[legacy] Import complete"
fi

if [ ! -s "$DB_PATH" ]; then
    if [ "$DATA_BOOTSTRAP_MODE" = "empty" ] || [ "$DATA_BOOTSTRAP_MODE" = "bootstrap" ]; then
        echo "[db] Explicit $DATA_BOOTSTRAP_MODE mode; starting with an empty database"
    elif [ -f "$INIT_DB" ]; then
        echo "[db] Initializing from image baseline"
        printf 'pending\nseed_db=%s\nseed_media=%s\n' "$INIT_DB" "$INIT_MEDIA" > "$SEED_VALIDATION_MARKER"
        cp "$INIT_DB" "$DB_PATH"
        SEED_DB_COPIED=1
    else
        echo "[db] No database found; Django will create it during migrations"
    fi
fi

# A failed seed copy or validation leaves this marker behind. Retry the media
# copy and validation on every restart; a non-empty database is not proof that
# the declared seed was accepted.
if [ "$SEED_DB_COPIED" = "1" ] || [ -f "$SEED_VALIDATION_MARKER" ]; then
    if [ -d "$INIT_MEDIA" ]; then
        echo "[media] Initializing declared seed media"
        cp -a "$INIT_MEDIA/." "$MEDIA_DIR/"
    fi
fi

if [ "$DATA_BOOTSTRAP_MODE" != "empty" ] && [ "$DATA_BOOTSTRAP_MODE" != "bootstrap" ] \
    && [ -z "$(ls -A "$FAISS_DIR" 2>/dev/null)" ] \
    && [ -d "$INIT_FAISS" ] && [ "$(ls -A "$INIT_FAISS" 2>/dev/null)" ]; then
    echo "[faiss] Initializing indexes from image baseline"
    cp -a "$INIT_FAISS/." "$FAISS_DIR/"
fi

if [ -s "$DB_PATH" ]; then
    echo "[db] Running integrity check..."
    INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check")
    if [ "$INTEGRITY" != "ok" ]; then
        echo "[db] FATAL: database integrity check failed: $INTEGRITY" >&2
        exit 1
    fi
    echo "[db] Integrity check: ok"

fi

if [ -s "$CONTROL_DB_PATH" ]; then
    echo "[control] Running integrity check..."
    CONTROL_INTEGRITY=$(sqlite3 "$CONTROL_DB_PATH" "PRAGMA integrity_check")
    if [ "$CONTROL_INTEGRITY" != "ok" ]; then
        echo "[control] FATAL: control database integrity check failed: $CONTROL_INTEGRITY" >&2
        exit 1
    fi
fi

cd /app/flowdocs

PENDING_MIGRATIONS=$(python manage.py showmigrations --plan | grep -c '\[ \]' || true)
if [ "$PENDING_MIGRATIONS" -gt 0 ] && [ -s "$DB_PATH" ]; then
    echo "[recovery] Pending migrations detected; creating required recovery set"
    python manage.py emergency_db create --reason pre-migration
else
    echo "[recovery] No pending migrations; no restart backup required"
fi

if [ "${RUN_JSON_MIGRATIONS:-0}" = "1" ] && [ -x /usr/local/bin/apply_sqlite_json.py ]; then
    echo "[migrate] Applying explicit JSON migrations"
    python /usr/local/bin/apply_sqlite_json.py
fi

if [ "${STAGING_RUNTIME_ACTIVATION_ENABLED:-0}" = "1" ] \
    && [ "$RUNTIME_START_MODE" != "initial-bootstrap" ]; then
    echo "[migrate] Verifying immutable runtime has no pending migrations"
    python manage.py shell -c '
import sys
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor = MigrationExecutor(connection)
pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
sys.exit(1 if pending else 0)
'
else
    echo "[migrate] Running mutable bootstrap database migrations"
    python manage.py migrate --noinput
fi
echo "[migrate] Running stable control database migrations"
python manage.py migrate --database control --noinput
python manage.py maintenance_preflight

echo "[db] Running post-migration integrity check..."
POST_INTEGRITY=$(sqlite3 "$DB_PATH" "PRAGMA integrity_check")
if [ "$POST_INTEGRITY" != "ok" ]; then
    echo "[db] FATAL: post-migration integrity check failed: $POST_INTEGRITY" >&2
    exit 1
fi
echo "[db] Post-migration integrity: ok"

CONTROL_POST_INTEGRITY=$(sqlite3 "$CONTROL_DB_PATH" "PRAGMA integrity_check")
if [ "$CONTROL_POST_INTEGRITY" != "ok" ]; then
    echo "[control] FATAL: post-migration integrity check failed: $CONTROL_POST_INTEGRITY" >&2
    exit 1
fi
echo "[control] Post-migration integrity: ok"

if [ "$SEED_DB_COPIED" = "1" ] || [ -f "$SEED_VALIDATION_MARKER" ]; then
    echo "[data] Validating declared seed media before Gunicorn"
    python manage.py validate_runtime_data \
        --declared-seed "$INIT_DB" \
        --media-root "$MEDIA_DIR" \
        --mode strict
    rm -f "$SEED_VALIDATION_MARKER"
fi

if [ "${CREATE_SUPERUSER:-0}" = "1" ]; then
    : "${DJANGO_SUPERUSER_USERNAME:?DJANGO_SUPERUSER_USERNAME is required when CREATE_SUPERUSER=1}"
    : "${DJANGO_SUPERUSER_EMAIL:?DJANGO_SUPERUSER_EMAIL is required when CREATE_SUPERUSER=1}"
    : "${DJANGO_SUPERUSER_PASSWORD:?DJANGO_SUPERUSER_PASSWORD is required when CREATE_SUPERUSER=1}"
    echo "[auth] Ensuring configured superuser exists"
    python manage.py shell -c '
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ["DJANGO_SUPERUSER_USERNAME"]
user = User.objects.filter(username=username).first()
if user is None:
    user = User.objects.create_superuser(username, os.environ["DJANGO_SUPERUSER_EMAIL"], os.environ["DJANGO_SUPERUSER_PASSWORD"])
    print("Created configured superuser")
else:
    print("Configured superuser already exists")
if getattr(user, "role", None) != "superadmin":
    user.role = "superadmin"
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.save(update_fields=["role", "is_staff", "is_superuser", "is_active"])
    print("Reconciled configured superuser application role")
'
fi

if [ -n "$(ls -A "$CHROMA_DIR" 2>/dev/null)" ]; then
    echo "[chroma] $(find "$CHROMA_DIR" -type f | wc -l | tr -d ' ') files present"
fi

if [ -n "$(ls -A "$STATIC_DIR" 2>/dev/null)" ]; then
    echo "[static] Updating changed static files"
else
    echo "[static] Collecting static files"
fi
python manage.py collectstatic --noinput

if [ "${FIXTURE_BACKUP:-0}" = "1" ]; then
    JSON_FILE="$BACKUP_DIR/json_backups/data_backup_$TIMESTAMP.json"
    echo "[fixture] Generating requested JSON fixture backup"
    python manage.py dumpdata --natural-foreign --natural-primary --indent 2 > "$JSON_FILE"
fi

echo "============================================================"
echo "  Starting web runtime supervisor"
echo "============================================================"
exec python /app/flowdocs/runtime_supervisor.py --role web
