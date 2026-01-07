#!/bin/bash
set -e

echo "🚀 Starting FlowDocs Django application..."
echo "------------------------------------------------------------"

# ===============================================================
# 1️⃣ Environment setup
# ===============================================================
export SECRET_KEY=${SECRET_KEY:-"django-insecure-change-me-in-production"}
export DEBUG=${DEBUG:-"True"}
export ALLOWED_HOSTS=${ALLOWED_HOSTS:-"*"}
export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-"flowdocs.settings"}
export OPENAI_API_KEY=${OPENAI_API_KEY:-""}
export CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-""}
export CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS:-""}

echo "🌍 Environment summary:"
echo "  DEBUG=$DEBUG"
echo "  ALLOWED_HOSTS=$ALLOWED_HOSTS"
echo "------------------------------------------------------------"

# ===============================================================
# 2️⃣ Database Paths and Volumes
# ===============================================================
# ===============================================================
# 2️⃣ Ensure key directories and permissions (new block)
# ===============================================================
echo "[entrypoint] Ensuring directories and permissions..."
mkdir -p \
    /app/flowdocs \
    /app/flowdocs/chroma_db \
    /app/flowdocs/media \
    /app/backups \
    /app/staticfiles

echo "[entrypoint] Fixing permissions for mounted volumes..."
chown -R appuser:appuser \
    /app/flowdocs \
    /app/flowdocs/chroma_db \
    /app/flowdocs/media \
    /app/backups \
    /app/staticfiles

chmod -R 770 \
    /app/flowdocs \
    /app/flowdocs/chroma_db \
    /app/flowdocs/media \
    /app/backups \
    /app/staticfiles

echo "✅ Directories and permissions ready"
echo "------------------------------------------------------------"

DB_PATH="/app/flowdocs/db.sqlite3"
OLD_DB_PATH="/app/flowdocs/flowdocs/db.sqlite3"
BACKUP_DIR="/app/backups"
CHROMA_DIR="/app/flowdocs/chroma_db"
CHROMA_BACKUP_DIR="$BACKUP_DIR/chroma_backup"

mkdir -p "$BACKUP_DIR" "$CHROMA_BACKUP_DIR"

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

# ===============================================================
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
# 🧩 5️⃣ Apply JSON → SQLite Data Migration
# ===============================================================
echo "🧩 Applying JSON → SQLite migrations..."

if [ -x "/usr/local/bin/apply_sqlite_json.py" ]; then
    python /usr/local/bin/apply_sqlite_json.py || {
        echo "❌ JSON migration failed!"
    }
    echo "✅ JSON → SQLite migration completed"
else
    echo "⚠️ JSON migration script not found!"
fi

echo "------------------------------------------------------------"
# ===============================================================
#  Restore ChromaDB (if needed)
# ===============================================================
echo "🧠 Checking ChromaDB vector store..."
if [ ! -d "$CHROMA_DIR" ]; then
    echo "📂 Creating new Chroma directory at $CHROMA_DIR"
    mkdir -p "$CHROMA_DIR"
elif [ -z "$(ls -A $CHROMA_DIR)" ]; then
    # empty chroma folder, try restore
    latest_chroma_backup=$(ls -dt $CHROMA_BACKUP_DIR/chroma_backup_* 2>/dev/null | head -n 1)
    if [ -n "$latest_chroma_backup" ]; then
        echo "♻️ Restoring ChromaDB from backup: $latest_chroma_backup"
        cp -r "$latest_chroma_backup"/* "$CHROMA_DIR"/
        echo "✅ ChromaDB restore complete"
    else
        echo "🆕 No ChromaDB backup found. Starting fresh."
    fi
else
    echo "✅ ChromaDB already present."
fi
echo "------------------------------------------------------------"

# ===============================================================
# 6️⃣ Run migrations
# ===============================================================
#echo "🗃️ Running Django migrations..."
#cd /app/flowdocs
#python manage.py migrate --noinput || echo "⚠️ Migration failed. Please check logs."
#echo "✅ Migrations complete"

echo "🗃️ Running Django migrations..."
set +e
python manage.py migrate --noinput
MIGRATION_STATUS=$?
set -e

if [ $MIGRATION_STATUS -ne 0 ]; then
    echo "⚠️ Migration failed — continuing startup to avoid container crash"
else
    echo "✅ Migrations applied successfully"
fi
echo "------------------------------------------------------------"

# ===============================================================
# 7️⃣ Create superuser if not exists
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

# ===============================================================
# 8️⃣ Backup ChromaDB
# ===============================================================
echo "🧠 Backing up ChromaDB..."
if [ -d "$CHROMA_DIR" ] && [ "$(ls -A $CHROMA_DIR)" ]; then
    CHROMA_BACKUP_PATH="$CHROMA_BACKUP_DIR/chroma_backup_$(date +%F_%H%M%S)"
    mkdir -p "$CHROMA_BACKUP_PATH"
    cp -r "$CHROMA_DIR"/* "$CHROMA_BACKUP_PATH"/
    echo "✅ ChromaDB backup saved at $CHROMA_BACKUP_PATH"
else
    echo "⚠️ No ChromaDB data to backup"
fi
echo "------------------------------------------------------------"

# ===============================================================
# 9️⃣ Collect static files
# ===============================================================
echo "🎨 Collecting static files..."
python manage.py collectstatic --noinput --clear || echo "⚠️ Static collection failed"
echo "------------------------------------------------------------"

# ===============================================================
# 🔟 Start Gunicorn server
# ===============================================================
#echo "🔥 Starting Gunicorn (Django app)..."
#exec gunicorn \
#    --bind 0.0.0.0:8000 \
#    --workers 4 \
#    --timeout 300 \
 #   --access-logfile - \
#    --error-logfile - \
#    flowdocs.wsgi:application

echo "🔥 Starting Gunicorn (Django app)..."
exec gosu appuser:appuser gunicorn \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    flowdocs.wsgi:application

