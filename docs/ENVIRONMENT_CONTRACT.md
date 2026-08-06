Status: Active
Audience: Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical source: docs/ENVIRONMENT_CONTRACT.md
Supersedes: env.minimal, env.template

Use docs/ENVIRONMENT_REFERENCE.md for the complete variable-by-variable
reference and reviewed dev/stage/production examples.

# Environment Contract

## 2026 stage applied posture (2026-08-03)

The current stage deployment has a signed active runtime. Production remains
untouched. The operator-approved stage image channel intentionally follows
`:latest`:

    APP_ENV=staging
    DATA_MODE=local
    DATA_BOOTSTRAP_MODE=strict
    DATASET_ID=ai-sahakar-stage-2026
    AUTHORITATIVE_DATASET_ID=ai-sahakar-stage-2026
    DATAOPS_ENABLED=1
    ARTIFACT_VAULT_ENDPOINT=<approved-rustfs-origin>
    ARTIFACT_VAULT_BUCKET=ai-sahakar-stage-2026-flowdocs-artifact-vault
    BACKUP_ROLE=reader
    BACKUP_SYNC_MODE=manual
    STAGING_INITIAL_ACTIVATION_ENABLED=1
    STAGING_RUNTIME_ACTIVATION_ENABLED=1
    PDFSEARCH_IMAGE=ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest

The owned DataOps v3 connection points to the stage bucket/dataset. The
production-v2 source is a separate foreign read-only connection used only by
explicit Import. Backup and restore profile selectors are retired operator
choreography and are not required for v3 route decisions. Credential and
signing-key values are secret-provider material and must never appear in
examples. Compose derives the container's `APP_IMAGE_DIGEST` marker from
`PDFSEARCH_IMAGE`; the resolved digest remains separate inspection evidence.
Environment edits require a controlled Compose recreate; the Dokploy project
name must be preserved so named data/control volumes cannot be replaced by
timestamp-derived blank volumes. See [`HANDOFF.md`](HANDOFF.md); dated status
pages are historical evidence.

`.env.example` is the local copy target; `.env.dataops.example` and the reviewed
files under `docs/environments/` are tracked role-specific references. Deleted
duplicates (`env.minimal`, `env.template`) had stale PostgreSQL and loopback
Redis guidance and are superseded by this contract.

## Required In Production

Set these through Dokploy protected environment configuration:

```text
SECRET_KEY
OPENAI_API_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
CORS_ALLOWED_ORIGINS
PDFSEARCH_IMAGE
```

Production must keep:

```text
DEBUG=False
ALLOW_INSECURE_DEFAULTS=0
CREATE_SUPERUSER=0
```

Production `PDFSEARCH_IMAGE` must be an immutable GHCR digest tied to the
approved Git SHA. The disposable 2026 stage is an explicit exception: it uses
the `:latest` channel, always pulls, and records the resolved running digest as
evidence after deployment. Development uses
`PDFSEARCH_DEV_IMAGE` (default `pdfsearch-dev:local`) plus an identical local
build definition for web and maintenance. `APP_RELEASE_VERSION` records the
local revision and `APP_IMAGE_DIGEST` remains empty for a native local build.

The application-plane contract is common across modes: web, maintenance,
Redis, `/app/data`, `/app/data-control`, health/dependency semantics, and all
critical environment keys. Development supplies an in-stack RustFS capability;
staging and production connect to externally operated RustFS. Configuration
validation emits reason codes and key names only, never rendered values.
Tags such as `latest` or `dev` do not prove which digest is running.

## Environment Identity (New — 2026-07-24)

The `EnvironmentIdentity` system (`core/environment.py`) enforces fail-closed
startup validation. Every deployment must set these:

```text
APP_ENV=production|staging|development|test|review
PRODUCTION_SOURCE_ID=<unique-source-identifier>
AUTHORITATIVE_DATASET_ID=<canonical-dataset-id>
DATASET_ID=<this-instance-dataset-id>
BACKUP_ROLE=writer|reader|disabled
BACKUP_SYNC_MODE=manual|scheduled|event-driven|hybrid
EXTERNAL_SIDE_EFFECTS_MODE=enabled|sandbox|disabled
DATA_MODE=empty|seed|local|s3-restore|s3-pinned|sanitized-production|exact-production
```

### AppEnv Enum

