Status: Active
Audience: Operator, Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/PRODUCTION_BASELINE.md
Supersedes: None

# Production Baseline

This is the verified documentation baseline. It records boundaries and counts,
not PDF contents or secrets.

## Runtime

- Canonical domains: `https://ai-sahakar.net` and `https://www.ai-sahakar.net`.
- Preview domain: `https://2026.ai-sahakar.net` (historical verification host).
- Merged source: `f05e110`.
- Current release: Redis-enabled immutable image revision from PR #24.
- Release identity: exact web and Redis image digests recorded in Dokploy.
- Effective deployment gate: exact digests and `pull_policy: always`.
- Deployment provenance gate: GitHub branch SHA, Dokploy checkout SHA, OCI
  revision/digest, Compose hash, Dokploy deployment ID, and data generation must
  be recorded together.
- Health gates: `/livez` and `/readyz`; the endpoints do not validate PDF or
  FAISS contents.

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
snapshots/checksums, manual staged restore and promotion, and explicit artifact
inventory/RustFS custody.

Planned or absent: automatic cross-environment sync, release pointers, restore,
retention automation, and FAISS recovery orchestration.

## Verification Gates

Record the result of link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, PDF count, FAISS count, and representative search for each
release or staged recovery. Include source SHA, exact image digests, volume
identity, snapshot/checksum references, and the explicit promotion decision.
