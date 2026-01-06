# Docker Compose Configuration Review

## Summary

Reviewed both `docker-compose.dev.yml` and `docker-compose.prod.yml` files. All configurations are correct and consistent.

## Database Configuration ✅

### Path Consistency
All files use the same database path:
- **Dockerfile**: `SQLITE_DB_PATH=/app/flowdocs/db.sqlite3`
- **docker-compose.dev.yml**: `SQLITE_DB_PATH=/app/flowdocs/db.sqlite3`
- **docker-compose.prod.yml**: `SQLITE_DB_PATH=/app/flowdocs/db.sqlite3`
- **start.sh**: `DB_PATH="/app/flowdocs/db.sqlite3"`

### Volume Mounts ✅
Both compose files correctly mount the SQLite database:
```yaml
volumes:
  - ${VOLUME_BASE_PATH:-/home/prodsahakar/flowdocs}/db.sqlite3:/app/flowdocs/db.sqlite3
```

### Environment Variables ✅
Both files set the required migration environment variables:
```yaml
- SQLITE_DB_PATH=/app/flowdocs/db.sqlite3
- MIGRATIONS_JSON=/app/flowdocs/
- FORCE_MIGRATIONS=0
```

## Migration Handling

### Current Behavior
The `start.sh` script handles migration failures gracefully:
```bash
python manage.py migrate --noinput || echo "⚠️ Migration failed. Please check logs."
```

**Pros:**
- Container continues to start even if migrations fail
- Allows debugging without container restart loops
- Errors are logged for investigation

**Cons:**
- May mask critical migration failures
- Application might start in inconsistent state

### Recommendation
The current approach is acceptable for development, but consider:
1. **For Production**: Add exit code checking after migrations
2. **For Development**: Current behavior is fine (allows debugging)

## Key Differences: Dev vs Prod

### Development (`docker-compose.dev.yml`)
- **Build**: Uses `build: .` to build from local Dockerfile
- **DEBUG**: `True`
- **Image**: Built locally
- **Restart**: `unless-stopped`
- **Health Check**: 40s start period

### Production (`docker-compose.prod.yml`)
- **Build**: Uses pre-built image `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest`
- **DEBUG**: `False`
- **Image**: Pulled from GHCR
- **Restart**: `always`
- **Health Check**: 60s start period
- **Resource Limits**: CPU and memory limits configured
- **Logging**: JSON file driver with rotation
- **Backups Volume**: Mounted separately

## Redis Configuration ✅

Both files use the GHCR-mirrored Redis image:
```yaml
image: ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine
```

- ✅ No Docker Hub rate limit issues
- ✅ Always uses latest Redis version
- ✅ Health checks configured
- ✅ Persistent volumes configured

## User Permissions ✅

Both files set:
```yaml
user: "1000:1000"  # Match host user prodsahakar (uid:gid)
```

This ensures:
- ✅ Container runs as non-root
- ✅ File permissions match host user
- ✅ No permission issues with bind mounts

## Recommendations

1. **✅ Current Configuration**: All docker-compose settings are correct
2. **Migration Fix**: The migration fix in PR #1 will resolve the SQLite index removal issue
3. **No Changes Needed**: Docker-compose files don't need any modifications

## Next Steps

1. Merge PR #1 with the migration fix
2. Rebuild Docker image (will include fixed migration)
3. Deploy updated image to production
4. Migrations should now succeed on SQLite

