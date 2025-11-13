# -------------------- Base Image --------------------
FROM python:3.11-slim

# Set work directory
ENV APP_HOME=/app/flowdocs
WORKDIR ${APP_HOME}

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# -------------------- System Dependencies --------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-mar \
        poppler-utils \
        libpq-dev \
        gcc \
        g++ \
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
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
# -------------------- Install Python Dependencies --------------------
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# -------------------- Copy Application Files --------------------
COPY . ${APP_HOME}

# Create necessary directories (some may be volume-mounted later)
RUN mkdir -p \
    ${APP_HOME}/chroma_db \
    ${APP_HOME}/media \
    /app/backups && \
    adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser ${APP_HOME} /app/backups

# -------------------- Switch to non-root user --------------------
USER appuser

# -------------------- Entrypoint --------------------
# Make start script executable (if present)
RUN [ -f ./start.sh ] && chmod +x ./start.sh 
#|| true

# -------------------- Default Command --------------------
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "flowdocs.wsgi:application"]
