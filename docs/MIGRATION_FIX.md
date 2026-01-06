# Django Migration Fixes for SQLite Compatibility

## Problem Summary

Two migration errors occurred when running Django migrations on SQLite database:

1. **Migration 0007 Error**: `sqlite3.OperationalError: no such index: core_pdffil_search__1650a6_gin`
   - Migration 0007 tries to remove a PostgreSQL GIN index that doesn't exist in SQLite
   - SQLite doesn't support GIN indexes, so the index was never created

2. **Migration 0008 Error**: `sqlite3.OperationalError: table core_customuser has no column named department`
   - Migration 0008 couldn't run because migration 0007 failed
   - This is a cascading failure, not a problem with migration 0008 itself

## Root Cause

Migration 0006 (`0006_pdffile_content_pdffile_search_vector_and_more.py`) creates a PostgreSQL-specific GIN index:

```python
migrations.AddIndex(
    model_name='pdffile',
    index=django.contrib.postgres.indexes.GinIndex(fields=['search_vector'], name='core_pdffil_search__1650a6_gin'),
),
```

This index is only created when using PostgreSQL. When using SQLite:
- The `AddIndex` operation is skipped (SQLite doesn't support GIN indexes)
- But migration 0007 tries to remove the index unconditionally
- This causes the error: "no such index"

## Solution

Fixed migration 0007 to be database-aware:

1. **Check database vendor** before attempting to remove the index
2. **PostgreSQL**: Remove the index if it exists
3. **SQLite**: Skip index removal (index was never created)

### Implementation

```python
def remove_index_safely(apps, schema_editor):
    """
    Remove the GIN index only if it exists (PostgreSQL only).
    SQLite doesn't support GIN indexes, so this index was never created.
    """
    db_vendor = connection.vendor
    if db_vendor == 'postgresql':
        # Only try to remove index on PostgreSQL
        with connection.cursor() as cursor:
            # Check if index exists
            cursor.execute("""
                SELECT indexname FROM pg_indexes 
                WHERE tablename = 'core_pdffile' 
                AND indexname = 'core_pdffil_search__1650a6_gin'
            """)
            if cursor.fetchone():
                # Index exists, remove it
                cursor.execute('DROP INDEX IF EXISTS core_pdffil_search__1650a6_gin')
    # For SQLite, do nothing - the index was never created
```

## Testing

After applying this fix:

1. ✅ Migration 0007 will succeed on both PostgreSQL and SQLite
2. ✅ Migration 0008 will run successfully (adds `department` field to `CustomUser`)
3. ✅ All subsequent migrations will run correctly

## Files Changed

- `flowdocs/core/migrations/0007_remove_pdffile_core_pdffil_search__1650a6_gin_and_more.py`
  - Added database vendor check
  - Made index removal conditional on PostgreSQL only

## Related Issues

- Migration 0006 creates PostgreSQL-specific features that don't work with SQLite
- Future migrations should be database-aware when dealing with database-specific features

