#!/bin/bash
set -e

echo "[entrypoint] Running as root — fixing persistent data permissions..."

DATA_ROOT="${DATA_ROOT:-/app/data}"
DATA_CONTROL_ROOT="${DATA_CONTROL_ROOT:-/app/data-control}"
MEDIA_ROOT="${MEDIA_ROOT:-$DATA_ROOT/media}"
PDF_CACHE_DIR="${PDF_CACHE_DIR:-$DATA_ROOT/pdf_cache}"
CHROMA_DIR="${CHROMA_DIR:-$DATA_ROOT/chroma_db}"
FAISS_INDEX_DIR="${FAISS_INDEX_DIR:-$DATA_ROOT/faiss_indexes}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_ROOT/backups}"
STATIC_ROOT="${STATIC_ROOT:-$DATA_ROOT/staticfiles}"
VAULT_RESTORE_ROOT="${VAULT_RESTORE_ROOT:-$DATA_ROOT/restore-quarantine}"
RUNTIME_GENERATIONS_ROOT="${RUNTIME_GENERATIONS_ROOT:-$DATA_ROOT/runtime-generations}"

mkdir -p \
    "$DATA_ROOT" \
    "$DATA_CONTROL_ROOT" \
    "$MEDIA_ROOT/pdfs" \
    "$PDF_CACHE_DIR" \
    "$CHROMA_DIR" \
    "$FAISS_INDEX_DIR" \
    "$BACKUP_DIR/json_backups" \
    "$BACKUP_DIR/chroma_backup" \
    "$STATIC_ROOT" \
    "$VAULT_RESTORE_ROOT" \
    "$RUNTIME_GENERATIONS_ROOT"

chown appuser:appuser "$DATA_ROOT" "$DATA_CONTROL_ROOT" 2>/dev/null || true
for mutable_path in \
    "$MEDIA_ROOT" "$PDF_CACHE_DIR" "$CHROMA_DIR" "$FAISS_INDEX_DIR" \
    "$BACKUP_DIR" "$STATIC_ROOT"; do
    chown -R appuser:appuser "$mutable_path" 2>/dev/null || true
done
chmod 770 "$DATA_ROOT" "$DATA_CONTROL_ROOT" 2>/dev/null || true

test -w "$DATA_ROOT" || {
    echo "[entrypoint] ERROR: DATA_ROOT=$DATA_ROOT is not writable" >&2
    exit 1
}

test -w "$DATA_CONTROL_ROOT" || {
    echo "[entrypoint] ERROR: DATA_CONTROL_ROOT=$DATA_CONTROL_ROOT is not writable" >&2
    exit 1
}

echo "[entrypoint] Data volume ready. Dropping to appuser..."
exec gosu appuser:appuser ./start.sh
