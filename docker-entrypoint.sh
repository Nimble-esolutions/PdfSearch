#!/bin/bash
set -e

echo "[entrypoint] Running as root — fixing persistent data permissions..."

mkdir -p \
    /app/data \
    /app/data/media/pdfs \
    /app/data/chroma_db \
    /app/data/faiss_indexes \
    /app/data/backups/json_backups \
    /app/data/backups/chroma_backup \
    /app/data/staticfiles

chown -R appuser:appuser /app/data 2>/dev/null || true
chmod -R 770 /app/data 2>/dev/null || true

test -w /app/data || {
    echo "[entrypoint] ERROR: DATA_ROOT=/app/data is not writable" >&2
    exit 1
}

echo "[entrypoint] Data volume ready. Dropping to appuser..."
exec gosu appuser:appuser ./start.sh
