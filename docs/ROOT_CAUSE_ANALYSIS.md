Status: Historical
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-24
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

Legacy and active data were reconciled on 2026-07-22. Post-reconciliation:
253 PDF rows, 242 recovered PDF files, 53 folders, 8 users, 51 FAISS indexes,
8,753 vectors. Eleven target-only PDF rows remain preserved but unrecovered.
Never merge by direct copy. Use quarantine, inventory, conflict classification,
staged restore, FAISS fingerprint validation, and explicit promotion.

The application now supports the full restore pipeline: namespace-scoped S3 keys
(`core/namespace.py`), conditional operation probing
(`core/object_store_capabilities.py`), CAS-based single-writer fencing
(`core/global_writer.py`), immutable generation manifests, staged restore with
migration rehearsal (`core/rehearsal.py`), PII sanitization for non-prod
(`core/sanitize.py`), compatibility checks (`core/compatibility.py`), and
atomic symlink-based activation with crash recovery (`core/activate.py`,
`core/activation_journal.py`).

## Prevention Mechanisms (2026-07-24)

The following mechanisms were added to prevent recurrence of the data
divergence and deployment incidents documented above:

### Environment Identity (`core/environment.py`)
Fail-closed startup validation ensures every deployment declares its
`APP_ENV`, `DATA_MODE`, `BACKUP_ROLE`, and `EXTERNAL_SIDE_EFFECTS_MODE`.
Production uses `DATA_MODE=local` for the current volume-backed runtime and
requires a non-empty `PRODUCTION_SOURCE_ID`.
An invalid identity exits before migrations run — no silent misconfiguration.

### Side-Effect Policy (`core/side_effects.py`)
External calls (email, OpenAI, payments, webhooks) are gated by
`EXTERNAL_SIDE_EFFECTS_MODE`. Production uses `enabled`; staging uses `sandbox`
(payments/webhooks blocked); dev uses `disabled`. The AI guard
(`core/ai_guard.py`) enforces OpenAI containment at construction time.

### Global Writer Fencing (`core/global_writer.py`)
CAS-based single-writer fencing prevents two instances from writing to the
same dataset. Writer leases (`core/lease.py`) use Redis with SQLite fallback.
This prevents the "two instances diverging" failure mode.

### Restore Pipeline (`core/restore_pipeline.py`)
The strict download→validate→sanitize→rehearse→activate sequence prevents
direct data copying. Migration rehearsal runs against an isolated copy before
activation. Compatibility checks verify schema, embedding dimensions, and
FAISS format before promotion.

### Activation Safety (`core/activate.py`, `core/activation_journal.py`)
Atomic symlink swap prevents partial activation. Heartbeat-based crash
recovery detects and rolls back incomplete activations. This prevents the
"half-promoted generation" failure mode.

### Backup Policy (`core/backup_policy.py`)
Dirty-state tracking with fingerprinting and debouncing prevents redundant
backups and ensures backups are taken only when data has changed. This
prevents the "stale backup masking data loss" failure mode.

### Object Store Capabilities (`core/object_store_capabilities.py`)
Conditional operation probing at startup verifies that the S3/RustFS endpoint
supports If-None-Match and If-Match before any write path is enabled. This
prevents the "silent CAS failure" mode where writes succeed but fencing is
ineffective.
