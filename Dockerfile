# Use Python 3.11 slim image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Set work directory
WORKDIR /app

# Install system dependencies (combined into one layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    build-essential \
    python3-dev \
    libmupdf-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Copy .env if exists
#COPY .env* ./ || true

# Make start.sh executable
RUN chmod +x start.sh

# Create directories for static and media files
RUN mkdir -p /app/staticfiles /app/media

# Collect static files
RUN cd flowdocs && STATIC_ROOT=/app/staticfiles python manage.py collectstatic --noinput --clear

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser \
 && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# Run application
CMD ["./start.sh"]
