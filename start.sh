#!/bin/bash
set -e

echo "============================================================"
echo "  FlowDocs - PDF Search Utility"
echo "============================================================"

export SECRET_KEY="${SECRET_KEY:-django-insecure-change-me-in-production}"
export DEBUG="${DEBUG:-False}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-*}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-flowdocs.settings}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-}"
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-}"

echo "  DEBUG=$DEBUG"
echo "  ALLOWED_HOSTS=$ALLOWED_HOSTS"
echo "============================================================"

echo "[init] Ensuring directories..."
mkdir -p \
    /app/flowdocs \
    /app/flowdocs/media/pdfs \
    /app/flowdocs/chroma_db \
    /app/flowdocs/faiss_indexes \
    /app/backups \
    /app/backups/json_backups \
    /app/backups/chroma_backup \
    /app/staticfiles

if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /app/flowdocs /app/backups /app/staticfiles 2>/dev/null || true
    chmod -R 770 /app/flowdocs /app/backups /app/staticfiles 2>/dev/null || true
else
    chmod -R 770 /app/flowdocs /app/backups /app/staticfiles 2>/dev/null || true
fi

echo "[init] Directories ready."

DB_PATH="/app/flowdocs/db.sqlite3"
OLD_DB_PATH="/app/flowdocs/flowdocs/db.sqlite3"
BACKUP_DIR="/app/backups"
CHROMA_DIR="/app/flowdocs/chroma_db"
CHROMA_BACKUP_DIR="$BACKUP_DIR/chroma_backup"
FAISS_DIR="/app/flowdocs/faiss_indexes"
MEDIA_DIR="/app/flowdocs/media"

INIT_DB="/app/init/db.sqlite3"
INIT_FAISS="/app/init/faiss_indexes"

# ===============================================================
# Database init
# ===============================================================
if [ ! -s "$DB_PATH" ]; then
    echo "[db] No database found at $DB_PATH"
    if [ -f "$OLD_DB_PATH" ]; then
        echo "[db] Moving old database from $OLD_DB_PATH..."
        mv "$OLD_DB_PATH" "$DB_PATH"
    else
        LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/db_backup_*.sqlite3 2>/dev/null | head -n 1)
        if [ -n "$LATEST_BACKUP" ]; then
            echo "[db] Restoring from latest backup: $LATEST_BACKUP"
            cp "$LATEST_BACKUP" "$DB_PATH"
        elif [ -f "$INIT_DB" ]; then
            echo "[db] Initializing from baseline init data..."
            cp "$INIT_DB" "$DB_PATH"
        else
            echo "[db] Fresh database will be created."
        fi
    fi
else
    SIZE=$(stat -c%s "$DB_PATH" 2>/dev/null || stat -f%z "$DB_PATH" 2>/dev/null || echo "?")
    echo "[db] Database found (${SIZE} bytes)"
fi

UPLOAD_COUNT=$(find "$MEDIA_DIR/pdfs" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "[media] $UPLOAD_COUNT files in media/pdfs/"

# ===============================================================
# FAISS indexes
# ===============================================================
if [ -z "$(ls -A "$FAISS_DIR" 2>/dev/null)" ]; then
    echo "[faiss] No indexes found"
    if [ -d "$INIT_FAISS" ] && [ "$(ls -A "$INIT_FAISS" 2>/dev/null)" ]; then
        echo "[faiss] Initializing from baseline..."
        cp -r "$INIT_FAISS"/* "$FAISS_DIR"/
        echo "[faiss] $(ls -1 "$FAISS_DIR" | wc -l | tr -d ' ') files copied."
    else
        echo "[faiss] No init data available. Indexes will be built on first use."
    fi
else
    echo "[faiss] $(ls -1 "$FAISS_DIR" | wc -l | tr -d ' ') index files found."
fi

# ===============================================================
# Backup DB before migration
# ===============================================================
TIMESTAMP=$(date +%F_%H%M%S)
if [ -f "$DB_PATH" ]; then
    BACKUP_FILE="$BACKUP_DIR/db_backup_$TIMESTAMP.sqlite3"
    echo "[backup] Saving database to $BACKUP_FILE"
    cp "$DB_PATH" "$BACKUP_FILE"
fi

# ===============================================================
# Apply JSON -> SQLite migrations
# ===============================================================
echo "[migrate] Applying JSON -> SQLite migrations..."
if [ -x "/usr/local/bin/apply_sqlite_json.py" ]; then
    python /usr/local/bin/apply_sqlite_json.py || echo "[migrate] JSON migration had errors, continuing..."
else
    echo "[migrate] JSON migration script not found, skipping."
fi

# ===============================================================
# Django migrations
# ===============================================================
echo "[migrate] Running Django migrations..."
cd /app/flowdocs
python manage.py migrate --noinput || echo "[migrate] WARNING: Migration step had errors, continuing..."

# ===============================================================
# Superuser
# ===============================================================
echo "[auth] Checking admin superuser..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@gmail.com', 'admin123')
    print('Created superuser: admin / admin123')
else:
    print('Superuser already exists.')
"

# ===============================================================
# ChromaDB restore
# ===============================================================
if [ ! -d "$CHROMA_DIR" ] || [ -z "$(ls -A "$CHROMA_DIR" 2>/dev/null)" ]; then
    LATEST_CHROMA=$(ls -dt "$CHROMA_BACKUP_DIR"/chroma_backup_* 2>/dev/null | head -n 1)
    if [ -n "$LATEST_CHROMA" ]; then
        echo "[chroma] Restoring from backup: $LATEST_CHROMA"
        cp -r "$LATEST_CHROMA"/* "$CHROMA_DIR"/
    else
        echo "[chroma] No backup found. Starting fresh vector store."
    fi
else
    COUNT=$(find "$CHROMA_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "[chroma] $COUNT files in vector store."
fi

# ===============================================================
# Collect static
# ===============================================================
echo "[static] Collecting static files..."
python manage.py collectstatic --noinput --clear || echo "[static] WARNING: collectstatic skipped."

# ===============================================================
# JSON fixture backup
# ===============================================================
JSON_BACKUP_DIR="$BACKUP_DIR/json_backups"
JSON_FILE="$JSON_BACKUP_DIR/data_backup_$TIMESTAMP.json"
echo "[fixture] Generating JSON backup..."
python manage.py dumpdata --natural-foreign --natural-primary --indent 2 > "$JSON_FILE" 2>/dev/null && \
    echo "[fixture] Saved to $JSON_FILE" || \
    echo "[fixture] JSON backup skipped."

# ===============================================================
# ChromaDB backup
# ===============================================================
if [ -d "$CHROMA_DIR" ] && [ "$(ls -A "$CHROMA_DIR" 2>/dev/null)" ]; then
    CHROMA_BCK="$CHROMA_BACKUP_DIR/chroma_backup_$TIMESTAMP"
    mkdir -p "$CHROMA_BCK"
    cp -r "$CHROMA_DIR"/* "$CHROMA_BCK"/ 2>/dev/null || true
    echo "[chroma] Backup saved to $CHROMA_BCK"
fi

# ===============================================================
# Start Gunicorn
# ===============================================================
echo "============================================================"
echo "  Starting Gunicorn on 0.0.0.0:8000 (4 workers)"
echo "============================================================"

exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    flowdocs.wsgi:application
