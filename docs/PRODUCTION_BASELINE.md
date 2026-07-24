Status: Active
Audience: Operator, Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-24
Canonical source: docs/PRODUCTION_BASELINE.md
Supersedes: None

# Production Baseline

This is the verified documentation baseline. It records boundaries and counts,
not PDF contents or secrets.

## Runtime

- Canonical domains: `https://ai-sahakar.net` and `https://www.ai-sahakar.net`.
- Preview domain: `https://2026.ai-sahakar.net` (historical verification host).
- Merged source: `2e1ca38`.
- Current release: Redis-enabled immutable image revision from PR #53.
- Release identity: exact web and Redis image digests recorded in Dokploy.
- Effective deployment gate: exact digests and `pull_policy: always`.
- Deployment provenance gate: GitHub branch SHA, Dokploy checkout SHA, OCI
  revision/digest, Compose hash, Dokploy deployment ID, and data generation must
  be recorded together.
- Health gates: `/livez`, `/readyz`, `/health/data/`, `/health/lease/`,
  `/health/metrics/`; the endpoints do not validate PDF or FAISS contents.
- Management commands: `config_inspect`, `sync_active_generation`,
  `probe_capabilities` (via shell), `get_authoritative_pointer` (via shell).

## Data Baseline

- Legacy custody: 242 PDFs and 45 FAISS files.
- Active custody after reconciliation: 253 PDF rows, 242 PDF files, 53 folders,
  8 users, and 51 rebuilt FAISS indexes with 8,753 vectors.
- Preserved unrecovered target-only rows: 11.
- Database state: source and target were reconciled through isolated staging;
  the legacy source remains preserved separately.

These facts mean legacy and active data are not interchangeable. Direct copying,
silent merging, and assuming path overlap means content equivalence are
prohibited.

## Recovery Boundary

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped active
and legacy snapshots plus checksums. RustFS is isolated from the application
network. Application-level S3 integration is opt-in and explicit. The bucket is
an operator recovery vault, not runtime storage or automatic cross-environment
synchronization.

## Current Versus Planned

Current: immutable application release, Redis runtime dependency, operator-held
snapshots/checksums, manual staged restore and promotion, explicit artifact
inventory/RustFS custody, environment identity (`environment.py`), side-effect
guards (`side_effects.py`), AI call guarding (`ai_guard.py`), activation
journal (`activation_journal.py`), restore pipeline (`restore_pipeline.py`),
restore workspace (`restore_workspace.py`), global writer fencing
(`global_writer.py`), dataset registration (`registration.py`), backup policy
(`backup_policy.py`), data sanitization (`sanitize.py`), migration rehearsal
(`rehearsal.py`), writer lease (`lease.py`), FAISS compatibility
(`compatibility.py`), Prometheus metrics (`metrics.py`), scoped namespace
(`namespace.py`), and object-store capability probing
(`object_store_capabilities.py`).

Planned or absent: automatic cross-environment sync, release pointers,
retention automation, and FAISS recovery orchestration. Restore now builds the
canonical database chunks/embeddings/index pipeline and fails atomically if it
cannot produce a searchable result set.

## Runtime Facts

- `APP_ENV` controls environment identity (production/staging/development).
- `PRODUCTION_SOURCE_ID` and `AUTHORITATIVE_DATASET_ID` identify the canonical
  dataset for publication.
- `DATASET_ID` scopes operations to a specific registered dataset.
- `BACKUP_ROLE` gates backup write capability (writer/reader/none).
- `EXTERNAL_SIDE_EFFECTS_MODE` controls email, AI, and other external calls
  (enabled/disabled/dry_run).
- `DATA_MODE` selects data access mode (read_write/read_only).

## Data Boundaries

- `/app/flowdocs`: immutable application code (never shadow with a volume).
- `/app/data`: persistent data root (SQLite, media, FAISS, Chroma).
- `/app/staticfiles`: collected static assets.
- `/app/backups`: operator backup target.
- RustFS bucket `ai-sahakar-prod-flowdocs-data-volume`: recovery vault only.

## Verification Gates

Record the result of link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, `/health/data/`, `/health/lease/`, `/health/metrics/`,
PDF count, FAISS count, and representative search for each release or staged
recovery. Include source SHA, exact image digests, volume identity,
snapshot/checksum references, and the explicit promotion decision.
