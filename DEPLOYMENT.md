# FlowDocs - PDF Search Utility Deployment Guide

## Overview
This is a Django-based PDF search utility with AI-powered search capabilities, supporting both English and Marathi languages.

## Deployment Options for Dokploy

### 1. Dockerfile (Recommended)
The project includes an optimized Dockerfile that:
- Uses Python 3.11 slim image
- Installs system dependencies (Tesseract OCR, Poppler)
- Sets up proper security with non-root user
- Includes health checks
- Optimized for production with Gunicorn

### 2. Build Configuration
- **Build Type**: Dockerfile
- **Dockerfile Path**: `./Dockerfile`
- **Context**: `.` (root directory)

## Environment Variables

Create a `.env` file based on `env.template`:

```bash
# Required
SECRET_KEY=your-secret-key-here
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

# Database (PostgreSQL recommended for production)
DB_NAME=flowdocs
DB_USER=postgres
DB_PASSWORD=your-db-password
DB_HOST=your-db-host
DB_PORT=5432

# API Keys
OPENAI_API_KEY=your-openai-api-key
GOOGLE_API_KEY=your-google-api-key

# Optional
REDIS_URL=redis://your-redis-host:6379/1
SENTRY_DSN=your-sentry-dsn
```

## Database Setup

### PostgreSQL (Production)
1. Create a PostgreSQL database
2. Set environment variables
3. Run migrations: `python manage.py migrate`

### SQLite (Development)
- Automatically created if no DB_* environment variables are set

## Static Files
Static files are automatically collected during Docker build and served by WhiteNoise.

## System Dependencies
The Dockerfile includes:
- Tesseract OCR (for PDF text extraction)
- Poppler utils (for PDF to image conversion)
- PostgreSQL client libraries

## Health Checks
The application includes health checks at `http://localhost:8000/`

## Security Features
- Non-root user in Docker
- Environment-based configuration
- CORS protection
- XSS protection
- CSRF protection
- Secure headers

## Monitoring
- Sentry integration (optional)
- Structured logging
- Health check endpoints

## Scaling
- Uses Gunicorn with 3 workers by default
- Redis caching support
- Database connection pooling

## File Upload Limits
- Maximum file size: 10MB
- Supported formats: PDF
- OCR support for scanned PDFs

## Multi-language Support
- English (default)
- Marathi (with OCR support)
- Easy to extend for other languages
