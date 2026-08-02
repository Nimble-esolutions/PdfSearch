Status: Active
Audience: Operator, Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-26
Canonical source: docs/PRODUCTION_BASELINE.md
Supersedes: None

# Production Baseline
## Current-state pointer

For the verified state as of 2026-08-02, use [STATUS-2026-08-02.md](STATUS-2026-08-02.md).
Earlier dated sections in this page remain historical evidence and must not
be used as current deployment state without reconciling them to that record.


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
snapshots/checksums, explicit artifact inventory/RustFS custody, environment
identity (`environment.py`), side-effect
guards (`side_effects.py`), AI call guarding (`ai_guard.py`), activation
journal (`activation_journal.py`), restore pipeline (`restore_pipeline.py`),
restore workspace (`restore_workspace.py`), global writer fencing
(`global_writer.py`), dataset registration (`registration.py`), backup policy
(`backup_policy.py`), data sanitization (`sanitize.py`), migration rehearsal
(`rehearsal.py`), writer lease (`lease.py`), FAISS compatibility
(`compatibility.py`), Prometheus metrics (`metrics.py`), scoped namespace
(`namespace.py`), and object-store capability probing
(`object_store_capabilities.py`).

Planned or absent: one operator/startup path connected to the full restore
pipeline, automatic cross-environment sync, production-ready scheduled
publication, retention automation, and FAISS recovery orchestration. The
library-level restore pipeline passes isolated integration tests, but current
admin restore/promotion does not activate runtime bytes.

## Runtime Facts

- `APP_ENV` controls environment identity (production/staging/development).
- `PRODUCTION_SOURCE_ID` and `AUTHORITATIVE_DATASET_ID` identify the canonical
  dataset for publication.
- `DATASET_ID` scopes operations to a specific registered dataset.
- `BACKUP_ROLE` gates backup write capability (`writer`, `reader`, `disabled`).
- `EXTERNAL_SIDE_EFFECTS_MODE` controls email, AI, and other external calls
  (`enabled`, `disabled`, `sandbox`).
- `DATA_MODE` selects data posture (`empty`, `seed`, `local`, `s3-restore`,
  `s3-pinned`, `sanitized-production`, `exact-production`).

## Data Boundaries

- `/app/flowdocs`: immutable application code (never shadow with a volume).
- `/app/data`: persistent data root (SQLite, media, FAISS, Chroma).
- `/app/data/staticfiles`: collected static assets.
- `/app/data/backups`: same-volume local snapshots and restore workspaces; not
  an off-host backup.
- RustFS bucket `ai-sahakar-prod-flowdocs-data-volume`: recovery vault only.

## Verification Gates

Record the result of link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, `/health/data/`, `/health/lease/`, `/health/metrics/`,
PDF count, FAISS count, and representative search for each release or staged
recovery. Include source SHA, exact image digests, volume identity,
snapshot/checksum references, and the explicit promotion decision.
