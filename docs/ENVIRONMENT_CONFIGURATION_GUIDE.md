# Environment Configuration Guide

## Current 2026 stage posture

The stage moved beyond its earlier empty/private rehearsal posture. The latest
verified state is a public, approved production-derived rehearsal with a signed
active generation, 242/242 indexed PDFs, manual backup evidence, and an
isolated restore receipt:

    APP_ENV=staging
    DATA_MODE=local
    DATA_BOOTSTRAP_MODE=strict
    DATASET_ID=ai-sahakar-stage-2026
    DATAOPS_ENABLED=1
    BACKUP_ROLE=reader
    BACKUP_SYNC_MODE=manual
    STAGING_INITIAL_ACTIVATION_ENABLED=1
    STAGING_RUNTIME_ACTIVATION_ENABLED=1

This is a non-secret example only. Do not copy credential, API-key, signing-key,
or recovery-password values into Git or support records. Environment changes
must be applied through the preserved Dokploy Compose project and verified
inside both web and maintenance containers. The current status and volume
boundary are documented in [`HANDOFF.md`](HANDOFF.md). The dated
`STATUS-2026-08-02.md` page is historical evidence, not current configuration.

**Status:** Active
**Audience:** Developers, release operators, and reviewers
**Last audited:** 2026-08-06
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
| `EXTERNAL_SIDE_EFFECTS_MODE` | Gates non-AI email/payment/webhook behavior | `enabled` | `sandbox` or `disabled` | `sandbox` |
| `EXTERNAL_AI_MODE` | Independently gates OpenAI/AI provider behavior; inherits the general mode only when absent | Explicit reviewed policy | Explicit reviewed policy | `sandbox` or `disabled` |
| `PDFSEARCH_IMAGE` | Compose image reference | Immutable GHCR digest | `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest` | Image/build reference |
| `APP_IMAGE_DIGEST` | Runtime image marker shown in admin/health evidence | Derived by Compose from `PDFSEARCH_IMAGE` | Derived by Compose from `PDFSEARCH_IMAGE` | Optional locally; record the resolved container digest separately |
| `APP_RELEASE_VERSION` | Git SHA/release identifier | Approved SHA | Approved SHA | Local SHA or blank |

Production with `BACKUP_ROLE=writer` additionally requires the artifact vault,
authoritative dataset identity, and matching image/build identity. Non-production
must not become an authoritative writer.

## Upload and retrieval settings

| Variable | Default | Runtime effect | Change risk |
| --- | ---: | --- | --- |
| `MAX_FILE_SIZE_MB` | `10` | Maximum accepted PDF size; also sets Django request/upload memory limits | Low-to-medium; affects upload acceptance and memory pressure |
| `ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES` | `67108864` | Maximum canonical existing-media size accepted by read-only custody verification | Medium; raising it increases verification I/O, but never upload acceptance |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Model used to embed indexed chunks and queries | High; candidate preflight rejects unknown or dimension-incompatible model changes before mutation; changing vector space requires a reviewed full reindex |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Model used to prepare the grounded answer | Medium-high; answer quality, latency, and cost |
| `PDF_CHUNK_SIZE` | `1200` | Approximate text chunk size for indexing | High; changes retrieval boundaries and requires reindex review |
| `PDF_CHUNK_OVERLAP` | `200` | Repeated boundary context between chunks | Medium; increases index size and context overlap |
| `PDF_OCR_FALLBACK_ENABLED` | `1` | OCR blank PDF pages when native text extraction returns no text | Medium-high; adds CPU and derived-text work for scanned PDFs |
| `PDF_OCR_BINARY` | `tesseract` | OCR executable resolved inside the immutable image | High; missing or mismatched binaries fail scanned-document indexing closed |
| `PDF_OCR_LANGUAGES` | `eng+mar+hin` | Tesseract language packs used for OCR | High; changing languages requires OCR/reindex review |
| `PDF_OCR_DPI` | `200` | Rasterization resolution for OCR pages | Medium-high; raises CPU and memory use as it increases |
| `PDF_OCR_MAX_PAGES` | `50` | Maximum blank pages OCR will process per PDF | High; exceeding the cap leaves the PDF unindexed rather than partial |
| `PDF_OCR_PAGE_TIMEOUT_SECONDS` | `180` | Per-page OCR subprocess timeout | Medium; bounds worker occupancy |
| `PDF_OCR_MAX_SECONDS` | `900` | Total OCR timeout per PDF | Medium-high; bounds a single maintenance item |
| `PDF_OCR_MAX_PIXELS` | `25000000` | Maximum rendered pixels for one OCR page | High; prevents oversized scans from exhausting worker memory |
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

