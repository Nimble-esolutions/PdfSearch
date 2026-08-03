#!/bin/bash
BACKUP_ROLE="${BACKUP_ROLE:-disabled}"
echo "[worker] BACKUP_ROLE=$BACKUP_ROLE MAINTENANCE_SCHEDULER=${MAINTENANCE_SCHEDULER_ENABLED:-0}"
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/app/data}"
DATA_CONTROL_ROOT="${DATA_CONTROL_ROOT:-/app/data-control}"
CONTROL_DB_PATH="${CONTROL_DB_PATH:-$DATA_CONTROL_ROOT/control.sqlite3}"
PDF_CACHE_DIR="${PDF_CACHE_DIR:-$DATA_ROOT/pdf_cache}"
VAULT_RESTORE_ROOT="${VAULT_RESTORE_ROOT:-$DATA_ROOT/restore-quarantine}"
RUNTIME_GENERATIONS_ROOT="${RUNTIME_GENERATIONS_ROOT:-$DATA_ROOT/runtime-generations}"
export DATA_CONTROL_ROOT CONTROL_DB_PATH PDF_CACHE_DIR VAULT_RESTORE_ROOT RUNTIME_GENERATIONS_ROOT
mkdir -p "$DATA_ROOT" "$DATA_ROOT/media/pdfs" "$DATA_ROOT/faiss_indexes" \
  "$PDF_CACHE_DIR" "$DATA_ROOT/chroma_db" \
  "$DATA_ROOT/backups/json_backups" "$DATA_CONTROL_ROOT" \
  "$VAULT_RESTORE_ROOT" "$RUNTIME_GENERATIONS_ROOT"
chown appuser:appuser "$DATA_ROOT" "$DATA_CONTROL_ROOT" 2>/dev/null || true
chown -R appuser:appuser \
  "$VAULT_RESTORE_ROOT" "$RUNTIME_GENERATIONS_ROOT" 2>/dev/null || true

gosu appuser:appuser bash -lc \
  'cd /app/flowdocs && python manage.py startup_restore_preflight'

RUNTIME_START_MODE="disabled"
if [ "${STAGING_RUNTIME_ACTIVATION_ENABLED:-0}" = "1" ]; then
  RUNTIME_START_MODE="$(
    gosu appuser:appuser python /app/flowdocs/runtime_paths_cli.py \
      generation --allow-initial-bootstrap
  )"
  if [ "$RUNTIME_START_MODE" = "initial-bootstrap" ] \
      && [ "${STAGING_INITIAL_ACTIVATION_ENABLED:-0}" != "1" ]; then
    echo "[activation] ERROR: initial bootstrap requires explicit opt-in" >&2
    exit 1
  fi
fi

if [ "${STAGING_RUNTIME_ACTIVATION_ENABLED:-0}" = "1" ] \
    && [ "$RUNTIME_START_MODE" != "initial-bootstrap" ]; then
  gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py shell -c "
import sys
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
executor = MigrationExecutor(connection)
sys.exit(1 if executor.migration_plan(executor.loader.graph.leaf_nodes()) else 0)
"'
else
  PENDING_MIGRATIONS=$(gosu appuser:appuser bash -lc \
    "cd /app/flowdocs && python manage.py showmigrations --plan | grep -c '\\[ \\]'" || true)
  if [ "$PENDING_MIGRATIONS" -gt 0 ] && [ -s "${SQLITE_DB_PATH:-$DATA_ROOT/db.sqlite3}" ]; then
    gosu appuser:appuser bash -lc \
      'cd /app/flowdocs && python manage.py emergency_db create --reason pre-migration'
  fi
  gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py migrate --noinput'
fi
gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py migrate --database control --noinput'
gosu appuser:appuser bash -lc 'python /app/flowdocs/manage.py maintenance_preflight'

exec gosu appuser:appuser bash -lc 'cd /app/flowdocs && python runtime_supervisor.py --role maintenance'
