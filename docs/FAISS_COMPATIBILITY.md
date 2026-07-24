Status: Active
Audience: Recovery, Release
Owner: FlowDocs maintainers
Last verified: 2026-07-24
Canonical source: docs/FAISS_COMPATIBILITY.md
Supersedes: None

# FAISS Compatibility

## Current Evidence

The verified custody baseline is 51 active FAISS indexes with 8,753 vectors
(post-reconciliation). Active and legacy databases diverge, so an index must
not be promoted merely because its filename or path exists in both sources.

## Manual Validation

For every candidate staged restore:

1. Keep source indexes in quarantine and record relative paths, byte sizes, and
   SHA-256 fingerprints.
2. Record available index dimensions, embedding model, document/chunk mapping,
   and application/image revision. Do not invent missing metadata.
3. Load each candidate with the matching immutable application image.
4. Confirm the index dimension and model are compatible with the query path.
5. Check document references against the staged database and media inventory.
6. Run a representative search and compare only approved operational evidence,
   not PDF contents.
7. Record pass, fail, or unknown for each index before explicit promotion.

Fingerprint validation is a release gate, not a checksum-only substitute for
load and search validation. A failed or unknown candidate stays quarantined.

## Compatibility Module

`compatibility.py` validates FAISS index dimensions, vector counts, and
embedding model compatibility against the active database. It refuses to load
an index whose dimensions or vector count differ from the database embeddings.
It never substitutes zero-score results for an invalid index.

## Namespace Module

`namespace.py` enforces scoped key validation for FAISS index storage in the
artifact vault. FAISS indexes are stored under `faiss/{id}/folder_N.index` and
`datasets/{id}/generations/*` patterns. Unrecognized key patterns are rejected.

## Restore Pipeline FAISS Handling

`restore_pipeline.py` builds the canonical database chunks/embeddings/index
pipeline during restore. It fails atomically if it cannot produce a searchable
result set. Each FAISS index is validated for dimension, vector count, and
folder mapping before promotion.

## Current Versus Planned

Current: operators can perform file fingerprints, load checks, metadata review,
database chunk-count/dimension validation, and representative search in an
isolated target. The compatibility module (`compatibility.py`) enforces
dimension and vector-count validation. The namespace module (`namespace.py`)
enforces scoped key validation. The restore pipeline (`restore_pipeline.py`)
builds and validates FAISS indexes atomically.

Planned or absent: generated index manifests, automatic FAISS compatibility
classification, and automated FAISS recovery/promotion. Restore now builds the
canonical database chunks/embeddings/index pipeline and fails atomically if it
cannot produce a searchable result set.

## Required Gates

Pass link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
PDF count, FAISS count, and representative search. Record source SHA, exact
image digest, data snapshot/checksum references, and the promotion decision.
