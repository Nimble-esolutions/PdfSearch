Status: Active
Audience: Developers, operators, reviewers, and release managers
Owner: FlowDocs maintainers
Last verified: 2026-08-02
Canonical source: docs/ENVIRONMENT_REFERENCE.md
Related: docs/ENVIRONMENT_CONTRACT.md, docs/ENVIRONMENT_CONFIGURATION_GUIDE.md

# FlowDocs environment reference

This is the navigation and decision guide for the environment variables used by
FlowDocs. It explains what each variable controls, where it belongs, and what
can go wrong when it is changed.

The repository examples are intentionally non-secret. They are reviewed
templates, not exports of a running server. A running deployment is authoritative
only when its effective environment, image digest, mounted volume identities,
and readiness evidence have been inspected together.

## Start here

| Need | Use | Safe default |
| --- | --- | --- |
| Start local development | .env.example and docs/environments/development.env.example | Empty local dataset, sandboxed side effects, no automatic activation |
| Configure the 2026 stage | docs/environments/stage.env.example plus .env.dataops.example | Stage profile for restore/backup, manual backup until receipt verification, signed activation still off |
| Review production posture | docs/environments/production.env.example | Verify production identity from the running service; do not copy stage or migration-source values |
| Understand validation rules | docs/ENVIRONMENT_CONTRACT.md | Startup rejects unsafe identity, role, and restore combinations |
| Understand effects of a change | docs/ENVIRONMENT_CONFIGURATION_GUIDE.md | Inspect effective values without printing secrets |
| Operate Data Operations | docs/dataops/ENV_CONTRACT.md and docs/dataops/ROLLOUT.md | Profile-based RustFS access, explicit operations, immutable receipts |

## One variable, one source of truth

There are three configuration layers:

