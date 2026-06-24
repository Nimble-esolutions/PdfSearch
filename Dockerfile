# ===============================
# Python base image
# ===============================
FROM python:3.10-slim

# ===============================
# Environment variables
# ===============================
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=flowdocs.settings

# ===============================
# Work directory
# ===============================
WORKDIR /app

# ===============================
# System dependencies (FIXED)
# ===============================
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    sqlite3 \
    curl \
    pkg-config \
    cmake \
    meson \
    gettext \
    libcairo2 \
    libcairo2-dev \
    libgirepository1.0-dev \
    gir1.2-cairo-1.0 \
    && rm -rf /var/lib/apt/lists/*

# ===============================
# Python dependencies
# ===============================
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ===============================
# Copy project
# ===============================
COPY . .

# ===============================
# Permissions
# ===============================
RUN chmod +x start.sh

# ===============================
# Port
# ===============================
EXPOSE 8000

# ===============================
# Healthcheck
# ===============================
HEALTHCHECK --interval=30s --timeout=30s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# ===============================
# Entrypoint
# ===============================
ENTRYPOINT ["./start.sh"]
