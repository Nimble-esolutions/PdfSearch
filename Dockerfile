# =====================================================================
# 🐍 Use Python 3.11 slim image for better performance
# =====================================================================
FROM python:3.11-slim

# =====================================================================
# 🌱 Environment Variables
# =====================================================================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_USER=appuser \
    APP_HOME=/home/appuser \
    WORKDIR=/app \
    SQLITE_DB_PATH=/app/flowdocs/flowdocs/db.sqlite3 \
    MIGRATIONS_JSON="/app/flowdocs/" \
    FORCE_MIGRATIONS=0

# =====================================================================
# 📂 Working Directory
# =====================================================================
WORKDIR ${WORKDIR}

# =====================================================================
# 🧰 System Dependencies
# =====================================================================
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-mar \
        poppler-utils \
        libpq-dev \
        gcc \
        g++ \
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
        pkg-config \
        curl \
        sqlite3 \
        gosu \
        bash && \
    rm -rf /var/lib/apt/lists/*

# =====================================================================
# 📦 Python Dependencies
# =====================================================================
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# =====================================================================
# 🧾 Application Code
# =====================================================================
COPY . .
COPY .env* ./ 
#|| true

# Make start script executable (if present)
RUN [ -f ./start.sh ] && chmod +x ./start.sh 
#|| true

# Create static and media directories
RUN mkdir -p /app/staticfiles /app/media

# Collect static files (for Django-based apps)
RUN bash -lc 'if [ -d "flowdocs" ] && [ -f "flowdocs/manage.py" ]; then \
      cd flowdocs && STATIC_ROOT=/app/staticfiles python manage.py collectstatic --noinput --clear; \
    else \
      echo "collectstatic skipped (flowdocs/manage.py not found)"; \
    fi'

# =====================================================================
# 👤 Create Non-Root App User
# =====================================================================
RUN adduser --disabled-password --gecos '' ${APP_USER} && \
    mkdir -p ${APP_HOME} && \
    chown -R ${APP_USER}:${APP_USER} ${APP_HOME} ${WORKDIR}

# =====================================================================
# 🧮 SQLite JSON Migration Runner
# =====================================================================
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
            print(f"[migrate] No SQL found in {path}; expecting keys like 'sql' or 'migrations'. Skipping.")
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

# =====================================================================
# 🚀 Entrypoint Script
# =====================================================================
RUN cat > /usr/local/bin/entrypoint.sh << 'EOSH'
#!/usr/bin/env bash
set -euo pipefail

export APP_USER="${APP_USER:-appuser}"
export APP_HOME="${APP_HOME:-/home/appuser}"
export SQLITE_DB_PATH="${SQLITE_DB_PATH:-/app/flowdocs/flowdocs/db.sqlite3}"
export MIGRATIONS_JSON="${MIGRATIONS_JSON:-/app/flowdocs}"
export FORCE_MIGRATIONS="${FORCE_MIGRATIONS:-0}"

# Ensure dirs & ownership even with mounted volumes
mkdir -p "${APP_HOME}" "$(dirname "${SQLITE_DB_PATH}")" /app/staticfiles /app/media /app/backups

# 🔧 Fix permissions for SQLite and backups (handles mounted volumes)
echo "[entrypoint] Fixing permissions for /app/flowdocs and /app/backups"
chown -R "${APP_USER}:${APP_USER}" /app/flowdocs/flowdocs /app/backups /app/staticfiles /app/media || true
chmod -R 770 /app/flowdocs/flowdocs /app/backups /app/staticfiles /app/media || true


# Run migrations as root (SQLite file is created if missing)
echo "[entrypoint] Running JSON -> SQLite migrations (DB=${SQLITE_DB_PATH})"
python3 /usr/local/bin/apply_sqlite_json.py || {
    echo "[entrypoint] Migration step failed"
    exit 1
}

# Drop privileges and start app
echo "[entrypoint] Starting app as ${APP_USER}"
exec gosu "${APP_USER}:${APP_USER}" "$@"
EOSH

RUN chmod +x /usr/local/bin/entrypoint.sh

# =====================================================================
# 🌐 Networking & Health Check
# =====================================================================
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

# =====================================================================
# 🎯 Default Entrypoint & Command
# =====================================================================
ENTRYPOINT ["entrypoint.sh"]
CMD ["./start.sh"]
