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

- Domain: `https://2026.ai-sahakar.net` (healthy at verification).
- Merged source: `f05e110`.
- Current release: Redis-enabled immutable image revision from PR #24.
- Release identity: exact web and Redis image digests recorded in Dokploy.
- Effective deployment gate: exact digests and `pull_policy: always`.
- Health gates: `/livez` and `/readyz`; the endpoints do not validate PDF or
  FAISS contents.

## Data Baseline

- Legacy custody: 242 PDFs and 45 FAISS files.
- Active custody: 17 PDF database rows, 0 PDF files, and 11 FAISS files.
- Path overlap: 6 PDF paths.
- Database state: active and legacy SQLite databases diverge.

These facts mean legacy and active data are not interchangeable. Direct copying,
silent merging, and assuming path overlap means content equivalence are
prohibited.

## Recovery Boundary

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped active
and legacy snapshots plus checksums. RustFS is isolated from the application
network. Application-level S3 integration is **not implemented**. The bucket is
an operator recovery vault, not runtime storage and not automatic
cross-environment synchronization.

## Current Versus Planned

Current: immutable application release, Redis runtime dependency, operator-held
snapshots/checksums, and manual staged restore and promotion.

Planned or absent: S3 application integration, automatic cross-environment sync,
generated artifact manifests, and FAISS recovery automation.

## Verification Gates

Record the result of link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, PDF count, FAISS count, and representative search for each
release or staged recovery. Include source SHA, exact image digests, volume
identity, snapshot/checksum references, and the explicit promotion decision.
