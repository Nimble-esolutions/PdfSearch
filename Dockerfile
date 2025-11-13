# -------------------- Base Image --------------------
FROM python:3.11-slim

# Set work directory
ENV APP_HOME=/app/flowdocs
WORKDIR ${APP_HOME}

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# -------------------- System Dependencies --------------------
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libpq-dev \
    pkg-config \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    libpng-dev \
    libblas-dev \
    liblapack-dev \
    gfortran \
    git \
    && rm -rf /var/lib/apt/lists/*

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
ENTRYPOINT ["/app/flowdocs/entrypoint.sh"]

# -------------------- Default Command --------------------
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "flowdocs.wsgi:application"]
