# Migration Compatibility Root Cause Analysis

## Historical Incident

Migration 0007 attempted to remove a PostgreSQL GIN index unconditionally.
SQLite never created that index, so SQLite deployments failed before later
migrations could run.

## Durable Prevention

Database-specific migrations must use database-aware operations such as
`SeparateDatabaseAndState`, `RunSQL(..., state_operations=...)`, or an
explicit vendor check through `schema_editor.connection.vendor`.

Every migration change must be tested against:

- a fresh SQLite database;
- a copy of the current production SQLite database;
- PostgreSQL when PostgreSQL-specific behavior is involved.

## Startup Rule

A failed migration is a deployment failure. The web process must not continue
to readiness after migration errors. The startup script should exit non-zero
and allow Dokploy to keep traffic away from the unhealthy container.

## Data Rule

Database migrations and document/index migration are separate operations:

1. Snapshot and validate the database.
2. Migrate schema.
3. Validate media references.
4. Validate FAISS/Chroma compatibility.
5. Promote readiness.

Do not use a generic JSON fixture dump as a substitute for a consistent
SQLite backup or a schema migration.