| Layer | File or system | Purpose | Precedence |
| --- | --- | --- | --- |
| Application template | .env.example | Copyable local reference and compatibility map | Lowest |
| Environment template | docs/environments/*.env.example | Reviewed dev, stage, and production posture | Human-controlled deployment input |
| Data Operations contract | .env.dataops.example | Structured RustFS profiles and recovery controls | Authoritative for Data Operations |
| Deployment secrets | Dokploy secret provider | Passwords, API keys, signing keys, object-store credentials | Never committed |
| Effective runtime | Compose/Dokploy container environment | What the process actually uses | Operational truth |

If two variables describe the same concern, the newer contract wins. The
profile-based DATAOPS_* contract is authoritative for RustFS recovery. The
older ARTIFACT_VAULT_* and VAULT_* values remain for compatibility and explicit
operator workflows; they must not silently create cross-dataset transfers or
automatic activation.

## Environment posture at a glance

| Concern | Development | 2026 stage | Production |
| --- | --- | --- | --- |
| APP_ENV | development | staging | production |
| Dataset | ai-sahakar-dev | ai-sahakar-stage-2026 | Verify the authoritative production dataset |
| Data bootstrap | empty | empty for the current rehearsal; strict for a normal populated deployment | strict |
| External side effects | sandbox | sandbox | enabled |
| External AI | sandbox or disabled | enabled only for approved retrieval/embeddings | enabled only by approved policy |
| Public anonymous search | enabled locally | enabled for the current search route; does not approve public authentication data | enabled by product policy |
| Backup role | disabled | reader at the application boundary; stage Data Operations profile is both | disabled in the unchanged legacy production baseline |
| Data Operations backup | manual local profile | stage_2026, manual until first receipt | Separate reviewed production policy |
| Restore auto-activation | disabled | disabled | disabled |
| Signed activation | disabled | pending operator evidence | disabled |
| Exact production authentication data | never copied | private-only unless a security owner approves the permanent exception | authoritative |

## Naming and value conventions

| Convention | Meaning |
| --- | --- |
| 0 / false / disabled | Capability is intentionally off |
| 1 / true / enabled | Capability is on, subject to startup and operation gates |
| manual | An operator must request and review the operation |
| scheduled | A bounded scheduler may request work; it does not bypass validation |
| strict | A populated compatible dataset is required; accidental empty bootstrap fails |
| empty | Start a deliberately empty disposable volume |
| s3-restore | Restore through the controlled restore pipeline, not a raw directory copy |
| exact-production | Exact production-derived data; use only with explicit custody and security approval |
| <...> | Required operator value; never replace with a guess |

## 1. Identity, security, and release

| Variable | What it controls | Development | Stage | Production | Change impact and notes |
| --- | --- | --- | --- | --- | --- |
| SECRET_KEY | Django signing and cryptographic application secret | Local-only value is acceptable | Dokploy secret | Dokploy secret | Rotation invalidates signed state and may affect sessions; never reuse a public example |
| DEBUG | Django debug behavior | True only locally | False | False | Enabling it can expose internals and must fail policy checks outside local development |
| ALLOW_INSECURE_DEFAULTS | Allows local-only defaults | 1 | 0 | 0 | Keep 0 wherever data or users are real |
| APP_ENV | Runtime policy selector | development | staging | production | Changes validation, side-effect, bootstrap, and backup rules |
| DEPLOYMENT_ID | Unique deployment identity | local-dev | stage-2026-ai-sahakar | Verify from deployment | Used in receipts, leases, logs, and generation ownership |
| DATASET_ID | Dataset boundary for reads/writes | ai-sahakar-dev | ai-sahakar-stage-2026 | Verify from running production | A mismatch must fail; never use it to bypass custody |
| AUTHORITATIVE_DATASET_ID | Dataset accepted as authoritative | Local dataset | Stage dataset | Verify from running production | Prevents accidental writes to another environment |
| PRODUCTION_SOURCE_ID | Source identity used in lineage | local-dev | ai-sahakar-prod-v2 | Operator-supplied | Stage uses the v2 migration source, not the stage identity |
| DATA_MODE | Data source/bootstrap mode | empty | local for current mounted rehearsal | local for unchanged legacy service | s3-restore and exact-production require explicit restore evidence |
| NONPROD_DATA_POLICY | Whether non-production data is sanitized or exact | none | sanitized or approved exact custody | blank | Exact production authentication data is a security exception |
| BACKUP_ROLE | Local application backup authority | disabled | reader | disabled in legacy baseline | Do not make multiple environments writers |
| BACKUP_SYNC_MODE | Local backup scheduling policy | manual | manual | manual | Scheduled mode needs a tested receipt and bounded retries |
| RESTORE_POLICY | Whether restore is permitted | manual | manual | disabled | Restore is a controlled operation, not startup convenience |
| RESTORE_SOURCE_DATASET_ID | Restore source boundary | blank | stage dataset or explicit source | blank | Must match the selected profile and manifest |
| DATA_PINNED_GENERATION | Exact generation to use | blank | explicit during rehearsal | blank | Pinning prevents moving-target restores; record the digest |
| EXTERNAL_SIDE_EFFECTS_MODE | Email, webhook, payment, and similar effects | sandbox | sandbox | enabled | Never let production-derived stage data trigger real external effects |
| EXTERNAL_AI_MODE | AI-only provider policy | sandbox or disabled | enabled by reviewed policy | enabled by reviewed policy | Sandbox is refused in review/stage/prod; this does not authorize unrelated side effects |
| APP_RELEASE_VERSION | Human-readable release identity | local-dev | approved Git SHA | approved Git SHA | Must agree with release evidence |
| APP_IMAGE_DIGEST | Immutable running image identity | blank locally | immutable GHCR digest | immutable GHCR digest | Mutable tags are aliases; verify the digest from the running container |
| PDFSEARCH_IMAGE | Compose image reference | local image may be used | immutable digest | immutable digest | A tag pull is not proof that the intended image is running |
| PDFSEARCH_DEV_IMAGE | Local development image | pdfsearch-dev:local | unused | unused | Never use a local tag for deployment |
| LOCAL_BUILD_REVISION | Local build marker | local-dev | unused | unused | Useful for local diagnostics only |
| ALLOWED_HOSTS | Host header allowlist | localhost values | 2026.ai-sahakar.net and approved aliases | production domains | A missing host causes request rejection; a broad value weakens routing protection |
| DJANGO_SETTINGS_MODULE | Django settings module | flowdocs.settings | flowdocs.settings | flowdocs.settings | Keep consistent with the image entrypoint |

## 2. Persistent paths and bootstrap

| Variable | Purpose | Expected path or value | Notes |
| --- | --- | --- | --- |
| DATA_ROOT | Application data root | /app/data | Contains database, media, indexes, cache, and generations |
| SQLITE_DB_PATH | SQLite database | /app/data/db.sqlite3 | Database integrity and migration state are release evidence |
| MEDIA_ROOT | Uploaded PDFs and media | /app/data/media | Reconcile with database rows; do not copy directly into active data |
| STATIC_ROOT | Collected static files | /app/data/staticfiles | Not part of the legacy data snapshot |
| FAISS_INDEX_DIR | Folder/search indexes | /app/data/faiss_indexes | Derived artifacts; compatibility and indexing checks are required |
| CHROMA_DIR | Optional vector store | /app/data/chroma_db | Include only when present and compatible |
| PDF_CACHE_DIR | Render or extraction cache | /app/data/pdf_cache | Include in recovery when it is part of the approved generation |
| BACKUP_DIR | Local backup workspace | /app/data/backups | Never treat local history as the authoritative RustFS generation |
| DATA_CONTROL_ROOT | Control-plane data | /app/data-control | Keep paired with data volumes for activation and recovery evidence |
| CONTROL_DB_PATH | Control database | /app/data-control/control.sqlite3 | Contains registrations, receipts, leases, and activation state |
| VAULT_RESTORE_ROOT | Quarantine restore root | /app/data/restore-quarantine | Never make quarantine the active runtime implicitly |
| RUNTIME_GENERATIONS_ROOT | Atomic runtime generations | /app/data/runtime-generations | Activation changes a pointer only after signed evidence |
| LEGACY_DATA_ROOT | Read-only legacy mount | /mnt/legacy | Source boundary only; never use as an active target |
| IMPORT_LEGACY_DATA | One-time legacy import switch | 0 | Enable only in a disposable, read-only-source migration procedure |
| DATA_BOOTSTRAP_MODE | Empty versus populated startup | empty locally, empty for current stage rehearsal, strict in production | An empty value is not a restore; it deliberately starts without data |
| DECLARED_SEED_DB | Optional image/mount seed database | blank unless declared | A seed must be inventoried and compatible |
| DECLARED_SEED_MEDIA | Optional image/mount seed media | blank unless declared | Do not silently mix seed media with a production-derived database |
| FIXTURE_BACKUP | Test fixture backup behavior | 0 | Never enable for production custody |
| RUN_JSON_MIGRATIONS | Migration helper switch | 0 | Use the approved release process; inspect migration evidence |

## 3. OCR, extraction, chunking, and embeddings

| Variable | What it controls | Current reviewed value | Notes |
| --- | --- | --- | --- |
| OPENAI_API_KEY | Embedding/chat provider credential | Secret or blank locally | Extracted text may be sent for approved embeddings; document bytes are not sent by the local OCR path |
| OPENAI_EMBED_MODEL | Embedding model | text-embedding-3-small | Changing it invalidates or partitions vector compatibility; reindex deliberately |
| OPENAI_CHAT_MODEL | Answer-generation model | gpt-4o-mini | Affects answer behavior and cost, not source custody |
| EXTERNAL_EMBEDDINGS_ENABLED | Whether external embeddings are allowed | 0 in the root local example; explicit stage policy | Keep disabled for tests; enable only with a configured provider |
| PDF_CHUNK_SIZE | Approximate chunk size | 1200 | Changes chunk boundaries and requires reindexing |
| PDF_CHUNK_OVERLAP | Context overlap | 200 | Higher values increase storage and embedding work |
| PDF_OCR_FALLBACK_ENABLED | OCR for image/scanned PDFs | 1 | Fallback is triggered when extracted text is insufficient |
| PDF_OCR_BINARY | OCR executable | tesseract | Container image must contain the binary and traineddata |
| PDF_OCR_LANGUAGES | OCR language packs | eng+mar+hin | Required reviewed coverage for English, Marathi, and Hindi; use plus-separated Tesseract codes |
| PDF_OCR_DPI | Rasterization resolution | 200 | Higher DPI can improve small text but increases CPU, pixels, and timeout risk |
| PDF_OCR_MAX_PAGES | Per-document OCR page bound | 50 | Bounds work for large documents |
| PDF_OCR_PAGE_TIMEOUT_SECONDS | Per-page wall-clock limit | 180 | Protects the worker from pathological pages |
| PDF_OCR_MAX_SECONDS | Per-document OCR budget | 900 | A timeout leaves evidence for retry or review; it must not make a partial result authoritative |
| PDF_OCR_MAX_PIXELS | Raster safety bound | 25000000 | Prevents oversized page allocations |
| MAX_CONTEXT_WORDS | Answer context budget | 2500 | Affects prompt size and answer grounding |
| TOP_K_CHUNKS | Retrieved chunks | 5 | Higher values increase context and provider cost |
| EMBEDDING_TTL | Embedding cache lifetime | 604800 | Cache invalidation must follow model/chunk configuration changes |
| SEARCH_CACHE_TTL | Search cache lifetime | 600 | Affects freshness, not source data |
| MAX_FILE_SIZE_MB | New upload limit | 10 | MiB-equivalent application limit |
| ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES | Read-only inventory limit | 67108864 | Does not increase upload limits |

## 4. Public web and browser security

| Variable | Effect | Development | Stage | Production | Notes |
| --- | --- | --- | --- | --- | --- |
| PUBLIC_SEARCH_ENABLED | Anonymous search route | 1 | 1 for current 2026 search posture | 1 by policy | This is anonymous search only; it does not approve production passwords or admin exposure |
| PUBLIC_SEARCH_FOLDER_IDS | Public corpus allowlist | blank or test IDs | blank means reviewed public corpus | product-defined | Keep blank only when all current/future folders are intended to be public |
| PUBLIC_SEARCH_MAX_WORDS | Query length bound | 30 | 30 | 30 | Protects retrieval and provider budgets |
| PUBLIC_SEARCH_RATE_LIMIT | Requests per window | 30 | 30 | 30 | Tune with traffic evidence |
| PUBLIC_SEARCH_RATE_WINDOW | Rate-limit window seconds | 60 | 60 | 60 | Applies with PUBLIC_SEARCH_RATE_LIMIT |
| DISPLAY_SERVICE_FOOTER | Service identification | 1 | 1 | 1 | Keep enabled for support and provenance |
| CORS_ALLOWED_ORIGINS | Browser cross-origin allowlist | localhost origins | stage origins | production origins | Exact origins only |
| CSRF_TRUSTED_ORIGINS | Django CSRF trusted origins | localhost origins | stage HTTPS origins | production HTTPS origins | Must include scheme |
| CSRF_COOKIE_SECURE | Secure CSRF cookie | false locally | true | true | Requires HTTPS outside local development |
| SESSION_COOKIE_SECURE | Secure session cookie | false locally | true | true | Never disable on public HTTPS deployments |
| SECURE_SSL_REDIRECT | Django redirect | false behind local/proxy contract | proxy-dependent | proxy-dependent | Verify with the actual proxy; avoid redirect loops |
| SECURE_HSTS_SECONDS | HSTS duration | 0 | reviewed value | reviewed value | Enable only when HTTPS and subdomains are proven |
| SECURE_HSTS_INCLUDE_SUBDOMAINS | HSTS subdomains | false | reviewed value | reviewed value | Affects every subdomain |
| SECURE_HSTS_PRELOAD | Browser preload request | false | reviewed value | reviewed value | Only after permanent HTTPS readiness |
| SENTRY_DSN | Error reporting destination | blank | approved DSN | approved DSN | Never put credentials in logs or examples |
| GOOGLE_API_KEY | Optional external integration | blank | approved only | approved only | Keep blank unless a feature requires it |

## 5. Redis, workers, and runtime bounds

| Variable | Purpose | Reviewed value | Notes |
| --- | --- | --- | --- |
| REDIS_URL | Cache/queue endpoint | redis://redis:6379/1 | In Dokploy, redis is the service name; localhost means the web container |
| MAINTENANCE_WORKER_POLL_SECONDS | Maintenance polling cadence | 3 | Lower values increase load |
| MAINTENANCE_WORKER_HEARTBEAT_PATH | Worker liveness file | /app/data-control/runtime/maintenance-worker.heartbeat | Paired with readiness evidence |
| MAINTENANCE_WORKER_HEARTBEAT_MAX_AGE_SECONDS | Worker heartbeat freshness | 30 | Stale evidence degrades readiness |
| MAINTENANCE_WORKER_READINESS_REQUIRED | Require worker readiness | 1 | Keep enabled in stage/production |
| MAINTENANCE_JOB_TIMEOUT_SECONDS | Maintenance job bound | 7200 | Shared bound for migration rehearsal, candidate preparation, reindex, and OCR; large legacy databases must not use a separate short timeout |
| MAINTENANCE_SCHEDULER_ENABLED | Automatic scheduler | 0 in reviewed examples | Enable only with writer fencing and budgets |
| MAINTENANCE_WORKSPACE_ROOT | Isolated workspaces | /app/data-control/maintenance-workspaces | Never use the active generation as a scratch area |
| GUNICORN_WORKERS | Web worker count | 1 local, 2 stage, 4 production example | Match memory and concurrency budget |
| GUNICORN_MAX_REQUESTS | Worker recycling bound | 100 local, 500 stage, 1000 production | Protects long-lived workers |
| GUNICORN_MAX_REQUESTS_JITTER | Recycling jitter | 0 local, 25 stage, 50 production | Avoids synchronized recycling |
| GUNICORN_TIMEOUT | Request timeout seconds | 300 | Does not replace background-job bounds |
| WEB_PORT | Internal web port | 8000 | Traefik/proxy owns public ingress |

## 6. Legacy artifact-vault compatibility

| Variable | Purpose | Reviewed posture | Notes |
| --- | --- | --- | --- |
| ARTIFACT_VAULT_ENABLED | Older S3/RustFS adapter | 0 in root and stage examples; local dev may use local RustFS | Data Operations profiles are canonical for new recovery work |
| ARTIFACT_VAULT_ENDPOINT | Older endpoint | Blank unless explicitly local/legacy | Do not point it at a new bucket accidentally |
| ARTIFACT_VAULT_BUCKET | Older bucket | Blank or explicitly reviewed | The v2 source and stage buckets belong in DATAOPS_PROFILE_MANIFEST |
| ARTIFACT_VAULT_REGION | S3 region | us-east-1 when local/approved | Must match the provider configuration |
| ARTIFACT_VAULT_ACCESS_KEY | Older access key | Secret provider only | Never commit |
| ARTIFACT_VAULT_SECRET_KEY | Older secret key | Secret provider only | Never commit |
| ARTIFACT_VAULT_AUTO_SYNC | Older automatic sync | 0 | Do not enable alongside profile-based controls without a reviewed migration |
| ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY | Pull on empty startup | 0 | Startup must not select an arbitrary generation |
| ARTIFACT_VAULT_BOOTSTRAP_GENERATION | Older pinned generation | blank | Use explicit Data Operations restore instead |
| ARTIFACT_VAULT_RETENTION_COUNT | Older local retention | 5 | Does not control RustFS immutable retention |
| DEV_RUSTFS_ACCESS_KEY | Local RustFS access key | local-test-only | Disposable local value only; never reuse in stage or production |
| DEV_RUSTFS_SECRET_KEY | Local RustFS secret key | local-test-only-change-me | Disposable local value only; never commit a real credential |
| DEV_RUSTFS_BUCKET | Local RustFS bucket | pdfsearch-dev | Keep local buckets separate from recovery buckets |

## 7. Vault sync and snapshot safety

| Variable | Effect | Safe reviewed posture | Operational warning |
| --- | --- | --- | --- |
| VAULT_SYNC_ENABLED | Enables local active sync controller | 0 | Requires authoritative writer and mutation tracking |
| VAULT_SYNC_MODE | disabled/manual/scheduled/continuous_coalesced | manual | Scheduling does not make a generation authoritative |
| VAULT_SYNC_PROMOTION_MODE | Candidate promotion policy | manual | Production rejects unsafe automatic promotion |
| VAULT_SYNC_INTERVAL_SECONDS | Sync cadence | 900 | Bound provider and disk load |
| VAULT_SYNC_QUIET_PERIOD_SECONDS | Stable-snapshot quiet period | 120 | Source mutation during scans must fail/retry |
| VAULT_SYNC_MAX_LAG_SECONDS | Freshness bound | 3600 | Stale evidence must be visible |
| VAULT_SYNC_MAX_PARALLEL_UPLOADS | Upload concurrency | 4 | Increase only with provider and host evidence |
| VAULT_SYNC_MAX_PARALLEL_HASHERS | Hash concurrency | 2 | Hashing competes with OCR/indexing for CPU |
| VAULT_DEFAULT_PROFILE | Older profile selector | production | Prefer DATAOPS_* profile selection |
| VAULT_SNAPSHOT_ROOT | Candidate workspace | /app/data-control/snapshots | Keep outside active data |
| VAULT_SNAPSHOT_BARRIER_TIMEOUT_SECONDS | Snapshot barrier bound | 30 | Failed barriers do not publish |
| VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS | Cleanup delay | 120 | Allows evidence review |
| VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS | Cleanup item bound | 8 | Prevents large destructive passes |
| VAULT_SNAPSHOT_CLEANUP_MAX_SCAN_ITEMS | Cleanup scan bound | 64 | Prevents unbounded scans |
| VAULT_SNAPSHOT_CLEANUP_MAX_BYTES | Cleanup byte bound | 536870912 | Prevents accidental bulk deletion |
| VAULT_SNAPSHOT_CLEANUP_MAX_SECONDS | Cleanup time bound | 5 | Cleanup can be retried safely |
| VAULT_SNAPSHOT_FAISS_MAX_VECTORS | FAISS candidate vector bound | 1000000 | Input safety bound, not a memory guarantee |
| VAULT_SNAPSHOT_FAISS_MAX_DIMENSIONS | Vector dimension bound | 4096 | Must match the approved embedding model |
| VAULT_SNAPSHOT_FAISS_MAX_BYTES | Derived FAISS size bound | 536870912 | Prevents oversized candidate artifacts |
| VAULT_SNAPSHOT_FAISS_MAX_SOURCE_BYTES | Source artifact bound | 1073741824 | Protects candidate processing |
| VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES | Per-PDF JSON bound | 134217728 | Decoded values can use more memory |
| VAULT_SNAPSHOT_FAISS_MAX_PDFS | PDF count bound | 100000 | Tune with workload evidence |
| VAULT_SNAPSHOT_FAISS_MAX_CHUNKS_PER_PDF | Chunk bound | 100000 | Prevents pathological documents |
| VAULT_JOB_HEARTBEAT_SECONDS | Job heartbeat cadence | 15 | Used to detect stale work |
| VAULT_JOB_STALE_SECONDS | Stale job threshold | 90 | Stale work can be retried, not silently promoted |
| VAULT_VALIDATION_MAX_AGE_SECONDS | Validation freshness | 1800 | Activation requires current evidence |

## 8. Vault networking, credentials, restore, and UI

| Variable | Purpose | Safe posture | Notes |
| --- | --- | --- | --- |
| VAULT_RESTORE_ENABLED | Enables controlled restore | 0 by default | Enable only for an explicit operation |
| STAGE_SAME_DATASET_RESTORE_ENABLED | Allows an explicitly gated stage recovery point to be restored back into the same stage dataset | 0 by default | Stage-only rehearsal switch; keep disabled in production and retain admin/confirmation gates |
| VAULT_ADMIN_MUTATIONS_ENABLED | Allows operator mutations | 0 by default | UI visibility is not mutation authority |
| VAULT_MUTATION_TRACKING_ENABLED | Tracks source mutations | 0 unless sync is enabled | Required before source publication |
| VAULT_ALLOWED_S3_ENDPOINTS | Exact permitted origins | Approved HTTPS origins only | Prevents endpoint substitution |
| VAULT_CREDENTIAL_ALIASES | Server-side credential alias map | Approved aliases only | Values name secret references, not secret contents |
| VAULT_BLOCK_PRIVATE_S3_ENDPOINTS | Reject private endpoints | 1 | Protects against SSRF-like endpoint misuse |
| VAULT_ALLOW_HTTP_S3_ENDPOINTS | Permit HTTP object store | 0 | Local development can use explicit local HTTP only |
| VAULT_INVENTORY_CACHE_SECONDS | Inventory cache duration | 30 | Affects freshness of operator views |
| VAULT_MAX_MANIFEST_BYTES | Manifest size bound | 8388608 | Reject oversized untrusted manifests |
| VAULT_MAX_MANIFEST_OBJECTS | Manifest object bound | 100000 | Reject incomplete or abusive inventories |
| VAULT_MAX_GENERATION_BYTES | Generation size bound | 536870912000 | Must fit available quarantine capacity |
| VAULT_RESTORE_REQUIRE_SANITIZATION | Require sanitization | 1 | Exact production-derived stage data needs explicit policy |
| VAULT_RESTORE_ALLOW_REPACKED_RELEASE_MISMATCH | Staging-only repack exception | 0 | Provenance and structural checks still apply |
| VAULT_RESTORE_MIN_FREE_BYTES | Free-space floor | 0 in examples | Set a measured floor before high-volume restore |
| VAULT_RESTORE_MIN_FREE_INODES | Free-inode floor | 0 in examples | Set a measured floor before high-file-count restore |
| VAULT_UI_PROFILE_CONFIGURATION_ENABLED | Allow profile config in UI | 0 production, 1 local/stage control plane | Configuration must remain auditable |
| VAULT_UI_SECRET_ENTRY_ENABLED | Allow secret entry in UI | 0 | Use the secret provider |
| VAULT_PROFILE_ENCRYPTION_KEY | Encrypt UI fallback data | Secret only | Never place a real key in a file |
| LOCAL_INDEX_MAINTENANCE_ENABLED | Local index jobs | 0 by default | Enable only on the approved worker |
| FORCE_REINDEX_ENABLED | Permit forced reindex | 0 | One-time budgeted operation only |
| MAINTENANCE_CANDIDATE_PREPARATION_ENABLED | Build candidate generation | 0 | Does not activate a candidate |
| MAINTENANCE_CANDIDATE_WRITER_MODE | Authorize candidate writer | 0 | Exactly one fenced writer |

## 9. Atomic activation

| Variable | Purpose | Reviewed posture | Required evidence |
| --- | --- | --- | --- |
| STAGING_INITIAL_ACTIVATION_ENABLED | Permit first activation | 0 | Signed intent, generation, manifest digest, compatibility, smoke tests |
| STAGING_RUNTIME_ACTIVATION_ENABLED | Permit runtime pointer change | 0 | Same evidence plus recovery path |
| STAGING_ACTIVATION_APPLY_MODE | auto or pending application | pending for templates | pending keeps an operator in the loop |
| ACTIVATION_INTENT_SIGNING_KEY | Signs activation intent | Secret provider only | Missing or invalid signatures must leave the current pointer unchanged |
| ACTIVATION_SMOKE_QUERIES_FILE | Representative checks | /app/data-control/config/activation-smoke-queries.json | Include English and Marathi search, listing, source link, and PDF access |
| ACTIVATION_RECOVERY_SUPERADMIN_USERNAME | Recovery account | Secret/config provider | Must be separately authorized |
| ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD | Recovery password | Secret provider only | Never document or log the value |
| ACTIVATION_SUPERVISOR_POLL_SECONDS | Supervisor cadence | 2 | Does not weaken the gates |
| ACTIVATION_READINESS_TIMEOUT_SECONDS | Activation readiness bound | 120 | Timeout leaves the prior pointer active |

## 10. Data Operations and RustFS profiles

| Variable | Purpose | Current reviewed posture | Notes |
| --- | --- | --- | --- |
| DATAOPS_ENABLED | Enables profile-based Data Operations | 1 in reviewed deployment templates | Startup still validates credentials and permissions |
| DATAOPS_PROFILE_MANIFEST | Structured profile definitions | v2 source plus stage_2026 | Contains bucket/dataset/namespace identity, never secret values |
| DATAOPS_ENV_PROFILES | Enabled profile names | production_v2_source,stage_2026 | Keep names stable for receipts and automation |
| DATAOPS_BACKUP_PROFILE | Backup destination profile | stage_2026 | First backup remains manual until receipt verification |
| DATAOPS_RESTORE_PROFILE | Restore profile | stage_2026 for stage | Explicit operation still selects a generation |
| DATAOPS_BACKUP_SOURCE_PROFILE | Optional source override | blank | Set only for an approved cross-profile operation |
| DATAOPS_BACKUP_DESTINATION_PROFILE | Optional destination override | blank | Set only for an approved operation |
| DATAOPS_RESTORE_SOURCE_PROFILE | Optional restore source override | blank | Must agree with the operation request |
| DATAOPS_RESTORE_DESTINATION_PROFILE | Optional restore destination override | blank | Must be a separate quarantine target for rehearsal |
| DATAOPS_BACKUP_MODE | Manual or scheduled backup | manual | Change only after a successful first receipt |
| DATAOPS_BACKUP_INTERVAL_SECONDS | Scheduled backup interval | 900 | Bounded cadence, not a durability guarantee |
| DATAOPS_BACKUP_QUIET_PERIOD_SECONDS | Stable source quiet period | 120 | Mutation during scan invalidates publication |
| DATAOPS_BACKUP_MAX_LAG_SECONDS | Backup freshness bound | 3600 | Readiness can degrade when stale |
| DATAOPS_AUTO_HEAL_ENABLED | Bounded recovery worker | 1 stage, 0 local | Does not authorize activation |
| DATAOPS_AUTO_HEAL_INTERVAL_SECONDS | Auto-heal cadence | 60 | Keep bounded |
| DATAOPS_AUTO_HEAL_REINDEX_PER_RUN | Per-run reindex budget | 500 | Protects CPU and provider spend |
| DATAOPS_AUTO_HEAL_REINDEX_PER_DAY | Daily reindex budget | 5000 | Reset is evidence-tracked |
| DATAOPS_AUTO_HEAL_STALE_SECONDS | Stale operation threshold | 90 | Enables retry after worker failure |
| DATAOPS_AUTO_HEAL_MAX_RETRIES | Retry count | 3 | Permanent mismatch must fail closed |
| DATAOPS_OPERATION_LEASE_SECONDS | Operation lease | 3600 | Prevents competing mutations |
| DATAOPS_RESTORE_AUTO_ACTIVATE_STAGING | Automatic activation after restore | 0 | This must remain 0 until explicitly redesigned and approved |
| DATAOPS_RESTORE_REQUIRE_PRODUCTION_CONFIRMATION | Confirmation for production-derived restore | 1 | Keeps custody boundary explicit |
| DATAOPS_CLONE_REBIND_ENABLED | Explicit cross-dataset clone/rebind | 0 by default | Requires source generation, destination profile, confirmation, collision and digest checks |
| DATAOPS_UI_CONFIG_ENABLED | Show Data Operations configuration UI | 1 in operator control plane | UI is not a secret store |
| DATAOPS_UI_SECRET_ENTRY_ENABLED | Permit UI secret entry | 0 | Use the secret provider |
| DATAOPS_MIRROR_QUARANTINE_RETENTION_DAYS | Retain guarded mirror deletions | 30 | Cleanup is bounded and recoverable; it is not RustFS retention |
| DATAOPS_MIRROR_QUARANTINE_CLEANUP_MAX_OBJECTS | Per-pass mirror cleanup bound | 100 | Prevents a single maintenance pass from deleting a large set |

The restore quarantine is intentionally derived as
`DATA_ROOT/restore-quarantine`; it is not configurable through an environment
variable. This keeps verified candidates on the shared data volume visible to
both the maintenance worker and activation supervisor.

### Canonical profile identities

| Profile | Bucket | Dataset | Role | Use |
| --- | --- | --- | --- | --- |
| production_v2_source | ai-sahakar-prod-flowdocs-artifact-vault-v2 | ai-sahakar-prod-v2 | restore | Immutable verified legacy-production source generation |
| stage_2026 | ai-sahakar-stage-2026-flowdocs-artifact-vault | ai-sahakar-stage-2026 | both | Stage clone, stage recovery points, and round-trip backups |

The profile credential reference may point to the existing production secret
reference as approved, but the application must not receive RustFS root
credentials. Provisioning and permission tests are operator-only.

### Profile variable expansion

The compatibility form DATAOPS_PROFILE_<NAME>_* uses an uppercase normalized
profile name. Prefer DATAOPS_PROFILE_MANIFEST because it keeps the complete
identity in one auditable value. If the compatibility form is used, verify all
of these fields before enabling a profile:

| Field | Example for production_v2_source | Example for stage_2026 |
| --- | --- | --- |
| PROVIDER | rustfs | rustfs |
| ENDPOINT | approved HTTPS RustFS origin | approved HTTPS RustFS origin |
| BUCKET | v2 source bucket | stage bucket |
| REGION | us-east-1 | us-east-1 |
| DATASET_ID | ai-sahakar-prod-v2 | ai-sahakar-stage-2026 |
| SOURCE_ID | ai-sahakar-prod-v2 | stage-2026 |
| NAMESPACE | production-v2 | stage-2026 |
| CREDENTIAL_REF | DATAOPS_PRODUCTION | DATAOPS_PRODUCTION |

## 11. Public authentication exception

| Variable | Safe value | Meaning |
| --- | --- | --- |
| STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED | 1 | Stage exact-production authentication data needs an exception |
| STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED | 0 | No public exposure until a security owner signs off |
| STAGE_PUBLIC_AUTH_EXCEPTION_OWNER | blank | Named owner is mandatory for approval |
| STAGE_PUBLIC_AUTH_EXCEPTION_MONITORING | blank | Monitoring and alerting plan is mandatory |
| STAGE_PUBLIC_AUTH_EXCEPTION_INCIDENT_RESPONSE | blank | Incident response procedure is mandatory |
| STAGE_PUBLIC_AUTH_EXCEPTION_ROLLBACK_AUTHORITY | blank | Named rollback authority is mandatory |

PUBLIC_SEARCH_ENABLED=1 does not change this gate. It enables anonymous
document search only. Production password hashes, user accounts, admin routes,
and indefinite public exposure remain blocked without written approval.

## 12. Optional administration and email

| Variable | Purpose | Development | Stage/production |
| --- | --- | --- | --- |
| CREATE_SUPERUSER | One-time bootstrap account creation | 0 after local setup | 0 after initialization |
| DJANGO_SUPERUSER_USERNAME | Bootstrap username | Secret/local only | Secret provider only |
| DJANGO_SUPERUSER_EMAIL | Bootstrap email | Local only | Approved address |
| DJANGO_SUPERUSER_PASSWORD | Bootstrap password | Local secret | Secret provider only |
| EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS | SMTP transport | Usually unset | Approved provider settings |
| EMAIL_HOST_USER, EMAIL_HOST_PASSWORD | SMTP credentials | Unset or local secret | Secret provider only |

Changing bootstrap variables does not change an existing user. Use the
application's password-management flow and record the operational decision.

PDFSEARCH_TEST_EMBEDDINGS is reserved for disposable CI/test runs. It must
remain 0 in stage and production; it is not a substitute for the approved
embedding provider or a recovery validation.

`EXTERNAL_AI_MODE=sandbox` follows the same boundary. It is valid only for
development/test. A stage-like disposable lifecycle certification must carry
both `CI=true` and `PDFSEARCH_TEST_EMBEDDINGS=1`; an ordinary reachable stage,
review, or production process fails closed instead of returning plausible fake
answers or vectors.

## 13. Safe change recipes

| Change | Update together | Verification | Rollback |
| --- | --- | --- | --- |
| Add a new local OCR language | PDF_OCR_LANGUAGES, image traineddata, OCR tests | Representative PDFs, OCR evidence, index ratio | Restore prior image/config and reprocess affected documents |
| Change embedding model | OPENAI_EMBED_MODEL, release identity, index policy | Reindex budget, vector dimension/model evidence, search smoke tests | Keep prior generation and index pointer |
| Enable stage backup | DATAOPS_BACKUP_PROFILE, mode, credentials, permissions | First receipt, manifest digest, object count, profile fields | Return to manual and retain failed target |
| Restore a stage generation | Restore source/destination selectors, quarantine root | Checksums, migrations, counts, search, readiness | Do not touch active volumes; discard only after evidence review |
| Activate a generation | Activation flags, signed intent, smoke query file | Signed pointer and exact manifest digest | Leave previous pointer unchanged |
| Change public-search posture | PUBLIC_SEARCH_ENABLED and allowlist/rate limits | Route response, authentication boundary, security review | Revert flag and verify route |
| Change external provider policy | EXTERNAL_AI_MODE, API secret, model, side-effect mode | Provider call policy and redaction checks | Disable provider; local OCR remains available |

## Verification commands

Use the effective runtime, not only the template, for verification. These
commands are examples and must not print secrets:

    python manage.py config_inspect
    python manage.py verify_object_store_capabilities
    python manage.py validate_data_release
    python -m unittest flowdocs.core.test_environment_policy
    python scripts/ci/docs_contract.py

For a deployment, record:

| Evidence | Why it matters |
| --- | --- |
| Image repository and immutable digest | Proves code identity |
| Web and maintenance effective lifecycle keys | Prevents split-brain worker posture |
| Data and control volume identities | Proves paired custody |
| Dataset, source, profile, and generation IDs | Proves boundary and lineage |
| SQLite integrity, migration, media, and PDF counts | Proves artifact compatibility |
| OCR language/budget and indexing ratio | Proves document intelligence readiness |
| Backup/restore receipt and manifest digest | Proves recovery behavior |
| Signed active pointer | Proves atomic activation |
| Public auth exception fields | Proves security gate remains explicit |

Never include API keys, passwords, signing keys, object-store secrets, document
contents, or raw authentication data in evidence or documentation.

## References

- docs/ENVIRONMENT_CONTRACT.md — startup validation and required identity rules
- docs/ENVIRONMENT_CONFIGURATION_GUIDE.md — impact matrix and operational caveats
- docs/dataops/ENV_CONTRACT.md — Data Operations profile and operation contract
- docs/dataops/ROLLOUT.md — staged profile rollout and receipt gates
- docs/STATUS-2026-08-03.md — current activation, stage search, backup, recovery, and readiness evidence
- docs/STATUS-2026-08-02.md — historical migration, clone, OCR, and quarantine evidence
- docs/OPERATIONS_RUNBOOK.md — backup, restore, activation, and incident procedures
- docs/LEGACY_VS_CURRENT_STATE.md — old-versus-current documentation contract
- .env.example — local copyable reference
- .env.dataops.example — canonical Data Operations template
