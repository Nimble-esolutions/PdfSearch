Status: Active
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/DATA_CUSTODY_AND_PROMOTION.md
Supersedes: None

# Data Custody and Promotion

## Boundary

The active application volume and the legacy volume are separate custody
domains. Legacy contains 242 PDFs and 45 FAISS files. Active contains 17 PDF
rows, 0 PDFs, and 11 FAISS files. Only 6 PDF paths overlap, and the databases
diverge. No PDF contents belong in an incident record.

The read-only `/mnt/legacy` mount is evidence/quarantine only. It is not a
permission to import or merge. RustFS is an isolated recovery vault; the
application supports explicit superadmin generation sync and staged pull, but
never overwrites active data during either operation.

## Required Lifecycle

1. **Quarantine:** preserve active and legacy sources unchanged; use separate
   read-only restore targets and record volume/snapshot identities.
2. **Inventory:** count database rows, PDF paths, FAISS files, and checksums
   without copying document contents.
3. **Conflict classification:** classify missing files, duplicate paths,
   divergent rows, orphan indexes, and uncertain matches. A path overlap is not
   proof of equivalent content.
4. **Staged restore:** construct a uniquely named disposable target from an
   explicit selection. Never write the active volume during analysis.
5. **FAISS fingerprint validation:** record file-level fingerprints and validate
   index load, expected dimensions/model metadata when available, and search
   behavior. See [`FAISS_COMPATIBILITY.md`](FAISS_COMPATIBILITY.md).
6. **Explicit promotion:** an operator records the selected source, conflict
   decisions, image/data compatibility, and approval before reconnecting or
   replacing active data.

Legacy and active data must not be copied directly. `IMPORT_LEGACY_DATA` does
not replace this lifecycle and must not be treated as automatic promotion.

## RustFS Handling

Use the timestamped snapshots and checksums in bucket
`ai-sahakar-prod-flowdocs-data-volume` as recovery evidence. Because the bucket
is isolated from the application network, recovery is an operator-mediated
restore, not a runtime read or automatic sync.

## Gates

Before promotion, pass link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, PDF count, FAISS count, and representative search. Retain
failed isolated targets and evidence until the recovery decision is closed.

## Current Versus Planned

Current: manual custody, inventory, conflict classification, staged restore,
fingerprint checks, and explicit promotion.

Planned: generated artifact manifests, automatic reconciliation, automatic
cross-environment sync, and automated FAISS recovery.
