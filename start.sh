#!/bin/bash
set -e

echo "🚀 Starting Django application"

# Ensure working directory
cd /app/flowdocs

# Ensure SQLite directory exists
mkdir -p /app/flowdocs

# Create SQLite DB if not exists
if [ ! -f /app/flowdocs/db.sqlite3 ]; then
  echo "🗄️ Creating SQLite database"
  touch /app/flowdocs/db.sqlite3
fi

# Apply migrations (MUST succeed)
echo "🗃️ Applying Django migrations"
python manage.py migrate --noinput

# Collect static files
echo "🎨 Collecting static files"
python manage.py collectstatic --noinput

# Start Gunicorn
echo "🔥 Starting Gunicorn server"
exec gunicorn flowdocs.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 3 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
