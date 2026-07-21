Status: Active
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/RUSTFS_RECOVERY_VAULT.md
Supersedes: None

# RustFS Recovery Vault

## Current Boundary

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped
active and legacy snapshots and checksums. RustFS is currently isolated from the
FlowDocs application network. Application-level S3 integration is **not
implemented**.

The bucket is an operator recovery vault only. It is not mounted by the
application, is not a runtime source of PDFs or indexes, and does not provide
automatic cross-environment synchronization.

## Recovery Use

1. Identify the exact timestamped snapshot and checksum set without exposing
   credentials or document contents.
2. Verify checksums before extraction.
3. Extract active and legacy snapshots into separate uniquely named quarantine
   targets.
4. Inventory and classify conflicts; never copy one custody domain directly
   over the other.
5. Stage a selected restore, validate SQLite, PDF count, FAISS fingerprints,
   FAISS load, and representative search.
6. Promote only after an explicit operator decision and release record.

Do not delete or rewrite vault snapshots while an incident, restore drill, or
promotion decision references them.

## Current Versus Planned

Current: timestamped snapshots and checksums are available for operator-led
recovery.

Planned: application S3 integration, runtime bucket access, automatic
cross-environment sync, generated artifact manifests, and automated promotion.

## Gates

The restore record must include link/path scan, Mermaid validation, Compose
config, `/livez`, `/readyz`, PDF count, FAISS count, representative search,
source SHA, exact image digests, volume identity, and checksum references.
