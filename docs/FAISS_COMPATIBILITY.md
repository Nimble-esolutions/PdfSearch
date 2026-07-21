Status: Active
Audience: Recovery, Release
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/FAISS_COMPATIBILITY.md
Supersedes: None

# FAISS Compatibility

## Current Evidence

The verified custody baseline is 45 legacy FAISS files and 11 active FAISS
files. The counts do not establish compatibility. Active and legacy databases
also diverge, so an index must not be promoted merely because its filename or
path exists in both sources.

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

## Current Versus Planned

Current: operators can perform file fingerprints, load checks, metadata review,
and representative search in an isolated target.

Planned or absent: generated index manifests, automatic FAISS compatibility
classification, and automated FAISS recovery/promotion.

## Required Gates

Pass link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
PDF count, FAISS count, and representative search. Record source SHA, exact
image digest, data snapshot/checksum references, and the promotion decision.
