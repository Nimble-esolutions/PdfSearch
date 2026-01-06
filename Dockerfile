# =====================================================================
# 🏗️ Stage 1: Build Stage (for compiling Python packages)
# =====================================================================
FROM python:3.10-slim as builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install build dependencies only
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libpq-dev \
        libcairo2-dev \
        libpango-1.0-dev \
        libpangoft2-1.0-dev \
        libgdk-pixbuf-2.0-dev \
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
        build-essential \
        cargo \
        cmake && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --user -r requirements.txt

# =====================================================================
# 🚀 Stage 2: Runtime Stage (final image)
# =====================================================================
FROM python:3.10-slim

# =====================================================================
# 🌱 Environment Variables
# =====================================================================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_USER=appuser \
    APP_HOME=/home/appuser \
    WORKDIR=/app \
    SQLITE_DB_PATH=/app/flowdocs/db.sqlite3 \
    MIGRATIONS_JSON="/app/flowdocs/" \
    FORCE_MIGRATIONS=0 \
    PATH=/home/appuser/.local/bin:$PATH

# =====================================================================
# 📂 Working Directory
# =====================================================================
WORKDIR ${WORKDIR}

# =====================================================================
# 🧰 Runtime System Dependencies (no build tools!)
# =====================================================================
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-mar \
        poppler-utils \
        libpq5 \
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libjpeg62-turbo \
        zlib1g \
        libpng16-16 \
        libtiff5 \
        libwebp6 \
        libopenjp2-7 \
        libfreetype6 \
        liblcms2-2 \
        libharfbuzz0b \
        libfribidi0 \
        libxcb1 \
        libffi8 \
        curl \
        sqlite3 \
        gosu \
        bash && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# =====================================================================
# 👤 Create Non-Root App User (UID/GID 1000:1000 to match host user)
# =====================================================================
RUN groupadd -g 1000 ${APP_USER} 2>/dev/null || true && \
    useradd -u 1000 -g 1000 -m -s /bin/bash ${APP_USER} 2>/dev/null || \
    adduser --disabled-password --gecos '' --uid 1000 --gid 1000 ${APP_USER} && \
    mkdir -p ${APP_HOME} && \
    chown -R ${APP_USER}:${APP_USER} ${APP_HOME} ${WORKDIR}

# =====================================================================
# 📦 Copy Python packages from builder stage
# =====================================================================
COPY --from=builder --chown=${APP_USER}:${APP_USER} /root/.local /home/${APP_USER}/.local

# =====================================================================
# 🧾 Application Code
# =====================================================================
COPY --chown=${APP_USER}:${APP_USER} . .

# Make start script executable
RUN [ -f ./start.sh ] && chmod +x ./start.sh || true

# Create static and media directories
RUN mkdir -p /app/staticfiles /app/media && \
    chown -R ${APP_USER}:${APP_USER} /app/staticfiles /app/media

# Collect static files (for Django-based apps)
RUN bash -lc 'if [ -d "flowdocs" ] && [ -f "flowdocs/manage.py" ]; then \
      cd flowdocs && STATIC_ROOT=/app/staticfiles python manage.py collectstatic --noinput --clear; \
    else \
      echo "collectstatic skipped (flowdocs/manage.py not found)"; \
    fi' && \
    chown -R ${APP_USER}:${APP_USER} /app/staticfiles

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
# Note: Entrypoint logic is handled by start.sh script

# =====================================================================
# 🌐 Networking & Health Check
# =====================================================================
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

# =====================================================================
# 🎯 Default Entrypoint & Command
# =====================================================================
ENTRYPOINT ["./start.sh"]