| Value | Description |
|-------|-------------|
| `production` | Live production deployment |
| `staging` | Pre-production staging |
| `development` | Local development |
| `test` | Automated/disposable test environment |
| `review` | Isolated review application |

### DataMode Enum

| Value | Description |
|-------|-------------|
| `empty` | No data, fresh start |
| `seed` | Bootstrap from the declared image seed |
| `local` | Use the data already present in the local volume |
| `s3-restore` | Restore-source identity with latest-generation policy |
| `s3-pinned` | Restore-source identity with a pinned generation |
| `sanitized-production` | Production-derived data requiring sanitization |
| `exact-production` | Explicitly approved exact production-derived data; forbidden in production |

The S3 modes currently validate identity and policy. They do not cause either
entrypoint to restore data automatically.

### BackupRole Enum

| Value | Description |
|-------|-------------|
| `writer` | Authoritative production publisher |
| `reader` | Read-only vault consumer |
| `disabled` | No vault publication role |

### Side-Effect Policy (`core/side_effects.py`)

`EXTERNAL_SIDE_EFFECTS_MODE` gates all external calls:

| Mode | Email | OpenAI | Payments | Webhooks |
|------|-------|--------|----------|----------|
| `enabled` | Allowed | Allowed | Allowed | Allowed |
| `sandbox` | Allowed | Allowed | Blocked | Blocked |
| `disabled` | Blocked | Blocked | Blocked | Blocked |

### AI Guard (`core/ai_guard.py`)

OpenAI client containment with three modes: `enabled`, `sandbox` (returns
deterministic mock embeddings), and `disabled` (raises on any call). The guard
is enforced at the client-construction level; no call site can bypass it.

### Startup Validation (Fail-Closed)

On startup, `EnvironmentIdentity` validates:
- `APP_ENV` is a recognized value
- `DATA_MODE` is valid for the given `APP_ENV` (production requires `live`)
- `BACKUP_ROLE` is valid
- `EXTERNAL_SIDE_EFFECTS_MODE` is valid
- `PRODUCTION_SOURCE_ID` is set when `APP_ENV=production`
- `AUTHORITATIVE_DATASET_ID` and `DATASET_ID` are set

Any validation failure exits the process before migrations run. The web
container will not reach readiness with an invalid identity.

### Instance Identity

Each instance writes a unique `instance_id` (UUID) to `/app/data/.instance_id`
on first startup. This identity is used for global writer fencing and lease
ownership. Never delete or rotate this file on a live instance.

### Build Identity

The OCI image carries build identity through:
- OCI labels: `org.opencontainers.image.revision` (Git SHA), `created`,
  `version`
- `/app/flowdocs/.release` file: Git SHA, build timestamp, CI run URL
- the resolved container image digest recorded by the deployment operator

For production, `PDFSEARCH_IMAGE` must name that exact immutable digest.
Production Compose passes the same reference to the application as
`APP_IMAGE_DIGEST`. The 2026 stage deliberately keeps `PDFSEARCH_IMAGE` on
`:latest`; its acceptance record pairs the mutable channel with the resolved
container digest and OCI revision observed after the pull.

## Artifact Vault Configuration

```text
# Legacy compatibility adapter; the stored DataOps v3 connection is canonical.
ARTIFACT_VAULT_ENDPOINT=<legacy-compatibility-endpoint>
ARTIFACT_VAULT_ACCESS_KEY=<secret-provider-reference>
ARTIFACT_VAULT_SECRET_KEY=<secret-provider-reference>
ARTIFACT_VAULT_BUCKET=<legacy-compatibility-bucket>
ARTIFACT_VAULT_REGION=<legacy-compatibility-region>
ARTIFACT_VAULT_ENABLED=0
BACKUP_SYNC_MODE=manual
MAINTENANCE_SCHEDULER_ENABLED=0
```

The vault uses S3-compatible storage (RustFS). Object store capabilities are
probed by the explicit publication path and the verification management
command. Conditional operations (If-None-Match, If-Match) gate authoritative
publication. They are not an automatic startup backup. Namespace-scoped keys
are built by `core/namespace.py` using `DATASET_ID`.

The web and maintenance services are separate processes. The service executing
sync or restore must receive the vault values, identity values, sync policy,
and immutable release identity. An optional Compose `env_file` is not a safe
substitute for explicit Dokploy service wiring.

## Restore Configuration

