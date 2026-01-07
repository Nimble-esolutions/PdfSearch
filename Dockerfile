# =====================================================================
# 🏗️ STAGE 1: Builder (Compile dependencies)
# =====================================================================
FROM python:3.10-slim AS builder

# Preventing python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /build

# 🧰 Install Build Dependencies
# Includes gcc, g++, make, cmake, cargo (for certain python libs), and dev libraries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    cmake \
    cargo \
    libpq-dev \
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
    pkg-config \
    curl && \
    rm -rf /var/lib/apt/lists/*

# 📦 Install Python Dependencies
# We build wheels to be copied to the runtime stage
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels -r requirements.txt

# =====================================================================
# 🚀 STAGE 2: Runtime (Minimal Image)
# =====================================================================
FROM python:3.10-slim AS runtime

# 🌱 Environment Variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_USER=appuser \
    APP_HOME=/home/appuser \
    WORKDIR=/app \
    SQLITE_DB_PATH=/app/flowdocs/db.sqlite3 \
    MIGRATIONS_JSON="/app/flowdocs/" \
    FORCE_MIGRATIONS=0

WORKDIR ${WORKDIR}

# 🧰 Install Runtime Dependencies ONLY
# No gcc, g++, or dev libraries here
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
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
    curl && \
    rm -rf /var/lib/apt/lists/*

# 👤 Create Non-Root App User
RUN groupadd -g 1000 ${APP_USER} 2>/dev/null || true && \
    useradd -u 1000 -g 1000 -m -s /bin/bash ${APP_USER} 2>/dev/null || \
    adduser --disabled-password --gecos '' --uid 1000 --gid 1000 ${APP_USER} && \
    mkdir -p ${APP_HOME} && \
    chown -R ${APP_USER}:${APP_USER} ${APP_HOME} ${WORKDIR}

# 📦 Install Python Dependencies from Builder Wheels
COPY --from=builder /build/wheels /wheels
COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl && \
    rm -rf /wheels

# =====================================================================
# 🧾 Application Code
# =====================================================================
COPY . .
COPY .env* ./ || true

# Make start script executable
RUN [ -f ./start.sh ] && chmod +x ./start.sh 

# Create directories
RUN mkdir -p /app/staticfiles /app/media

# =====================================================================
# 📦 Init Data (Baseline for Fresh Deployments)
# =====================================================================
RUN mkdir -p /app/init/faiss_indexes
COPY init/db.sqlite3 /app/init/db.sqlite3
COPY init/faiss_indexes/ /app/init/faiss_indexes/
RUN chmod -R 644 /app/init/*.sqlite3 && \
    chmod -R 755 /app/init/faiss_indexes

# Collect static files
RUN bash -lc 'if [ -d "flowdocs" ] && [ -f "flowdocs/manage.py" ]; then \
    cd flowdocs && STATIC_ROOT=/app/staticfiles python manage.py collectstatic --noinput --clear; \
    else \
    echo "collectstatic skipped (flowdocs/manage.py not found)"; \
    fi'

# =====================================================================
# 🧮 SQLite JSON Migration Runner (Helper Script)
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
# 🌐 Networking & Health Check
# =====================================================================
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

# =====================================================================
# 🎯 Default Entrypoint & Command
# =====================================================================
ENTRYPOINT ["./start.sh"]
