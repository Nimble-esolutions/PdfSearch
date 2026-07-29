# PdfSearch Architecture Overview

**Status:** Active
**Audience:** Developer, Operator
**Owner:** FlowDocs maintainers
**Last verified:** 2026-07-24
**Canonical source:** docs/ARCHITECTURE_OVERVIEW.md

Primary knowledge transfer artifact for the PdfSearch system. Read this first.

---

## 1. System Overview

PdfSearch is an AI-powered document search platform for Maharashtra Cooperative
Societies legal documents. It ingests PDFs, extracts text, generates OpenAI
embeddings, builds FAISS vector indexes, and serves natural-language search
with role-based access control.

| Layer | Technology |
|-------|-----------|
| Web framework | Django 5.x (Gunicorn WSGI) |
| Database | SQLite (single-writer, file-based) |
| Vector search | FAISS (in-process, file-based indexes) |
| Cache / locking | Redis 7 |
| Object store | S3-compatible (RustFS / MinIO), opt-in |
| Container runtime | Docker Compose (not Swarm) |
| Reverse proxy | Traefik (Dokploy-managed, Let's Encrypt TLS) |
| Deployment | Dokploy GitHub-connected application |
| AI | OpenAI (text-embedding-3-small, gpt-4o-mini) |
| i18n | English (Indian) + Marathi |

**Role-based access:**
- `superadmin` — full access including operations dashboard, data lifecycle, user management
- `admin` — folder/PDF management, user registration
- `user` — search, view assigned folders

**Public surface:** Anonymous search is enabled by default for the public corpus
(`PUBLIC_SEARCH_ENABLED=1`). `/register/` always requires authenticated `admin`
or `superadmin`.

---

## 2. Core Module Map

All modules live under `flowdocs/core/`. Grouped by concern:

### Environment & Safety

| Module | Purpose |
|--------|---------|
| `environment.py` | `EnvironmentIdentity` dataclass with `AppEnv`/`DataMode`/`BackupRole` enums. Startup validation is fail-closed — missing `APP_ENV` outside dev/test raises `EnvironmentIdentityError`. |
| `side_effects.py` | `SideEffectPolicy` gates email, OpenAI, payments, webhooks, SMS, analytics, indexers, and notifications based on `ExternalSideEffectsMode` (enabled/disabled/sandbox). |
| `ai_guard.py` | OpenAI client containment. Three modes: `enabled` → real client, `sandbox` → deterministic fake provider, `disabled` → raises `ExternalAIBlocked`. All AI calls must route through `get_openai_client()`, `get_embedding_provider()`, or `get_chat_provider()`. |

### Data Lifecycle

| Module | Purpose |
|--------|---------|
| `artifact_vault.py` | S3/RustFS content-addressed immutable storage. `ArtifactVault` class with `put`/`get`/`head`/`put_pdf`/`put_faiss`/`put_manifest`. Validates SHA-256 on put and get. Rejects mutable release aliases (`latest`, `dev`, `prod`). |
| `registration.py` | Dataset registration with conditional create (never overwrite). `register_dataset()` creates once; `validate_registration()` checks before every authoritative operation. `update_authoritative_pointer_cas()` publishes generation pointers with CAS fencing. |
| `global_writer.py` | Cross-deployment single-writer fencing using S3 conditional operations (`If-None-Match` for first acquisition, `If-Match` with ETag for renewal/takeover). Prevents two deployments from both publishing as authoritative writer. |
| `namespace.py` | `KeyBuilder` — dataset-scoped S3 key construction. Validates every component against unsafe characters, path traversal, and length limits. |
| `object_store_capabilities.py` | `probe_capabilities()` — safe S3 conditional operation probing (If-None-Match, If-Match, ETag, versioning, read-after-write, metadata). Sets `authoritative_publication_allowed` flag. |
| `backup_policy.py` | Dirty-state tracking via Redis cache keys, fingerprint-based no-change detection, minimum-interval enforcement, and idempotency keys. `queue_backup_if_needed()` creates `sync_generation` maintenance jobs. |
| `lease.py` | Writer lease with Redis `SET NX EX` (atomic) + DB fallback. `WriterLease` carries a monotonically increasing fencing token (`writer_epoch`) that must be presented for authoritative publication. |

### Restore & Activation

| Module | Purpose |
|--------|---------|
| `restore_pipeline.py` | Full restore pipeline: download → validate → sanitize → rehearse → activate. `run_restore_pipeline()` wires all stages. Never modifies source generation or partially overwrites active data. |
| `restore_workspace.py` | `RestoreWorkspace` state machine with 16 states (`CREATED` → `DOWNLOADING` → ... → `ACTIVE`). `VALID_TRANSITIONS` dict enforces legal state changes. Workspace is always outside the active data tree. |
| `sanitize.py` | PII sanitization for non-prod data. `sanitize_database()` replaces user emails/names with deterministic hashes, deletes sessions, nullifies password reset tokens. `validate_sanitization()` checks for non-sanitized email domains. |
| `rehearsal.py` | `rehearse_migrations()` — runs Django migrations against an isolated copy of the restored database. Checks integrity, foreign keys, and reports whether image rollback is safe. |
| `activate.py` | Atomic symlink-based generation activation. `activate_generation()` acquires lock, preserves previous pointer, switches active symlink, validates, and records audit event. `rollback_to_previous()` restores the prior pointer. |
| `activation_journal.py` | `ActivationJournal` with heartbeat-based crash recovery. `acquire_activation_lock()` uses `O_EXCL` with liveness detection. `reconcile_incomplete_activations()` runs at startup to detect and recover crashed activations. |
| `compatibility.py` | `check_generation_compatibility()` — pre-activation checks for manifest version, migration compatibility, embedding model match, FAISS index presence, and sanitization status. |

### Observability

| Module | Purpose |
|--------|---------|
| `metrics.py` | Prometheus metrics at `/health/metrics/`. Exposes `pdfsearch_backup_last_success_timestamp`, `pdfsearch_backup_dirty_age_seconds`, `pdfsearch_current_generation_age_seconds`, `pdfsearch_data_pdf_count`, `pdfsearch_data_indexed_pdf_count`, `pdfsearch_maintenance_queue_depth`, `pdfsearch_backup_role`, `pdfsearch_is_production`. |

---

## 3. Environment Identity System

### Required Environment Variables

| Variable | Purpose | Required In |
|----------|---------|-------------|
| `APP_ENV` | Environment class | All (fail-closed if missing) |
| `PRODUCTION_SOURCE_ID` | Identifies the production source instance | Production, backup writer |
| `AUTHORITATIVE_DATASET_ID` | Which dataset this env may write to | Production |
| `DATASET_ID` | Local dataset identity | Production |
| `BACKUP_ROLE` | writer / reader / disabled | Production |
| `EXTERNAL_SIDE_EFFECTS_MODE` | enabled / disabled / sandbox | All |
| `DATA_MODE` | empty / seed / local / s3-restore / s3-pinned / sanitized-production / exact-production | All |

### Enum Values

**AppEnv:** `production`, `staging`, `development`, `test`, `review`

**DataMode:**
- `empty` — no data, clean start
- `seed` — bootstrap from declared seed database
- `local` — use local volume data
- `s3-restore` — declare a latest-compatible restore source/policy
- `s3-pinned` — declare a specific pinned restore source/policy
- `sanitized-production` — restore and sanitize production data for non-prod use
- `exact-production` — exact production data copy (requires explicit approval, forbidden in production)

**BackupRole:** `writer`, `reader`, `disabled`

**ExternalSideEffectsMode:** `enabled`, `disabled`, `sandbox`

**RestorePolicy:** `disabled`, `manual`, `startup-latest`, `startup-pinned`

The `startup-*` values are consumed by both entrypoints as a DB-free,
fail-closed posture check. They preserve an existing non-empty database and
stop before creating or migrating an absent/zero-byte database. They do not
perform automatic restore or activation.

### Startup Validation Flow

```
EnvironmentIdentity.from_env()
  ├── Parse APP_ENV (fail-closed if missing and not dev/test)
  ├── Parse DATASET_ID, PRODUCTION_SOURCE_ID, AUTHORITATIVE_DATASET_ID
  ├── Parse DATA_MODE, BACKUP_ROLE, EXTERNAL_SIDE_EFFECTS_MODE
  ├── Resolve INSTANCE_ID (persisted file > env var > generated + persisted)
  ├── Derive build identity (OCI labels > env > RELEASE.txt)
  └── .validate()
       ├── Production requires DEPLOYMENT_ID, DATASET_ID, PRODUCTION_SOURCE_ID
       ├── Backup writer requires ARTIFACT_VAULT_ENABLED + full S3 config
       ├── DATA_MODE restrictions (no empty/seed/exact-production in prod)
       ├── S3 restore modes require RESTORE_SOURCE_DATASET_ID
       ├── Non-prod with production-derived data must disable/sandbox side effects
       ├── Staging cannot be backup writer
       └── Build image digest must match APP_IMAGE_DIGEST if both set
```

### Instance Identity

- **Persisted at:** `/app/data/.instance_id`
- **Resolution order:** persisted file → `INSTANCE_ID` env var → `{hostname}-{random_hex}` (generated and persisted)
- **Survives** container restarts; stable across the data volume's lifetime

### Build Identity

- **Preference order:** `/app/.oci-labels.json` → `/app/build-info.json` → `APP_IMAGE_DIGEST` env → `/app/RELEASE.txt`
- Fields: `image_digest`, `git_commit` / `release_version`

---

## 4. Data Flow: Publication → Restore → Activation

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PRODUCTION (SOURCE)                         │
│                                                                     │
│  1. Acquire writer lease  (lease.py: Redis SET NX EX)              │
│  2. Acquire global writer (global_writer.py: S3 If-None-Match)    │
│  3. Register dataset      (registration.py: conditional create)    │
│  4. Build manifest        (inventory_artifacts command)            │
│  5. Upload artifacts      (artifact_vault.py: PDFs, FAISS, DB)    │
│  6. Upload manifest       (artifact_vault.py: put_manifest)       │
│  7. Update authoritative  (registration.py: CAS pointer update)   │
│     pointer                                                         │
│  8. Release global writer (global_writer.py: delete control obj)   │
│  9. Release lease         (lease.py: cache.delete)                 │
└──────────────────┬──────────────────────────────────────────────────┘
                   │  S3 bucket (RustFS / MinIO)
                   │  datasets/{id}/generations/{gen}/...
                   │  datasets/{id}/control/authoritative.json
                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      STAGING / DEV (TARGET)                         │
│                                                                     │
│  1. Resolve generation  (restore_pipeline.py: authoritative ptr)   │
│  2. Create workspace    (restore_workspace.py: isolated dir)       │
│  3. DOWNLOADING         (restore_pipeline.py: fetch all artifacts) │
│  4. DOWNLOADED          (verify manifest completeness)             │
│  5. SOURCE_VALIDATED    (SQLite integrity check)                   │
│  6. PREFLIGHT_PASSED    (compatibility.py: schema/embedding/FAISS) │
│  7. [SANITIZING]        (sanitize.py: PII removal, if non-prod)   │
│  8. [SANITIZED]         (validate_sanitization)                    │
│  9. [MIGRATION_REHEARSAL] (rehearsal.py: isolated migration run)  │
│ 10. [MIGRATION_READY]   (integrity + FK checks passed)             │
│ 11. APPLICATION_VALIDATED                                           │
│ 12. ACTIVATION_READY                                                │
│ 13. ACTIVATING          (activate.py: acquire lock)                │
│ 14. ACTIVE              (atomic symlink switch)                    │
└─────────────────────────────────────────────────────────────────────┘
```

### Rollback Path

```
activate_generation()
  ├── preserve_previous_pointer()   # /app/data-control/previous-generation
  ├── write_active_pointer(target)  # atomic symlink rename
  ├── verify pointer resolves
  └── on failure:
       └── rollback_to_previous()   # restore previous symlink
```

### Current orchestration boundary

The diagram above describes the tested library-level publication and full
restore pipeline. It is not the current admin/worker call graph:

```text
admin Sync
  -> maintenance job
  -> sync_active_generation()
  -> dataset-scoped generation manifest

admin Pull to Staging
  -> maintenance job
  -> stage_generation()
  -> legacy flat manifest lookup
  -> checksum/SQLite staging only

admin Promote/Rollback
  -> ArtifactGeneration status update
  -X-> activate_generation()
```

Consequences:

- a generation created by current sync is not addressable through the legacy
  admin staging lookup;
- the admin path does not run compatibility, sanitization, rehearsal, or
  byte-level activation;
- database “active” status is not proof that `/app/data` switched generation;
- the full `run_restore_pipeline()` path is currently reached by direct
  integration tests/tooling, not startup or the maintenance job.

Plan 003 owns reconciliation to one namespace and one activation-aware
orchestrator.

### Activation Journal Crash Recovery

```
Startup: reconcile_incomplete_activations()
  ├── Scan /app/data-control/activation-journals/*.json
  ├── Skip completed journals
  ├── For incomplete:
  │    ├── If lock held by live process → skip
  │    ├── If active pointer matches target → mark complete (activation succeeded)
  │    ├── If active pointer matches previous → mark rollback confirmed
  │    └── Otherwise → rollback to previous generation
  └── Return reconciliation actions
```

---

## 5. Safety Architecture

| Layer | Mechanism | Failure Mode |
|-------|-----------|-------------|
| Environment identity | `EnvironmentIdentity.validate()` at startup | Fail-closed: raises `EnvironmentIdentityError` |
| Side-effect policy | `SideEffectPolicy.from_identity()` gates email, payments, webhooks | Non-prod with prod data → effects disabled/sandboxed |
| AI call containment | `ai_guard.py` three-mode gating (enabled/sandbox/disabled) | Disabled → `ExternalAIBlocked`; sandbox → deterministic fake |
| Global writer fencing | S3 `If-None-Match` / `If-Match` CAS on control objects | `GlobalWriterHeld` / `GlobalWriterConflict` |
| Dataset registration | Conditional create, never overwrite | `RegistrationConflict` if already registered |
| Compatibility checks | `check_generation_compatibility()` before activation | Blocks activation on schema/embedding/FAISS mismatch |
| Migration rehearsal | `rehearse_migrations()` against isolated DB copy | Blocks activation on integrity/FK failure |
| Sanitization | `sanitize_database()` + `validate_sanitization()` | Non-sanitized email domains → validation failure |
| Activation journal | `ActivationJournal` with heartbeat + O_EXCL lock | Crashed activation → startup reconciliation |
| Backup policy | Dirty-state tracking, fingerprint, debouncing, idempotency | No-change → skip; duplicate → skip |
| Writer lease | Redis `SET NX EX` with fencing token | `LeaseConflict` / `LeaseLost` |
| Object store capabilities | `probe_capabilities()` before first publication | `authoritative_publication_allowed=False` → `GlobalWriterError` |
| Workspace isolation | Restore workspace always outside active data tree | `WorkspaceError` if inside active root |
| Immutable artifact keys | SHA-256 content-addressed keys, mutable alias rejection | `ArtifactVaultIntegrityError` |

---

## 6. Health & Observability

### Endpoints

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `/livez` | Process liveness (`{"status": "ok"}`) | Public |
| `/readyz` | Database, cache, migrations, data state, backup status | Public |
| `/health/data/` | Data generation status (minimal, public) | Public |
| `/health/lease/` | Writer lease status (minimal, public) | Public |
| `/health/metrics/` | Prometheus text-format metrics | Public |

### Management Commands

| Command | Purpose |
|---------|---------|
| `config_inspect` | Dump resolved environment identity and configuration |
| `verify_object_store_capabilities` | Run S3 capability probes and print report |
| `inventory_artifacts` | Build a manifest of all local data artifacts |
| `validate_data_release` | Validate a data release manifest against local state |

### Prometheus Metrics

```
pdfsearch_backup_last_success_timestamp   # Unix timestamp
pdfsearch_backup_dirty_age_seconds        # Seconds since data marked dirty
pdfsearch_current_generation_age_seconds  # Age of active generation
pdfsearch_data_pdf_count                  # Total PDF rows
pdfsearch_data_indexed_pdf_count          # Indexed PDF count
pdfsearch_data_ready                      # 1 if data is ready
pdfsearch_maintenance_queue_depth         # Queued + running jobs
pdfsearch_backup_role                     # 1 if backup writer
pdfsearch_is_production                   # 1 if APP_ENV=production
```

---

## 7. Key Data Boundaries

| Path | Purpose | R/W |
|------|---------|-----|
| `/app/data/db.sqlite3` | Active SQLite database | RW |
| `/app/data/faiss_indexes/` | FAISS vector indexes | RW |
| `/app/data/media/` | Uploaded PDF files | RW |
| `/app/data/staticfiles/` | Collected static assets | RW |
| `/app/data/backups/` | Local backups and restore workspaces | RW |
| `/app/data/chroma_db/` | Chroma vector database (legacy) | RW |
| `/app/data/.instance_id` | Stable instance identity | RW (created once) |
| `/app/data-control/active-generation` | Atomic symlink to active workspace | RW |
| `/app/data-control/previous-generation` | Rollback target symlink | RW |
| `/app/data-control/activation-journals/` | Crash recovery journals | RW |
| `/app/data-control/activation.lock` | O_EXCL activation lock file | RW |
| `/mnt/legacy` | Read-only legacy data volume | RO |
| `/app/flowdocs/` | Application code (immutable image) | RO |
| `/app/.oci-labels.json` | OCI image labels | RO |
| `/app/RELEASE.txt` | Release version file | RO |

**Critical rule:** The restore workspace is always created under
`/app/data/backups/restore-workspaces/`, which is outside the active data tree.
`validate_workspace_paths()` enforces this — a workspace inside the active root
is rejected.

---

## 8. CI/CD Pipeline

### Compose Files

| File | Purpose | Key Settings |
|------|---------|-------------|
| `docker-compose.ci.yml` | CI disposable stack | `APP_ENV=development`, `EXTERNAL_SIDE_EFFECTS_MODE=sandbox`, `DATA_BOOTSTRAP_MODE=empty`, `BACKUP_ROLE=disabled` |
| `docker-compose.dev.yml` | Local development | `DEBUG=True`, `ALLOW_INSECURE_DEFAULTS=1`, build from local Dockerfile |
| `docker-compose.integration.yml` | Disposable MinIO/Redis lifecycle and staging runtime crash-recovery stack | Digest-pinned dependency images, isolated named volumes/network, separate candidate promotion, trust-chain restore, and shared-control-volume process-death recovery |
| `docker-compose.yml` | Production template | `APP_ENV=production`, `BACKUP_ROLE=disabled` by default, `pull_policy: always`, Traefik network |

### CI Scripts

| Script | Purpose |
|--------|---------|
| `scripts/ci/runtime_smoke.py` | End-to-end smoke: `/livez`, `/readyz`, login, dashboard, folder, PDF view, search |
| `scripts/ci/run_vault_integration.sh` | Isolated MinIO/Redis lifecycle plus signed runtime pointer container-death/restart gate |
| `scripts/ci/real_runtime_proof.sh` | Compatibility wrapper for `run_vault_integration.sh`; never targets existing containers |
| `scripts/ci/validate_migrations.py` | Migration number collision guard (catches duplicate migration numbers across PRs) |
| `scripts/ci/admin_ui_smoke.py` | Admin UI smoke tests |
| `scripts/ci/seed_admin_ui_demo.py` | Seed demo data for admin UI |
| `scripts/ci/data_release_gate.py` | Data release validation: build manifest, check FAISS compatibility, validate release, verify read-only |
| `scripts/ci/validate_public_html.py` | Public HTML validation |
| `scripts/ci/seed_smoke.py` | Seed data smoke test |
| `scripts/ci/run_compose_smoke.sh` | Compose smoke test runner |

---

## 9. Production Deployment

### Architecture

```
Internet
  │
  ▼
Traefik (:80/:443, Let's Encrypt)
  │
  ├── ai-sahakar.net, www.ai-sahakar.net
  │
  ▼
Dokploy-managed Compose stack
  ├── web (Gunicorn :8000, bound 127.0.0.1)
  ├── maintenance (worker, single instance)
  └── redis (Redis 7, internal network)
```

### Required Production Environment Variables

| Variable | Example | Notes |
|----------|---------|-------|
| `SECRET_KEY` | (generated) | Django secret key |
| `OPENAI_API_KEY` | `sk-...` | OpenAI API key |
| `ALLOWED_HOSTS` | `ai-sahakar.net,www.ai-sahakar.net` | Django host validation |
| `CSRF_TRUSTED_ORIGINS` | `https://ai-sahakar.net,https://www.ai-sahakar.net` | CSRF origin validation |
| `CORS_ALLOWED_ORIGINS` | `https://ai-sahakar.net,https://www.ai-sahakar.net` | CORS origin validation |
| `PDFSEARCH_IMAGE` | `ghcr.io/.../pdfsearch@sha256:...` | Immutable image digest |
| `APP_ENV` | `production` | Environment class |
| `DEPLOYMENT_ID` | `prod-mum-01` | Deployment identifier |
| `DATASET_ID` | `ai-sahakar-prod` | Dataset identity |
| `BACKUP_ROLE` | `disabled` by default; `writer` only for approved publication | Backup role |
| `EXTERNAL_SIDE_EFFECTS_MODE` | `enabled` | Side-effect policy |
| `DATA_MODE` | `local` | Data mode |

Production must also keep:
```
DEBUG=False
ALLOW_INSECURE_DEFAULTS=0
CREATE_SUPERUSER=0
```

### Common Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `/readyz` fails with `database: error` | SQLite file missing or corrupted | Check `/app/data/db.sqlite3` exists and is writable |
| `/readyz` fails with `cache: error` | Redis unreachable | Verify `REDIS_URL=redis://redis:6379/1`, check Redis container health |
| `/readyz` fails with `migrations: error` | Unapplied migrations | Run `python manage.py migrate --check` |
| 502 from Traefik | Web container not healthy | Check `docker ps`, verify port `8000` bound to `127.0.0.1` |
| Static files 404 | `collectstatic` not run or wrong `STATIC_ROOT` | Verify `/app/data/staticfiles/` contains collected assets |
| Search returns no results | FAISS indexes missing or incompatible | Check `/app/data/faiss_indexes/`, verify embedding model matches |
| Maintenance worker stuck | SQLite lock contention | Ensure single maintenance worker; check for zombie processes |
| Image digest mismatch | `pull_policy: always` not pulling expected digest | Verify `PDFSEARCH_IMAGE` is an immutable digest, not a tag |

---

## 10. Current State

| Metric | Value |
|--------|-------|
| dev HEAD | `2e1ca38` |
| Django migrations | 17 (core:0001 through core:0017) |
| Unit tests | 157+ (all passing) |
| Integration tests | 16 S3 primitive + 3 application publication (passing) |
| PDF rows | 253 |
| PDF files | 242 |
| Folders | 53 |
| Users | 8 |
| FAISS indexes | 51 |
| FAISS vectors | 8,753 |
| i18n languages | English (Indian) + Marathi |
| Production readiness | Pending: RustFS certification + staging rehearsal |

### What's Verified

- All 157 unit tests pass in the current image
- 16 MinIO S3/CAS integration tests pass
- Django system check: 0 issues
- `/livez`, `/readyz`, `/health/data/`, `/health/lease/`, `/health/metrics/` all respond correctly
- Operations dashboard renders (superadmin-only)
- CI pipeline: runtime smoke, migration guard, data release gate, admin UI smoke

### What's Planned / In Progress

- RustFS certification (S3 conditional operation verification against production RustFS)
- Staging rehearsal (full restore → sanitize → rehearse → activate cycle)
- OpenAI call routing through `ai_guard.py` (currently some code paths bypass the guard)
- Automatic cross-environment sync
- Generated artifact manifests with automatic reconciliation
- FAISS recovery orchestration
- Department-scoped admin roles (phase 2 authorization)
- Docker secrets migration for credential management
