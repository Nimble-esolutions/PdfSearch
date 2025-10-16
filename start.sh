#!/bin/bash

# Exit on any error
set -e

echo "Starting FlowDocs application..."

# Set default environment variables if not set
export SECRET_KEY=${SECRET_KEY:-"django-insecure-change-me-in-production"}
export DEBUG=${DEBUG:-"True"}
export ALLOWED_HOSTS=${ALLOWED_HOSTS:-"*"}
export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-"flowdocs.settings"}
export OPENAI_API_KEY=${OPENAI_API_KEY:-""}
export CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-""}
export CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS:-""}

echo "Environment: SECRET_KEY=${SECRET_KEY:0:10}..., DEBUG=$DEBUG, ALLOWED_HOSTS=$ALLOWED_HOSTS, DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS_MODULE"
echo "OpenAI API Key: ${OPENAI_API_KEY:0:20}..." 
echo "CORS Origins: $CORS_ALLOWED_ORIGINS"
echo "CSRF Origins: $CSRF_TRUSTED_ORIGINS"

# ------------------ SQLite Backup & Restore ------------------
mkdir -p /app/backups

# 1️⃣ Backup current database
if [ -f /app/flowdocs/db.sqlite3 ]; then
    echo "Backing up current SQLite database..."
    cp /app/flowdocs/db.sqlite3 /app/backups/db_backup_$(date +%F_%H%M%S).sqlite3 || echo "SQLite backup failed"
fi

# 2️⃣ Restore latest backup if exists
latest_backup=$(ls -t /app/backups/db_backup_*.sqlite3 2>/dev/null | head -n 1)
if [ -f "$latest_backup" ]; then
    echo "Restoring SQLite database from latest backup: $latest_backup"
    cp "$latest_backup" /app/flowdocs/db.sqlite3 || echo "SQLite restore failed"
fi
# -------------------------------------------------------------

# Run migrations (ignore errors for now)
echo "Running database migrations..."
cd /app/flowdocs && DJANGO_SETTINGS_MODULE=flowdocs.settings python manage.py migrate || {
    echo "Migration failed, continuing with existing database..."
}

# Create superuser if it doesn't exist (ignore errors)
echo "Checking for superuser..."
cd /app/flowdocs && DJANGO_SETTINGS_MODULE=flowdocs.settings python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@gmail.com', 'admin123')
    print('Superuser created: admin/admin123')
else:
    print('Superuser already exists')
" || echo "Superuser creation failed, continuing..."

# Collect static files
echo "Collecting static files..."
cd /app/flowdocs && DJANGO_SETTINGS_MODULE=flowdocs.settings python manage.py collectstatic --noinput --clear || echo "Static files collection failed, continuing..."

# Start the application
echo "Starting Gunicorn server on 0.0.0.0:8000..."
exec gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 --access-logfile - --error-logfile - flowdocs.wsgi:application
