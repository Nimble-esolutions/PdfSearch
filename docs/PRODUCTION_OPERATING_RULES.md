Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-25
Canonical source: docs/PRODUCTION_OPERATING_RULES.md
Supersedes: None

# Production Operating Rules

These rules apply to every PdfSearch production change.

## Source and Release

- Branch from the latest `dev`.
- Do not push fixes to merged branches.
- Every deployment must identify Git SHA, image digest, Compose hash, and data release.
- Dokploy must pull exact web and Redis image digests with `pull_policy: always`.
- Do not deploy a tag, cached `latest`, or alias as the release identifier.
- Keep application code in the image and mutable data in `/app/data`.
- A normal Dokploy deploy/autodeploy may recreate containers while retaining
  the named `/app/data` volume; this is conditional on the Compose project and
  volume mapping remaining unchanged. It is never a backup guarantee.

## Environment Identity

- Every deployment must set `APP_ENV`, `PRODUCTION_SOURCE_ID`,
  `AUTHORITATIVE_DATASET_ID`, `DATASET_ID`, `BACKUP_ROLE`,
  `EXTERNAL_SIDE_EFFECTS_MODE`, and `DATA_MODE`.
- Production requires `APP_ENV=production`, `DATA_MODE=live`, and a non-empty
  `PRODUCTION_SOURCE_ID`.
- Startup validation is fail-closed: an invalid identity exits before migrations.
- Never delete or rotate `/app/data/.instance_id` on a live instance.
- OCI labels, `/app/flowdocs/.release`, and `PDFSEARCH_IMAGE` must agree.

## Secrets

- Store secrets in Dokploy protected environment configuration.
- Never commit API keys, passwords, secret keys, or copied production `.env` files.
- Rotate any secret that appears in logs, chat, screenshots, or Git history.
- Production must use `DEBUG=False`, explicit hosts, and no insecure fallbacks.
- Artifact vault credentials (`ARTIFACT_VAULT_ACCESS_KEY`,
  `ARTIFACT_VAULT_SECRET_KEY`) must be set in production and never logged.

## Data Safety

- Never delete a volume without a verified backup and restore path.
- Never delete/recreate a Dokploy project, change its Compose project/volume
  name, or run `docker compose down -v` as part of a routine deployment.
- Before and after every deployment, record and compare the image digest,
  OCI revision, Compose hash, `/app/data` volume identity, and data counts.
- Never use a host bind path as an undocumented persistence contract.
- Never mount a named volume to a file path.
- Never mount persistent data over `/app/flowdocs`.
- Treat SQLite, media, FAISS, Chroma, snapshots, and checksums as one recovery set.
- Legacy and active data are divergent custody domains. Never copy them directly;
  require quarantine, inventory, conflict classification, staged restore, FAISS
  fingerprint validation, and explicit promotion.
- RustFS/S3 is implemented with namespace-scoped keys (`core/namespace.py`),
  conditional operation probing (`core/object_store_capabilities.py`), and
  CAS-based single-writer fencing (`core/global_writer.py`). Sync and restore
  must use immutable generation manifests, checksum validation, staged promotion,
  and a single maintenance worker. Never restore directly into the active data
  root.
- The web process queues maintenance work; it must not execute bulk reindex or
  restore loops inside a request.
- Public search is enabled by default. Restrict it only through an explicit
  `PUBLIC_SEARCH_FOLDER_IDS` allowlist and a reviewed deployment record.
- `/register/` is admin-only. Keep anonymous search separate from account
  creation; department-scoped admin roles require a phase-2 authorization
  design and server-side enforcement.

### Global Writer Fencing

`core/global_writer.py` enforces CAS-based single-writer fencing per dataset.
Before any write, the writer must acquire a lease (`core/lease.py`) backed by
Redis with SQLite fallback. Concurrent write attempts are rejected with a
conflict error. Never bypass the writer fence for ad-hoc data changes.

### Dataset Registration

`core/registration.py` handles dataset registration with conditional create.
Every dataset must be registered before writes are accepted. The registration
includes the `DATASET_ID`, `AUTHORITATIVE_DATASET_ID`, and instance identity.

### Activation and Crash Recovery

Generation activation (`core/activate.py`) uses atomic symlink swap. The
activation journal (`core/activation_journal.py`) records heartbeats during
activation; on crash recovery, it detects partial activations and rolls back
to the last known-good state. Never manually move or symlink data directories
on a live instance.

