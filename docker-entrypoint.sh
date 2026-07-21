#!/bin/bash
set -e

echo "[entrypoint] Running as root — fixing volume permissions..."

mkdir -p \
    /app/flowdocs \
    /app/flowdocs/media/pdfs \
    /app/flowdocs/chroma_db \
    /app/flowdocs/faiss_indexes \
    /app/backups \
    /app/backups/json_backups \
    /app/backups/chroma_backup \
    /app/staticfiles

chown -R appuser:appuser \
    /app/flowdocs \
    /app/backups \
    /app/staticfiles 2>/dev/null || true

chmod -R 770 \
    /app/flowdocs \
    /app/backups \
    /app/staticfiles 2>/dev/null || true

echo "[entrypoint] Permissions fixed. Dropping to appuser..."

exec gosu appuser:appuser ./start.sh
