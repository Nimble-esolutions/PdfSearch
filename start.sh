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

# -------------------- Database Path and Volumes --------------------

DB_PATH="/app/flowdocs/db.sqlite3"                # ✅ New mount path
OLD_DB_PATH="/app/flowdocs/flowdocs/db.sqlite3"   # 🔄 Legacy path for backward compat
BACKUP_DIR="/app/backups"
mkdir -p "$BACKUP_DIR"

# ===============================================================
# 3️⃣ Restore / Move DB from Old Path or Backup
# ===============================================================
if [ ! -f "$DB_PATH" ]; then
    echo "⚠️ No database found at $DB_PATH"

    if [ -f "$OLD_DB_PATH" ]; then
        echo "📦 Found old database at $OLD_DB_PATH → moving to new location..."
        mv "$OLD_DB_PATH" "$DB_PATH"
    else
        latest_backup=$(ls -t $BACKUP_DIR/db_backup_*.sqlite3 2>/dev/null | head -n 1)
        if [ -n "$latest_backup" ]; then
            echo "♻️ Restoring DB from latest backup: $latest_backup"
            cp "$latest_backup" "$DB_PATH"
        else
            echo "🆕 No existing DB found. A fresh one will be created."
        fi
    fi
else
    echo "✅ Database found at $DB_PATH"
fi
echo "------------------------------------------------------------"
# 4️⃣ Backup current database
# ===============================================================
if [ -f "$DB_PATH" ]; then
    BACKUP_FILE="$BACKUP_DIR/db_backup_$(date +%F_%H%M%S).sqlite3"
    echo "💾 Backing up database to $BACKUP_FILE"
    cp "$DB_PATH" "$BACKUP_FILE"
    echo "✅ Backup complete"
else
    echo "⚠️ No database file to backup"
fi
echo "------------------------------------------------------------"

# ===============================================================
# 5️⃣ Run migrations
# ===============================================================
echo "🗃️ Running Django migrations..."
cd /app/flowdocs
python manage.py migrate --noinput || echo "⚠️ Migration failed. Please check logs."
echo "✅ Migrations complete"
echo "------------------------------------------------------------"

# 6️⃣ Create superuser if not exists
# ===============================================================
echo "👤 Checking for admin superuser..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@gmail.com', 'admin123')
    print('✅ Superuser created: admin / admin123')
else:
    print('ℹ️ Superuser already exists')
"
echo "------------------------------------------------------------"
# 7️⃣ Initialize ChromaDB
# ===============================================================
CHROMA_DIR="/app/flowdocs/chroma_db"
echo "🧠 Initializing ChromaDB directory..."

if [ ! -d "$CHROMA_DIR" ]; then
    mkdir -p "$CHROMA_DIR"
    echo "✅ Created new Chroma directory: $CHROMA_DIR"
else
    echo "ℹ️ Chroma directory already exists: $CHROMA_DIR"
fi
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
