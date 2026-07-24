Status: Active
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-24
Canonical source: docs/RUSTFS_RECOVERY_VAULT.md
Supersedes: None

# RustFS Recovery Vault

## Current Boundary

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped
active and legacy snapshots and checksums. Runtime access is gated through
`object_store_capabilities.py` (capability probe), `registration.py` (dataset
registration), `global_writer.py` (CAS writer fencing), and `namespace.py`
(scoped key validation). Access must be enabled only with immutable generation
manifests.

The bucket is an operator recovery vault with opt-in application-level S3
integration. It is not mounted by the application, is not a runtime source of
PDFs or indexes, and does not provide automatic cross-environment
synchronization.

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

## Object Store Capabilities

`object_store_capabilities.py` probes the RustFS/S3 endpoint for CAS support
(If-None-Match, If-Match), bucket existence, and writeability. The capability
probe is a pre-flight gate before any publication or restore operation. A
missing or degraded capability blocks authoritative writes.

## Namespace

`namespace.py` enforces scoped key validation for all RustFS operations. Keys
are validated against the `datasets/{id}/control/`, `datasets/{id}/generations/`,
and `datasets/{id}/blobs/` patterns in addition to legacy flat prefixes
(`pdfs/`, `faiss/`, `databases/`, `metadata/`, `manifests/`). Unrecognized key
patterns are rejected.

## Registration

`registration.py` manages dataset identity and authoritative pointer lifecycle.
A dataset must be registered before any generation can be published. The
authoritative pointer is a CAS-guarded JSON blob that references the current
active generation manifest.

## Global Writer

`global_writer.py` implements CAS-based writer fencing. Only one writer
instance may hold the global writer lease at a time. Writer handover requires
an explicit epoch increment and takeover ceremony. The writer record is stored
as a CAS-guarded object in the vault.

## Current Versus Planned

Current: timestamped snapshots and checksums are available for operator-led
recovery. Object-store capability probing (`object_store_capabilities.py`),
scoped namespace validation (`namespace.py`), dataset registration
(`registration.py`), and CAS writer fencing (`global_writer.py`) are
implemented.

The application now exposes generation and maintenance contracts for UI-driven
operations. Runtime restore and promotion remain fail-closed until a worker
stages a manifest, verifies every checksum, and records an explicit promotion
event. No operation may overwrite the active data root directly.

## Gates

The restore record must include link/path scan, Mermaid validation, Compose
config, `/livez`, `/readyz`, PDF count, FAISS count, representative search,
source SHA, exact image digests, volume identity, and checksum references.