Keep `ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES` independent from the upload
limit. The default is 64 MiB and applies only when inventory, candidate,
activation, or recovery code verifies canonical media already in custody.
Changing it does not alter Django request-body limits or permit a new upload
larger than `MAX_FILE_SIZE_MB`.

The form reports the human-facing MB value; internal Django upload guards still
receive bytes because Django requires byte limits.

Do not increase this value without checking proxy limits, Gunicorn request
timeouts, worker memory, PDF parsing time, and maintenance queue capacity.

### OCR fallback policy

Native PDF extraction remains the first path. When a PDF contains pages without
native text, the application renders only those pages and invokes Tesseract
inside the immutable application image. OCR is bounded by language, page,
time, and pixel settings; failures leave the document unindexed instead of
creating an unverified search artifact. Any OCR configuration change requires
an explicit reindex review because it changes derived text and FAISS output.

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

`ARTIFACT_VAULT_ENDPOINT`, `ARTIFACT_VAULT_BUCKET`,
`ARTIFACT_VAULT_REGION`, `ARTIFACT_VAULT_ACCESS_KEY`, and
`ARTIFACT_VAULT_SECRET_KEY` control the RustFS/S3-compatible recovery vault.
Use explicit manual publication for the current production path:

```env
BACKUP_ROLE=writer
BACKUP_SYNC_MODE=manual
MAINTENANCE_SCHEDULER_ENABLED=0
RESTORE_POLICY=disabled
```

The worker that executes publication must receive the vault credentials,
writer identity, image marker, and `APP_RELEASE_VERSION`. Production Compose
maps the connection and credential set to both `web` and `maintenance`; the
effective Compose parity check must continue to prove that lifecycle-critical
environment remains aligned.

Restore identity uses `RESTORE_SOURCE_DATASET_ID`, `RESTORE_POLICY`, and, for
`s3-pinned` or `startup-pinned`, `DATA_PINNED_GENERATION`. These values do not
trigger either entrypoint to restore. Both entrypoints do consume `startup-*`
as a fail-closed posture: an absent/zero-byte database stops before migration,
while a non-empty database is preserved. The supported operator restore path
is DataOps v3: it chooses same-dataset restore or foreign-source import from
provenance, prepares an isolated candidate, and keeps signed activation
separate. Older `VAULT_*` restore switches are internal compatibility controls,
not the standard operator configuration contract.

`MAINTENANCE_WORKER_POLL_SECONDS`, `ARTIFACT_VAULT_AUTO_SYNC`, `ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY`,
`ARTIFACT_VAULT_BOOTSTRAP_GENERATION`, `ARTIFACT_VAULT_RETENTION_COUNT`,
`RESTORE_WORKSPACE_ROOT`, `RESTORE_STAGE_TIMEOUT_SECONDS`,
`RESTORE_REHEARSAL_ENABLED`, `RESTORE_SANITIZE_ENABLED`, and
`RESTORE_COMPATIBILITY_CHECK_ENABLED` are not current runtime controls.

See [`RUSTFS_RECOVERY_VAULT.md`](RUSTFS_RECOVERY_VAULT.md) for the audited
fresh-volume and accumulated-volume behavior.

## Edge-case playbook

- **Large new uploads:** raise `MAX_FILE_SIZE_MB` only with proxy/memory/timeouts
  reviewed together. For canonical legacy media already in custody, adjust
  `ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES` independently and retain a bounded
  verification limit; do not substitute a byte literal for
  `MAX_FILE_SIZE_MB` in Dokploy.
- **New embedding model:** record model, dimensions, index compatibility, cost,
  and reindex plan before changing `OPENAI_EMBED_MODEL`.
- **No OpenAI in local/CI:** use disposable test embeddings only in CI/test
  configuration; never enable them to mask a stage/production API failure.
- **Restricted stage corpus:** set `PUBLIC_SEARCH_FOLDER_IDS` explicitly and
  verify anonymous search cannot access other folders.
- **Sanitized stage restore:** use a different `DATASET_ID`, explicit
  `RESTORE_SOURCE_DATASET_ID`, disabled/sandbox effects, and a reader/disabled
  backup role. Run the full pipeline in an isolated target; setting
  `DATA_MODE=s3-restore` alone does not restore.
- **Fresh volume:** fail the release if data was expected. Do not accept
  successful migrations or an HTTP 200 as proof that the vault restored data.
- **Accumulated volume:** compare the last immutable generation with current
  database/PDF/FAISS counts. Vault health does not describe recovery-point age.
- **Rollback:** restore the previous immutable image digest and matching data
  generation; do not roll back by retagging `latest`.
