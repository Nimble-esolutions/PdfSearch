#!/bin/bash
BACKUP_ROLE="${BACKUP_ROLE:-disabled}"
echo "[worker] BACKUP_ROLE=$BACKUP_ROLE MAINTENANCE_SCHEDULER=${MAINTENANCE_SCHEDULER_ENABLED:-0}"
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/app/data}"
DATA_CONTROL_ROOT="${DATA_CONTROL_ROOT:-/app/data-control}"
CONTROL_DB_PATH="${CONTROL_DB_PATH:-$DATA_CONTROL_ROOT/control.sqlite3}"
PDF_CACHE_DIR="${PDF_CACHE_DIR:-$DATA_ROOT/pdf_cache}"
export DATA_CONTROL_ROOT CONTROL_DB_PATH PDF_CACHE_DIR
mkdir -p "$DATA_ROOT" "$DATA_ROOT/media/pdfs" "$DATA_ROOT/faiss_indexes" \
  "$PDF_CACHE_DIR" "$DATA_ROOT/chroma_db" \
  "$DATA_ROOT/backups/json_backups" "$DATA_CONTROL_ROOT"
chown -R appuser:appuser "$DATA_ROOT" 2>/dev/null || true
chown -R appuser:appuser "$DATA_CONTROL_ROOT" 2>/dev/null || true

gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py migrate --noinput'
gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py migrate --database control --noinput'

exec gosu appuser:appuser bash -lc 'cd /app/flowdocs && python manage.py run_maintenance_jobs'
