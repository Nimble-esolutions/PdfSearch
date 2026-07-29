Status: Active
Audience: Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-25
Canonical source: docs/ENVIRONMENT_CONTRACT.md
Supersedes: env.minimal, env.template

# Environment Contract

`.env.example` is the only tracked environment example. Deleted duplicate
templates (`env.minimal`, `env.template`) had stale PostgreSQL and loopback
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

`PDFSEARCH_IMAGE` must be an immutable GHCR digest tied to the approved Git SHA.
Tags such as `latest` or `dev` are compatibility aliases, not release identity.

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
- `PDFSEARCH_IMAGE` env var: the deployed digest

All three must agree for a release to be accepted.

## Artifact Vault Configuration

```text
ARTIFACT_VAULT_ENDPOINT=<S3-compatible-endpoint>
ARTIFACT_VAULT_ACCESS_KEY=<access-key>
ARTIFACT_VAULT_SECRET_KEY=<secret-key>
ARTIFACT_VAULT_BUCKET=ai-sahakar-prod-flowdocs-data-volume
ARTIFACT_VAULT_REGION=us-east-1
ARTIFACT_VAULT_ENABLED=1
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

The active Vault Operations lifecycle is deliberately split:

```text
publish candidate → promote authoritative pointer
select generation → verify → download → validate → sanitize → rehearse
                  → activation_ready
confirmed activation → signed intent → runtime cutover → readiness evidence
```

`vaultops/services/restore.py` owns restore preparation and its durable
workspace state. Compatibility and rehearsal reuse the bounded core services.
`vaultops/services/activation.py` coordinates signed activation intents and the
activation supervisor owns runtime cutover and crash recovery. A successful
restore-preparation job does not claim that active bytes changed.

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
`s3-pinned`. Plan 003 still tracks the accumulated-volume and genuinely fresh
volume deployment proofs; production RustFS remains separately
operator-authorized.

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
MAX_CONTEXT_WORDS=2500
TOP_K_CHUNKS=5
EMBEDDING_TTL=604800
SEARCH_CACHE_TTL=600
MAX_FILE_SIZE_MB=10
```

Model, chunking, and cache changes can affect FAISS compatibility, answer
quality, latency, and cost. Record the before/after values with any release that
changes them.

`MAX_FILE_SIZE_MB` is the operator-facing upload limit. It uses 1,048,576 bytes
per configured MB, accepts positive fractional values, and is converted to
bytes internally for Django. The old `MAX_FILE_SIZE` byte variable is accepted
only as a deprecated compatibility fallback; conflicting values fail startup.
Use the reviewed environment examples and remove the old key after migration.

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

## Maintenance Worker And Generations

The web process only queues maintenance work. The Compose `maintenance` service
runs `run_maintenance_jobs` and records per-document progress in SQLite. Keep a
single worker active for the SQLite data root. Generation sync and restore remain
fail-closed until their manifests pass staging validation.

```text
MAINTENANCE_WORKER_POLL_SECONDS=3
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

`ARTIFACT_VAULT_AUTO_SYNC`, `ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY`,
`ARTIFACT_VAULT_BOOTSTRAP_GENERATION`, and
`ARTIFACT_VAULT_RETENTION_COUNT` are not consumed runtime controls.

Scheduled/hybrid modes are not currently production-ready: the scheduler
requires `MAINTENANCE_SCHEDULER_ENABLED=1`, a writer identity, a non-manual
sync mode, and a dirty-state signal, but application mutations do not yet call
the dirty-state marker. Use explicit manual publication and verify the
resulting immutable generation. This metadata-only historical guidance is
superseded by the active Vault Operations publication contract.

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
