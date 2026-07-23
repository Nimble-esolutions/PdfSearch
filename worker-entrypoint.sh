#!/bin/bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/app/data}"
mkdir -p "$DATA_ROOT" "$DATA_ROOT/media/pdfs" "$DATA_ROOT/faiss_indexes" \
  "$DATA_ROOT/chroma_db" "$DATA_ROOT/backups/json_backups"
chown -R appuser:appuser "$DATA_ROOT" 2>/dev/null || true
exec gosu appuser:appuser bash -lc 'cd /app/flowdocs && python manage.py run_maintenance_jobs'
