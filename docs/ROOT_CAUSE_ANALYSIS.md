Status: Historical
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/OPERATIONS_RUNBOOK.md
Supersedes: None

> Historical migration incident context. Use [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)
> for current migration-failure response and [`PRODUCTION_OPERATING_RULES.md`](PRODUCTION_OPERATING_RULES.md)
> for current readiness boundaries.

# Migration and Production Compatibility Root Cause Analysis

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

## Verified Production Outage Causes

The subsequent production outage had multiple independent causes:

- stale local reuse of a mutable `latest` image;
- stale Traefik labels routing to the wrong deployment shape;
- `localhost` used for Redis from inside the web container;
- the Redis client missing from the web image;
- `DEBUG=True` in production.

The current release boundary addresses these with immutable image identity,
explicit Dokploy routing, service-name Redis configuration, the Redis-enabled
PR #24 image revision, and `DEBUG=False`. Verify the effective deployment rather
than treating any one fix as sufficient.

## Verified Data Divergence

Legacy and active data are not interchangeable: legacy contains 242 PDFs and 45
FAISS files; active contains 17 PDF rows, 0 PDFs, and 11 FAISS files; only 6 PDF
paths overlap; and the SQLite databases diverge. Never merge by direct copy.
Use quarantine, inventory, conflict classification, staged restore, FAISS
fingerprint validation, and explicit promotion. The application adapter now
supports immutable, checksum-verified generations and staged pulls; it still
does not promote a staged generation into live data automatically.
