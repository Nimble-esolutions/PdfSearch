# =====================================================================
# STAGE 1: Builder - compile Python wheels, discard build tools
# =====================================================================
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
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

COPY requirements-web.lock .

RUN pip wheel --require-hashes --wheel-dir /build/wheels -r requirements-web.lock

# =====================================================================
# STAGE 2: Runtime - minimal image with only runtime libraries
# =====================================================================
FROM python:3.10-slim AS runtime

ARG OCI_REVISION=unknown
LABEL org.opencontainers.image.revision="${OCI_REVISION}"

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

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    sqlite3 \
     gosu \
     bash \
     curl \
     gettext \
     fontconfig \
     fonts-noto-core

COPY --from=builder /build/wheels /wheels
RUN pip install --no-compile --no-index --no-deps /wheels/*.whl && \
    pip uninstall -y setuptools wheel && \
    rm -rf /wheels

COPY . .

RUN chmod +x ./start.sh ./docker-entrypoint.sh ./worker-entrypoint.sh 2>/dev/null || true

RUN bash -lc 'cd /app/flowdocs && \
    SECRET_KEY=build-only-not-for-runtime DEBUG=True ALLOW_INSECURE_DEFAULTS=1 \
    python manage.py compilemessages'

RUN mkdir -p /app/data/media/pdfs /app/data/chroma_db /app/data/faiss_indexes \
    /app/data/backups/json_backups /app/data/backups/chroma_backup /app/data/staticfiles && \
    [ -d /app/init ] && chmod -R 755 /app/init || true

RUN groupadd -g ${APP_UID} ${APP_USER} 2>/dev/null || true && \
    useradd -u ${APP_UID} -g ${APP_UID} -m -s /bin/bash ${APP_USER} 2>/dev/null || true && \
    mkdir -p ${APP_HOME} && \
    chown -R ${APP_USER}:${APP_USER} ${APP_HOME} /app

COPY scripts/runtime/apply_sqlite_json.py /usr/local/bin/apply_sqlite_json.py
RUN chmod +x /usr/local/bin/apply_sqlite_json.py

RUN bash -lc 'cd /app/flowdocs && \
    DATA_ROOT=/app/data STATIC_ROOT=/app/data/staticfiles \
    DEBUG=True ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=build-only-not-for-runtime \
    python manage.py collectstatic --noinput'

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/ || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
