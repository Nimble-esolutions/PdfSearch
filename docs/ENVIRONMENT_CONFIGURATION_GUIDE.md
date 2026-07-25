# Environment Configuration Guide

**Status:** Active
**Audience:** Developers, release operators, and reviewers
**Last audited:** 2026-07-25
**Canonical contract:** [`ENVIRONMENT_CONTRACT.md`](ENVIRONMENT_CONTRACT.md)

This guide explains the runtime effect of the environment variables. It does
not contain secrets or production values. Start from the reviewed examples:

- [`development.env.example`](environments/development.env.example)
- [`stage.env.example`](environments/stage.env.example)
- [`production.env.example`](environments/production.env.example)

Copy only the example that matches the target environment. A `.env` file is
not a deployment identity; the running image digest, Compose configuration,
Traefik route, data generation, and environment values must agree.

## Configuration precedence

1. Protected Dokploy/Compose environment values.
2. Django settings defaults where explicitly documented.
3. Database-backed settings only for settings marked editable in the admin
   configuration registry.

Changing an environment value normally requires a container recreation. It
does not change an existing SQLite row, uploaded PDF, FAISS index, or cached
embedding. Configuration changes that affect indexing or retrieval require a
release note and compatibility review.

## Mandatory identity and release variables

| Variable | Effect | Production | Stage | Development |
| --- | --- | --- | --- | --- |
| `APP_ENV` | Selects production/staging/development safety rules | `production` | `staging` | `development` |
| `DEPLOYMENT_ID` | Identifies the deployment instance in operations evidence | Required, unique | Required, unique | Recommended |
| `DATASET_ID` | Names the local dataset namespace | Required | Required and non-production-specific | Required/recommended |
| `AUTHORITATIVE_DATASET_ID` | Identifies the canonical dataset allowed to be authoritative | Required | Canonical production ID, but stage must not write it | Local dataset ID |
| `PRODUCTION_SOURCE_ID` | Identifies the source/authority; required for production or writer mode | Required | Recommended | Recommended |
| `DATA_MODE` | Selects local, empty, seed, or restore data posture | `local` for current Compose production | `local` or controlled `s3-restore` | `empty` or `local` |
| `BACKUP_ROLE` | Controls writer/reader/disabled custody behavior | `writer` only with vault and approvals | `disabled` or `reader` | `disabled` |
| `BACKUP_SYNC_MODE` | Selects manual/scheduled/hybrid backup behavior | `manual` unless explicitly approved | `manual` | `manual` |
| `EXTERNAL_SIDE_EFFECTS_MODE` | Gates email/OpenAI/payment/webhook behavior | `enabled` | `sandbox` or `disabled` | `sandbox` |
| `PDFSEARCH_IMAGE` | Exact deployed image reference | Immutable GHCR digest | Immutable GHCR digest | Image/build reference |
| `APP_IMAGE_DIGEST` | Runtime release identity shown in admin/health evidence | Same exact image digest | Same exact image digest | Optional but useful |
| `APP_RELEASE_VERSION` | Git SHA/release identifier | Approved SHA | Approved SHA | Local SHA or blank |

Production with `BACKUP_ROLE=writer` additionally requires the artifact vault,
authoritative dataset identity, and matching image/build identity. Non-production
must not become an authoritative writer.

## Upload and retrieval settings

| Variable | Default | Runtime effect | Change risk |
| --- | ---: | --- | --- |
| `MAX_FILE_SIZE_MB` | `10` | Maximum accepted PDF size; also sets Django request/upload memory limits | Low-to-medium; affects upload acceptance and memory pressure |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Model used to embed indexed chunks and queries | High; vector dimensions/model compatibility and cost |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Model used to prepare the grounded answer | Medium-high; answer quality, latency, and cost |
| `PDF_CHUNK_SIZE` | `1200` | Approximate text chunk size for indexing | High; changes retrieval boundaries and requires reindex review |
| `PDF_CHUNK_OVERLAP` | `200` | Repeated boundary context between chunks | Medium; increases index size and context overlap |
| `MAX_CONTEXT_WORDS` | `2500` | Maximum retrieved context sent to answer preparation | High; changes grounding coverage, latency, and token cost |
| `TOP_K_CHUNKS` | `5` | Number of top chunks selected for answer context | Medium-high; affects recall, noise, and answer length |
| `EMBEDDING_TTL` | `604800` seconds | Redis/cache lifetime for embeddings | Low; affects API cost and freshness |
| `SEARCH_CACHE_TTL` | `600` seconds | Cache lifetime for repeated search answers | Low-medium; stale answers and cache pressure |

### `MAX_FILE_SIZE_MB` migration rule

Operators now set:

```env
MAX_FILE_SIZE_MB=10
```

The application interprets one configured MB as `1,048,576` bytes so the
historical 10 MB limit remains exact. Fractional values such as `10.5` are
accepted. `MAX_FILE_SIZE` is a deprecated byte-based compatibility alias for
one migration cycle. If both variables are present, they must represent the
same limit or startup fails. Remove the old key after migrating.