```text
RESTORE_SOURCE_DATASET_ID=<source-dataset-id>
RESTORE_POLICY=disabled|manual|startup-latest|startup-pinned
DATA_PINNED_GENERATION=<immutable-generation-id>
```

The supported DataOps v3 lifecycle is deliberately split:

```text
publish candidate → promote authoritative pointer
select generation → verify → download → validate → sanitize → rehearse
                  → activation_ready
confirmed activation → signed intent → runtime cutover → readiness evidence
```

DataOps v3 owns source discovery, automatic same-dataset versus foreign-source
routing, durable operations, recovery points, and restore candidates.
Compatibility and rehearsal reuse bounded core services. Signed activation
currently adapts the candidate into internal `vaultops` runtime records;
`vaultops/services/activation.py` coordinates the intent and the supervisor
owns cutover and crash recovery. This adapter is an implementation detail, not
a second operator configuration surface. A successful restore-preparation job
does not claim that active bytes changed.

The retired `core.maintenance.stage_generation()` path and the direct
`core.restore_pipeline` integration seam are not the operator recovery
contract.

Both entrypoints consume `RESTORE_POLICY` through a DB-free, fail-closed
preflight before imports, seeds, backups, migrations, queues, remote Vault
access, or activation. `disabled` and `manual` retain normal startup.
`startup-latest` and `startup-pinned` preserve a non-empty existing database
but stop an absent or zero-byte database with
`startup_restore_required_but_unavailable`; they do not perform a restore.
`startup-pinned` requires `DATA_PINNED_GENERATION` even when `DATA_MODE` is not
`s3-pinned`. Accumulated-volume and genuinely fresh-volume proofs are part of
the recovery certification contract; current results are recorded in the
living handoff. Production RustFS remains separately operator-authorized.

`RESTORE_WORKSPACE_ROOT`, `RESTORE_STAGE_TIMEOUT_SECONDS`,
`RESTORE_REHEARSAL_ENABLED`, `RESTORE_SANITIZE_ENABLED`, and
`RESTORE_COMPATIBILITY_CHECK_ENABLED` are not consumed by the current runtime
and must not be represented as active controls.

## Runtime Data

The production data root is `/app/data`; code remains in the immutable image at
`/app/flowdocs`. Use these defaults unless an approved release record says
otherwise:

```text
DATA_ROOT=/app/data
SQLITE_DB_PATH=/app/data/db.sqlite3
MEDIA_ROOT=/app/data/media
STATIC_ROOT=/app/data/staticfiles
FAISS_INDEX_DIR=/app/data/faiss_indexes
CHROMA_DIR=/app/data/chroma_db
BACKUP_DIR=/app/data/backups
LEGACY_DATA_ROOT=/mnt/legacy
IMPORT_LEGACY_DATA=0
DATA_BOOTSTRAP_MODE=strict
DECLARED_SEED_DB=/app/init/db.sqlite3
DECLARED_SEED_MEDIA=/app/init/media
```

The current app uses SQLite through `SQLITE_DB_PATH`. `DB_ENGINE`, `DB_HOST`,
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_PORT` are not consumed by the
current settings module and must not appear in active examples.

## Search And AI

```text
OPENAI_EMBED_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini
PDF_CHUNK_SIZE=1200
PDF_CHUNK_OVERLAP=200
PDF_OCR_FALLBACK_ENABLED=1
PDF_OCR_BINARY=tesseract
PDF_OCR_LANGUAGES=eng+mar+hin
PDF_OCR_DPI=200
PDF_OCR_MAX_PAGES=50
PDF_OCR_PAGE_TIMEOUT_SECONDS=180
PDF_OCR_MAX_SECONDS=900
PDF_OCR_MAX_PIXELS=25000000
MAX_CONTEXT_WORDS=2500
TOP_K_CHUNKS=5
EMBEDDING_TTL=604800
SEARCH_CACHE_TTL=600
MAX_FILE_SIZE_MB=10
ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES=67108864
```

Model, chunking, and cache changes can affect FAISS compatibility, answer
quality, latency, and cost. Record the before/after values with any release that
changes them.

`MAX_FILE_SIZE_MB` is the operator-facing upload limit. It uses 1,048,576 bytes
per configured MB, accepts positive fractional values, and is converted to
bytes internally for Django. The old `MAX_FILE_SIZE` byte variable is accepted
only as a deprecated compatibility fallback; conflicting values fail startup.
Use the reviewed environment examples and remove the old key after migration.

`ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES` is a separate positive byte limit
for read-only inventory, candidate, activation, and recovery verification of
canonical media already in custody. Its 64 MiB default accommodates reviewed
legacy PDFs while keeping verification bounded. It does not change
`MAX_FILE_SIZE_MB`, Django request limits, or the policy for new uploads.

### OCR fallback policy

Native PDF text extraction remains the first path. When a PDF has blank pages,
the application renders only those pages and invokes the immutable image's
Tesseract binary with the configured language packs. OCR is bounded by page,
time, and pixel caps; the subprocess receives an argument list and never a
shell command. Missing language packs, timeouts, or unreadable OCR output do
not mark a PDF indexed. Operators must rebuild the affected FAISS folder and
record the OCR configuration with the generation evidence after changing any
OCR setting.

See [`ENVIRONMENT_CONFIGURATION_GUIDE.md`](ENVIRONMENT_CONFIGURATION_GUIDE.md)
for the complete variable impact matrix and edge-case playbook.

## Cache And Public Search

Production Compose owns Redis and passes `REDIS_URL=redis://redis:6379/1`.
Do not set a loopback Redis URL in Dokploy; `localhost` resolves inside the web
container and is rejected unless local insecure defaults are enabled.

