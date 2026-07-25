Status: Active
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-24
Canonical source: docs/DATA_CUSTODY_AND_PROMOTION.md
Supersedes: None

# Data Custody and Promotion

## Boundary

The active application volume and the legacy volume are separate custody
domains. Legacy contains 242 PDFs and 45 FAISS files. After the 2026-07-22
reconciliation, active custody holds 253 PDF rows, 242 PDF files, 53 folders,
8 users, and 51 rebuilt FAISS indexes with 8,753 vectors. Only 6 PDF paths
overlap between legacy and active, and the databases diverge. No PDF contents
belong in an incident record.

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
7. **Compatibility check:** verify image, schema, embedding model, and index
    format compatibility before staging (`compatibility` module).
8. **Migration rehearsal:** dry-run migration against a disposable copy to
    detect schema conflicts before touching active data (`rehearsal` module).
9. **Sanitization:** remove sensitive or out-of-contract data before promotion
    (`sanitize` module).
10. **Activation journal:** record every activation step with audit trail
    (`activation_journal` module).
11. **Atomic pointer switch:** the `activate` module performs an atomic
    active-release pointer switch after all gates pass.

Legacy and active data must not be copied directly. `IMPORT_LEGACY_DATA` does
not replace this lifecycle and must not be treated as automatic promotion.

## RustFS Handling

Use the timestamped snapshots and checksums in bucket
`ai-sahakar-prod-flowdocs-data-volume` as recovery evidence. The bucket is
isolated from the application network; recovery is an operator-mediated restore
through the `restore_pipeline` and `restore_workspace` modules, not a runtime
read or automatic sync. The `object_store_capabilities` module detects and
verifies S3-compatible storage capabilities. Explicit superadmin generation sync
creates immutable dataset-scoped generations. The current admin pull uses a
different legacy manifest namespace and is not the full restore pipeline.
Until Plan 003 reconciles that path, use controlled direct restore tooling in a
quarantine target and do not promote from the admin label alone.

## Gates

Before promotion, pass link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, PDF count, FAISS count, and representative search. Retain
failed isolated targets and evidence until the recovery decision is closed.

## Current Versus Planned

Current: manual custody, generated inventory/generation manifests, conflict
classification, compatibility, migration rehearsal, sanitization, activation
journal, atomic pointer switch, global writer fencing, dataset registration,
writer lease, object-store capabilities, namespace, and metrics. These
primitives are not yet connected to one admin/startup restore path.

Planned: immutable evidence-pack reconciliation, automatic reconciliation,
automatic cross-environment sync, and automated FAISS recovery. The restore
pipeline and activation journal provide the foundation for these; full
automation remains a future target.
