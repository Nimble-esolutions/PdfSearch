#!/bin/bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/app/data}"
DB_PATH="${SQLITE_DB_PATH:-$DATA_ROOT/db.sqlite3}"
MEDIA_DIR="${MEDIA_ROOT:-$DATA_ROOT/media}"
FAISS_DIR="${FAISS_INDEX_DIR:-$DATA_ROOT/faiss_indexes}"
CHROMA_DIR="${CHROMA_DIR:-$DATA_ROOT/chroma_db}"
STATIC_DIR="${STATIC_ROOT:-$DATA_ROOT/staticfiles}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_ROOT/backups}"
LEGACY_DATA="${LEGACY_DATA_ROOT:-/mnt/legacy}"
INIT_DB="/app/init/db.sqlite3"
INIT_FAISS="/app/init/faiss_indexes"
MIGRATION_MARKER="$DATA_ROOT/.legacy_migration_complete"

export DATA_ROOT DB_PATH MEDIA_DIR FAISS_DIR CHROMA_DIR STATIC_DIR BACKUP_DIR
export SECRET_KEY="${SECRET_KEY:-}"
export DEBUG="${DEBUG:-False}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-flowdocs.settings}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-}"
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-}"

printf '%s\n' '============================================================' '  FlowDocs - PDF Search Utility' '============================================================'
printf 'DEBUG=%s\nALLOWED_HOSTS=%s\nDATA_ROOT=%s\n' "$DEBUG" "$ALLOWED_HOSTS" "$DATA_ROOT"
printf '%s\n' '============================================================'

if [ -z "$SECRET_KEY" ] && [ "${ALLOW_INSECURE_DEFAULTS:-0}" != "1" ]; then
    echo "[config] ERROR: SECRET_KEY is required" >&2
    exit 1
fi

mkdir -p "$DATA_ROOT" "$MEDIA_DIR/pdfs" "$CHROMA_DIR" "$FAISS_DIR" \
    "$BACKUP_DIR/json_backups" "$BACKUP_DIR/chroma_backup" "$STATIC_DIR"
test -w "$DATA_ROOT" || { echo "[data] ERROR: $DATA_ROOT is not writable" >&2; exit 1; }

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
    if [ -f "$INIT_DB" ]; then
        echo "[db] Initializing from image baseline"
        cp "$INIT_DB" "$DB_PATH"
    else
        echo "[db] No database found; Django will create it during migrations"
    fi
fi

if [ -z "$(ls -A "$FAISS_DIR" 2>/dev/null)" ] && [ -d "$INIT_FAISS" ] && [ "$(ls -A "$INIT_FAISS" 2>/dev/null)" ]; then
    echo "[faiss] Initializing indexes from image baseline"
    cp -a "$INIT_FAISS/." "$FAISS_DIR/"
fi

TIMESTAMP=$(date +%F_%H%M%S)
if [ -s "$DB_PATH" ]; then
    BACKUP_FILE="$BACKUP_DIR/db_backup_$TIMESTAMP.sqlite3"
    echo "[backup] Creating consistent SQLite snapshot"
    sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
fi

cd /app/flowdocs

if [ "${RUN_JSON_MIGRATIONS:-0}" = "1" ] && [ -x /usr/local/bin/apply_sqlite_json.py ]; then
    echo "[migrate] Applying explicit JSON migrations"
    python /usr/local/bin/apply_sqlite_json.py
fi

echo "[migrate] Running Django migrations"
python manage.py migrate --noinput

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
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, os.environ["DJANGO_SUPERUSER_EMAIL"], os.environ["DJANGO_SUPERUSER_PASSWORD"])
    print("Created configured superuser")
else:
    print("Configured superuser already exists")
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
echo "  Starting Gunicorn on 0.0.0.0:8000 (4 workers)"
echo "============================================================"
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --timeout "${GUNICORN_TIMEOUT:-300}" \
    --access-logfile - \
    --error-logfile - \
    flowdocs.wsgi:application
