# syntax=docker/dockerfile:1

# =====================================================================
# STAGE 1: Builder - compile Python wheels, discard build tools
# =====================================================================
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    cmake \
    pkg-config \
    libcairo2-dev \
    libjpeg-dev \
    zlib1g-dev \
    libpng-dev \
    libtiff-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libxcb1-dev \
    libffi-dev \
    libpq-dev \
    curl

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip wheel --wheel-dir /build/wheels -r requirements.txt

# =====================================================================
# STAGE 2: Runtime - minimal image with only runtime libraries
# =====================================================================
FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_USER=appuser \
    APP_UID=1000 \
    APP_HOME=/home/appuser \
    DATA_ROOT=/app/data \
    SQLITE_DB_PATH=/app/data/db.sqlite3 \
    MIGRATIONS_JSON=/app/flowdocs/ \
    FORCE_MIGRATIONS=0

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-mar \
    poppler-utils \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libpq5 \
    libopenjp2-7 \
    libfreetype6 \
    liblcms2-2 \
    libharfbuzz0b \
    libfribidi0 \
    libxcb1 \
    sqlite3 \
    gosu \
    bash \
    curl

COPY --from=builder /build/wheels /wheels
COPY --from=builder /build/requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --no-compile --no-index --find-links=/wheels -r requirements.txt && \
    rm -rf /wheels

COPY . .

RUN chmod +x ./start.sh ./docker-entrypoint.sh 2>/dev/null || true

RUN mkdir -p /app/data/media/pdfs /app/data/chroma_db /app/data/faiss_indexes \
    /app/data/backups/json_backups /app/data/backups/chroma_backup /app/data/staticfiles && \
    [ -d /app/init ] && chmod -R 755 /app/init || true

RUN groupadd -g ${APP_UID} ${APP_USER} 2>/dev/null || true && \
    useradd -u ${APP_UID} -g ${APP_UID} -m -s /bin/bash ${APP_USER} 2>/dev/null || true && \
    mkdir -p ${APP_HOME} && \
    chown -R ${APP_USER}:${APP_USER} ${APP_HOME} /app

RUN cat > /usr/local/bin/apply_sqlite_json.py << 'PYCODE'
#!/usr/bin/env python3
import os, sys, glob, json, hashlib, sqlite3, time

DB_PATH = os.environ.get("SQLITE_DB_PATH", "/home/appuser/app.db")
HOME = os.environ.get("APP_HOME", "/home/appuser")
force = os.environ.get("FORCE_MIGRATIONS", "0") == "1"

def find_json_files():
    env_paths = os.environ.get("MIGRATIONS_JSON", "").strip()
    files = []
    if env_paths:
        for p in env_paths.split(":"):
            p = p.strip()
            if not p:
                continue
            if os.path.isdir(p):
                files.extend(sorted(glob.glob(os.path.join(p, "*.json"))))
            else:
                files.append(p)
    else:
        files = sorted(glob.glob(os.path.join(HOME, "*.json")))
    seen, ordered = set(), []
    for f in files:
        f = os.path.abspath(f)
        if os.path.isfile(f) and f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered

def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def extract_sql_list(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("sql"), list):
            return [s for s in payload["sql"] if isinstance(s, str)]
        if isinstance(payload.get("migrations"), list):
            return [s for s in payload["migrations"] if isinstance(s, str)]
        steps = payload.get("steps") or payload.get("operations") or []
        out = []
        if isinstance(steps, list):
            for it in steps:
                if isinstance(it, dict):
                    if isinstance(it.get("sql"), str):
                        out.append(it["sql"])
                    elif isinstance(it.get("execute"), str):
                        out.append(it["execute"])
        return out
    elif isinstance(payload, list):
        out = []
        for it in payload:
            if isinstance(it, dict):
                if isinstance(it.get("sql"), str):
                    out.append(it["sql"])
                elif isinstance(it.get("execute"), str):
                    out.append(it["execute"])
            elif isinstance(it, str):
                out.append(it)
        return out
    return []

def ensure_meta_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS __migrations_applied (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            applied_at INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mig_unique 
        ON __migrations_applied(file_path, file_hash)
    """)

def already_applied(cur, path, h):
    cur.execute("SELECT 1 FROM __migrations_applied WHERE file_path=? AND file_hash=? LIMIT 1", (path, h))
    return cur.fetchone() is not None

def record_applied(cur, path, h):
    cur.execute("INSERT OR IGNORE INTO __migrations_applied(file_path, file_hash, applied_at) VALUES (?, ?, ?)",
                (path, h, int(time.time())))

def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    open(DB_PATH, "ab").close()
    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None
    cur = conn.cursor()
    ensure_meta_table(cur)
    files = find_json_files()
    if not files:
        print("[migrate] No JSON files found; skipping.")
        conn.close()
        return 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            print(f"[migrate] Skipping {path}: cannot parse JSON ({e})")
            continue
        h = file_hash(path)
        if not force and already_applied(cur, path, h):
            print(f"[migrate] Already applied {os.path.basename(path)}; skipping.")
            continue
        sql_list = extract_sql_list(payload)
        if not sql_list:
            print(f"[migrate] No SQL found in {path}; skipping.")
            continue
        try:
            cur.execute("BEGIN")
            for stmt in sql_list:
                cur.execute(stmt)
            record_applied(cur, path, h)
            cur.execute("COMMIT")
            print(f"[migrate] Applied {len(sql_list)} statements from {os.path.basename(path)}")
        except Exception as e:
            cur.execute("ROLLBACK")
            print(f"[migrate] ERROR applying {path}: {e}")
            return 1
    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
PYCODE

RUN chmod +x /usr/local/bin/apply_sqlite_json.py

RUN bash -lc 'cd /app/flowdocs && \
    DATA_ROOT=/app/data STATIC_ROOT=/app/data/staticfiles \
    DEBUG=True ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=build-only-not-for-runtime \
    python manage.py collectstatic --noinput'

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
