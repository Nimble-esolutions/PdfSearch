# Dokploy Deployment Guide — FlowDocs (PdfSearch)

## Quick Setup

### 1. Create Application in Dokploy
- **Type**: Compose
- **Repository**: `https://github.com/Nimble-esolutions/PdfSearch.git`
- **Branch**: `feature/docker-optimization-v2` (or default after merge)
- **Compose Path**: `./docker-compose.yml`

### 2. Environment Variables (Dokploy UI → Environment)
```
DEBUG=False
SECRET_KEY=<generate 50-char random string>
ALLOWED_HOSTS=ai-sahakar.net,www.ai-sahakar.net,one.ai-sahakar.net
OPENAI_API_KEY=<your OpenAI API key>
CORS_ALLOWED_ORIGINS=https://ai-sahakar.net,https://www.ai-sahakar.net
CSRF_TRUSTED_ORIGINS=https://ai-sahakar.net,https://www.ai-sahakar.net,https://one.ai-sahakar.net
REDIS_URL=redis://redis:6379/1
APP_UID=1000
APP_GID=1000
DATA_PATH=/home/prodsahakar/flowdocs
WEB_PORT=8000
```

### 3. Domain & TLS
- **Host**: `ai-sahakar.net`
- **Container Port**: `8000`
- **TLS**: Enable Let's Encrypt

### 4. Default Credentials
- **Admin**: `admin` / `admin123` — **change after first login**

---

## Data Directory Layout

All persistent data at `${DATA_PATH}` (default `/home/prodsahakar/flowdocs/`):

```
/home/prodsahakar/flowdocs/
├── db.sqlite3          # SQLite database
├── media/
│   └── pdfs/           # Uploaded PDF documents
├── faiss_indexes/      # FAISS vector search indexes
├── chroma_db/          # ChromaDB vector embeddings
├── staticfiles/        # Collected Django static files
└── backups/
    ├── db_backup_*.sqlite3     # Rotating DB snapshots
    ├── chroma_backup/          # ChromaDB snapshots
    └── json_backups/           # Django dumpdata fixtures
```

All directories are bind-mounted — data survives `docker compose down`, container removal, and image rebuilds.

---

## Migration (One-Time)

If migrating from an older setup where `pdfs/` was at the top level:

```bash
ssh root@80.65.208.138
mkdir -p /home/prodsahakar/flowdocs/{media,chroma_db,staticfiles}
mv /home/prodsahakar/flowdocs/pdfs /home/prodsahakar/flowdocs/media/pdfs
chown -R 1000:1000 /home/prodsahakar/flowdocs/
```

---

## Backup Strategy

### Host-Level Backup (Recommended)

```bash
ssh root@80.65.208.138
DATE=$(date +%F_%H%M%S)
mkdir -p /root/backups/flowdocs
sqlite3 /home/prodsahakar/flowdocs/db.sqlite3 ".backup /tmp/db_snapshot.sqlite3"
tar czf /root/backups/flowdocs/flowdocs-$DATE.tar.gz \
    -C /home/prodsahakar/flowdocs \
    db.sqlite3 media faiss_indexes chroma_db
find /root/backups/flowdocs -name 'flowdocs-*.tar.gz' -mtime +7 -delete
```

### Automated Cron Job

```bash
# crontab -e
0 2 * * * /root/scripts/backup-flowdocs.sh
```

Script at `/root/scripts/backup-flowdocs.sh`:
```bash
#!/bin/bash
set -e
DATE=$(date +\%F)
DATA_PATH="/home/prodsahakar/flowdocs"
BACKUP_DIR="/root/backups/flowdocs"
mkdir -p "$BACKUP_DIR"
sqlite3 "$DATA_PATH/db.sqlite3" ".backup /tmp/db_snapshot.sqlite3" 2>/dev/null || true
[ -f /tmp/db_snapshot.sqlite3 ] && cp /tmp/db_snapshot.sqlite3 "$DATA_PATH/db.sqlite3"
tar czf "$BACKUP_DIR/flowdocs-$DATE.tar.gz" -C "$DATA_PATH" \
    db.sqlite3 media faiss_indexes chroma_db 2>/dev/null
find "$BACKUP_DIR" -name 'flowdocs-*.tar.gz' -mtime +7 -delete
```

### Restore

```bash
tar xzf /root/backups/flowdocs/flowdocs-<DATE>.tar.gz -C /home/prodsahakar/flowdocs/
chown -R 1000:1000 /home/prodsahakar/flowdocs/
# Redeploy via Dokploy UI
```

---

## Pre-Deployment Checklist

```bash
ssh root@80.65.208.138 "df -h /"                                    # disk space
ssh root@80.65.208.138 "ls -la /home/prodsahakar/flowdocs/"         # data exists
# Run backup (see above)
ssh root@80.65.208.138 "ls /etc/dokploy/traefik/dynamic/"           # check no route conflicts
```

---

## Post-Deployment Verification

```bash
docker compose -f <path>/docker-compose.yml ps
docker compose -f <path>/docker-compose.yml logs web --tail 50
curl -sS http://localhost:8000/
curl -sS -o /dev/null -w "%{http_code}" https://ai-sahakar.net/admin/
```

---

## Disaster Recovery

1. Restore backup (see Restore section)
2. Redeploy from Dokploy UI
3. Verify with post-deployment checks
4. ChromaDB/FAISS indexes auto-rebuild on next upload if missing