Anonymous search is enabled by default for the public corpus:

```text
PUBLIC_SEARCH_ENABLED=1
PUBLIC_SEARCH_FOLDER_IDS=
PUBLIC_SEARCH_MAX_WORDS=30
PUBLIC_SEARCH_RATE_LIMIT=30
PUBLIC_SEARCH_RATE_WINDOW=60
```

An empty `PUBLIC_SEARCH_FOLDER_IDS` means all folders. Use `all` explicitly or a
comma-separated allowlist when a deployment requires a restricted corpus.

Registration is not controlled by the public-search flag: `/register/` always
requires an authenticated `admin` or `superadmin`. Department-scoped admin
roles are deliberately deferred to phase 2.

## Maintenance Worker And Recovery Points

The web process only queues maintenance work. The Compose `maintenance` service
runs `run_maintenance_jobs` and records per-document progress in SQLite. Keep a
single worker active for the SQLite data root. DataOps backup, import, test
recovery, and restore remain fail-closed until their plans and manifests pass
the applicable validation gates.

```text
MAINTENANCE_SCHEDULER_ENABLED=0
VAULT_MUTATION_TRACKING_ENABLED=0
BACKUP_SYNC_MODE=manual
RESTORE_POLICY=disabled
```

`VAULT_MUTATION_TRACKING_ENABLED=1` is required for repair and reindex because
those operations create mutable candidates. It does not gate read-only
validation. The development Compose override enables it for its local
candidate workspace; production must leave it disabled until the deployment
can provide verified mutation evidence.

The worker contains unexpected execution errors to the affected durable job,
records a bounded failure reason and audit event, and continues polling. Raw
exception text is not an operator-facing status contract.

`MAINTENANCE_WORKER_POLL_SECONDS`, `ARTIFACT_VAULT_AUTO_SYNC`, `ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY`,
`ARTIFACT_VAULT_BOOTSTRAP_GENERATION`, and
`ARTIFACT_VAULT_RETENTION_COUNT` are not consumed runtime controls.

Scheduled/hybrid modes require `MAINTENANCE_SCHEDULER_ENABLED=1`, a writer
identity, a non-manual sync mode, and durable mutation-epoch evidence. Current
application mutation paths advance that evidence and the worker coalesces it
into publication work. This implementation and its CI coverage do not
authorize scheduled publication in production. Until the deployed writer,
worker, bucket, and recovery-point monitoring are separately certified, use
explicit manual publication and verify the resulting immutable generation.

## Bootstrap Credentials

`DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, and
`DJANGO_SUPERUSER_PASSWORD` are startup bootstrap inputs only when
`CREATE_SUPERUSER=1`. After the database user exists, changing these values in
`.env` does not rotate that account password. Use an authenticated Django admin
or management-command password change for existing users.

## Reserved Pass-Throughs

`GOOGLE_API_KEY` and `SENTRY_DSN` are retained as empty Compose pass-throughs for
future integrations or deployment compatibility. They are not active runtime
requirements in the current code.
