# Why Old Dockerfile "Worked" But Current One Fails

## Key Insight

**The error is NOT a Dockerfile build error** - it's a **runtime migration error** that occurs when the container runs and executes Django migrations.

## Why the Old Dockerfile Appeared to Work

The old Dockerfile likely "worked" for one of these reasons:

### 1. **Database Already Migrated** (Most Likely)
- If the SQLite database already had migration 0007 applied, Django wouldn't try to run it again
- The `django_migrations` table would show migration 0007 as already applied
- Django would skip it and continue to migration 0008
- **This is why it appeared to work** - the broken migration was never executed

### 2. **Different Database Backend**
- If the old setup used **PostgreSQL** instead of SQLite, the GIN index would exist
- Migration 0007 would succeed on PostgreSQL (index exists, can be removed)
- The error only occurs on SQLite (index never existed)

### 3. **Different Migration File**
- The old codebase might not have had migration 0007 yet
- Or migration 0007 was different (maybe it didn't try to remove the index)
- Migration 0007 was created on 2025-09-03, so older codebases wouldn't have it

### 4. **Error Handling Masked the Issue**
- The old `entrypoint.sh` might have had error handling that continued despite migration failures
- Or migrations were run manually and errors were ignored
- The current `start.sh` logs errors but continues, which is why you see the error now

## Key Differences Between Old and Current Dockerfile

### Old Dockerfile:
```dockerfile
ENTRYPOINT ["entrypoint.sh"]
CMD ["./start.sh"]
SQLITE_DB_PATH=/app/flowdocs/flowdocs/db.sqlite3
```

### Current Dockerfile:
```dockerfile
ENTRYPOINT ["./start.sh"]
SQLITE_DB_PATH=/app/flowdocs/db.sqlite3
```

**Important**: These differences don't cause the migration error - they're just configuration changes.

## The Real Issue

The migration file `0007_remove_pdffile_core_pdffil_search__1650a6_gin_and_more.py` has always had the same problem:
- It tries to remove a PostgreSQL GIN index unconditionally
- On SQLite, this fails because the index was never created
- **This bug existed in the old Dockerfile too** - it just wasn't triggered

## Why It's Failing Now

1. **Fresh Database**: You're likely using a fresh SQLite database that hasn't been migrated yet
2. **Migration 0007 Needs to Run**: Django tries to apply migration 0007
3. **Error Occurs**: Migration 0007 fails because it tries to remove a non-existent index
4. **Cascade Failure**: Migration 0008 can't run because 0007 failed

## Solution

The fix in PR #2 (`ConditionalRemoveIndex`) solves this by:
- Checking database vendor before removing the index
- Only executing on PostgreSQL (where index exists)
- Skipping on SQLite (where index was never created)

This fix works for both old and new Dockerfiles because it fixes the migration file itself, not the Dockerfile.

## Conclusion

The old Dockerfile didn't actually "work" - it just didn't hit this error because:
- The database was already migrated, OR
- PostgreSQL was used instead of SQLite, OR  
- Migration 0007 didn't exist yet

The current Dockerfile exposes the bug because you're running migrations on a fresh SQLite database. The fix in PR #2 resolves this issue properly.