The form reports the human-facing MB value; internal Django upload guards still
receive bytes because Django requires byte limits.

Do not increase this value without checking proxy limits, Gunicorn request
timeouts, worker memory, PDF parsing time, and maintenance queue capacity.

## Public search and product controls

| Variable | Effect | Safe baseline |
| --- | --- | --- |
| `PUBLIC_SEARCH_ENABLED` | Allows anonymous public search | `1` for the public service |
| `DISPLAY_SERVICE_FOOTER` | Shows the service footer/disclaimer | `1` |
| `PUBLIC_SEARCH_FOLDER_IDS` | Empty/all or comma-separated folder allowlist | Empty only when the corpus is approved public |
| `PUBLIC_SEARCH_MAX_WORDS` | Maximum words per public question | `30` |
| `PUBLIC_SEARCH_RATE_LIMIT` | Requests per rate window | `30` |
| `PUBLIC_SEARCH_RATE_WINDOW` | Rate window in seconds | `60` |

`PUBLIC_SEARCH_ENABLED` does not control registration; `/register/` remains
authenticated. An empty folder allowlist exposes all current/future folders,
so use an explicit allowlist for restricted stage or test corpora.

## Security and service infrastructure

| Group | Variables | Notes |
| --- | --- | --- |
| Django security | `SECRET_KEY`, `DEBUG`, `ALLOW_INSECURE_DEFAULTS`, `ALLOWED_HOSTS` | Secret key protected; no debug/insecure defaults outside local development |
| Browser security | `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`, HSTS variables | Include the actual stage/prod hostname; verify proxy behavior before enabling redirects |
| Cache | `REDIS_URL` | Production/stage must use `redis://redis:6379/1`, never loopback |
| Persistent paths | `DATA_ROOT`, `SQLITE_DB_PATH`, `MEDIA_ROOT`, `STATIC_ROOT`, `FAISS_INDEX_DIR`, `CHROMA_DIR`, `BACKUP_DIR`, `LEGACY_DATA_ROOT` | Keep code in the image and mutable data in `/app/data`; legacy is read-only custody |
| Runtime | `GUNICORN_WORKERS`, `GUNICORN_MAX_REQUESTS`, `GUNICORN_MAX_REQUESTS_JITTER`, `GUNICORN_TIMEOUT` | Tune against CPU, RAM, request latency, and worker restart behavior |
| Upload | `MAX_FILE_SIZE_MB`, `MAX_FILE_SIZE` legacy alias, `MAX_FILE_SIZE` internal setting | Use MB externally; never expose byte-only configuration to operators |
| Notifications | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `SENTRY_DSN` | Optional; keep credentials protected and side-effect mode aligned |

The current application uses SQLite. `DB_ENGINE`, `DB_HOST`, `DB_NAME`,
`DB_USER`, and `DB_PORT` are not active settings and must not be added to new
examples.

## Data custody and vault variables

`ARTIFACT_VAULT_ENABLED`, `ARTIFACT_VAULT_ENDPOINT`, `ARTIFACT_VAULT_BUCKET`,
`ARTIFACT_VAULT_REGION`, `ARTIFACT_VAULT_ACCESS_KEY`, and
`ARTIFACT_VAULT_SECRET_KEY` control the RustFS/S3-compatible recovery vault.
Automatic sync and pull remain disabled by default:

```env
ARTIFACT_VAULT_AUTO_SYNC=0
ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY=0
```

Restore variables (`RESTORE_SOURCE_DATASET_ID`, `RESTORE_WORKSPACE_ROOT`,
`RESTORE_STAGE_TIMEOUT_SECONDS`, `RESTORE_REHEARSAL_ENABLED`,
`RESTORE_SANITIZE_ENABLED`, and `RESTORE_COMPATIBILITY_CHECK_ENABLED`) apply
only to explicit restore workflows. They do not belong in a routine local
development configuration.

## Edge-case playbook

- **Large PDFs:** raise `MAX_FILE_SIZE_MB` only with proxy/memory/timeouts
  reviewed; do not use a byte literal in Dokploy.
- **New embedding model:** record model, dimensions, index compatibility, cost,
  and reindex plan before changing `OPENAI_EMBED_MODEL`.
- **No OpenAI in local/CI:** use disposable test embeddings only in CI/test
  configuration; never enable them to mask a stage/production API failure.
- **Restricted stage corpus:** set `PUBLIC_SEARCH_FOLDER_IDS` explicitly and
  verify anonymous search cannot access other folders.
- **Sanitized stage restore:** use `DATA_MODE=s3-restore`, a different
  `DATASET_ID`, `RESTORE_SOURCE_DATASET_ID`, disabled/sandbox effects, and a
  reader/disabled backup role.
- **Rollback:** restore the previous immutable image digest and matching data
  generation; do not roll back by retagging `latest`.