### Restore Pipeline

The restore pipeline (`core/restore_pipeline.py`) enforces a strict sequence:
download → validate → sanitize → rehearse → activate. Each stage is tracked
by the workspace state machine (`core/restore_workspace.py`). Migration
rehearsal (`core/rehearsal.py`) runs against an isolated copy before
activation. Compatibility checks (`core/compatibility.py`) verify schema,
embedding dimensions, and FAISS index format. Never skip a stage or restore
directly into the active data root.

### Sanitization

`core/sanitize.py` enforces PII sanitization for non-prod data. When
`DATA_MODE=sanitized`, all PII is stripped before data reaches the application.
Production (`DATA_MODE=live`) skips sanitization. Never deploy sanitized data
to a production instance.

### Backup Policy

`core/backup_policy.py` tracks dirty-state with fingerprinting and debouncing.
Backups are triggered only when data has changed since the last backup.
Fingerprint comparison prevents redundant backups. Never skip the dirty-state
check to force a backup — use the explicit management command instead.

### Writer Lease

`core/lease.py` provides writer leases with Redis primary and SQLite fallback.
Leases have a TTL and are renewable. A stale lease (expired without renewal)
is released automatically. Never hold a lease longer than the configured TTL
without renewal.

### Generation Lifecycle

Generations follow a strict state machine: `staged` → `validated` → `active` →
`superseded`. Promotion (`promote_active_generation`) atomically marks the prior
active generation as superseded and sets the new one active. Rollback
re-promotes a prior generation. Purge deletes generations past their retention
window. All lifecycle transitions are audited via `MaintenanceAuditEvent`.

### PDF Lifecycle

PDFs follow: `uploaded` → `processing` → `ready` → `deprecated` → `archived`.
Deprecated PDFs are hidden from search. Archived PDFs are hidden from both
search and dashboard. Restore resets to `uploaded` and requeues reindex.
Lifecycle transitions are role-gated (superadmin for deprecate/archive).

## Startup and Health

- Migrations must fail closed.
- `/livez` proves process liveness only.
- `/readyz` proves database connectivity, the configured cache (or reports
  `not_configured` when no cache URL is set), and that no migrations are
  pending.
- `/health/data/` validates data integrity: SQLite, media, FAISS, Chroma.
- `/health/lease/` reports current writer lease state.
- `/health/metrics/` exposes Prometheus metrics (`core/metrics.py`).
- Do not route Traefik traffic to a container that is only HTTP-200 on `/`.

### Deployment Identity Rules (2026-07-24)

- **Stage identity**: The `sahakar-ai-sahakar-frontend-2026-prod-ruhj6z` Compose project IS the stage deployment. Its Traefik labels route `2026.ai-sahakar.net`. The "prod" in the name is historical — this is stage.
- **Production identity**: `ai-sahakar.net` is routed via static Traefik config (`/etc/dokploy/traefik/dynamic/sahakar-dev-frontend-dockerfile-1cubi5.yml`). Production deployment requires a separate Compose project or static config update.
- **Never change stage Traefik labels** to claim production domains. This would silently redirect production users to unverified code.
- **/readyz 503 ≠ container failure**: Stage containers with degraded data (ratio-based readiness) return HTTP 503 from /readyz. This is a readiness signal, not a health failure. Use /livez for Docker health checks.

## Deployment

- Back up before schema, volume, or image changes.
- Verify the exact internal container port in Dokploy.
- Run authenticated login, PDF listing, search, and static asset smoke tests.
- Verify link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
  PDF count, FAISS count, and representative search.
- Record rollback image and data release before promotion.

## Incident Handling

- Preserve logs, deployment metadata, and checksums.
- Do not clean or prune the production server during an incident.
- Separate code rollback from database/data rollback.
- Perform a restore drill after recovery.

## Side-Effect Safety

- `EXTERNAL_SIDE_EFFECTS_MODE` gates all external calls: email, OpenAI,
  payments, webhooks.
- Production must use `live` mode. Staging and training use `sandbox` (email
  and AI allowed, payments and webhooks blocked). Dev uses `disabled`.
- The AI guard (`core/ai_guard.py`) enforces OpenAI client containment at
  construction time. No call site can bypass it.
- Never change `EXTERNAL_SIDE_EFFECTS_MODE` on a running production instance
  without a reviewed deployment record.
