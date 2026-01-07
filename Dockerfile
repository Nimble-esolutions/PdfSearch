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
# Set work directory
# ===============================
WORKDIR /app

# ===============================
# System dependencies
# ===============================
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ===============================
# Python dependencies
# ===============================
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ===============================
# Copy project files
# ===============================
COPY . .

# ===============================
# Make start.sh executable
# ===============================
RUN chmod +x start.sh

# ===============================
# Expose port
# ===============================
EXPOSE 8000

# ===============================
# Health check
# ===============================
HEALTHCHECK --interval=30s --timeout=30s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# ===============================
# Entrypoint
# ===============================
ENTRYPOINT ["./start.sh"]
