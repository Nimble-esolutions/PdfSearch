#!/bin/bash
set -e

echo "🚀 Starting FlowDocs application..."

# -------------------- Environment setup --------------------
export SECRET_KEY=${SECRET_KEY:-"django-insecure-change-me-in-production"}
export DEBUG=${DEBUG:-"True"}
export ALLOWED_HOSTS=${ALLOWED_HOSTS:-"*"}
export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-"flowdocs.settings"}
export OPENAI_API_KEY=${OPENAI_API_KEY:-""}
export CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-""}
export CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS:-""}

echo "Environment summary:"
echo "  DEBUG=$DEBUG"
echo "  ALLOWED_HOSTS=$ALLOWED_HOSTS"
echo "------------------------------------------------------------"

# -------------------- SQLite Backup & Restore --------------------
DB_PATH=/app/flowdocs/db.sqlite3
BACKUP_DIR=/app/backups
mkdir -p "$BACKUP_DIR"

# Restore if DB missing but backup exists
if [ ! -f "$DB_PATH" ]; then
    latest_backup=$(ls -t $BACKUP_DIR/db_backup_*.sqlite3 2>/dev/null | head -n 1)
    if [ -n "$latest_backup" ]; then
        echo "♻️ Restoring DB from $latest_backup"
        cp "$latest_backup" "$DB_PATH"
        chown appuser:appuser "$DB_PATH"
        echo "✅ Restore complete"
    fi
fi

# Now backup current DB
if [ -f "$DB_PATH" ]; then
    BACKUP_FILE="$BACKUP_DIR/db_backup_$(date +%F_%H%M%S).sqlite3"
    echo "📦 Backing up DB to $BACKUP_FILE"
    cp "$DB_PATH" "$BACKUP_FILE" && echo "✅ Backup successful"
fi


# -------------------- Migrations --------------------
echo "🗃️ Applying migrations..."
cd /app/flowdocs
python manage.py migrate --noinput || echo "⚠️ Migration issue, please check logs."
echo "------------------------------------------------------------"

# -------------------- Superuser --------------------
echo "👤 Checking for admin superuser..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@gmail.com', 'admin123')
    print('✅ Superuser created: admin/admin123')
else:
    print('ℹ️ Superuser already exists')
" || echo "⚠️ Superuser creation failed."
echo "------------------------------------------------------------"

# -------------------- Static files --------------------
echo "🧹 Collecting static files..."
python manage.py collectstatic --noinput --clear || echo "⚠️ Static collection failed"
echo "------------------------------------------------------------"

# -------------------- Start Gunicorn --------------------
echo "🔥 Starting Gunicorn..."
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    flowdocs.wsgi:application
