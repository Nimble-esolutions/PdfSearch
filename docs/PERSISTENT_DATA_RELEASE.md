Status: Active
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/PERSISTENT_DATA_RELEASE.md
Supersedes: None

# Persistent Data Release Contract

## Scope

A data release is the compatible set of mutable application state used by one
application image. It includes:

- SQLite database;
- uploaded media;
- FAISS indexes;
- Chroma data;
- static files;
- backup metadata and checksums.

This contract does not make RustFS a runtime backend. The current RustFS bucket
`ai-sahakar-prod-flowdocs-data-volume` is an isolated operator recovery vault
with timestamped active/legacy snapshots and checksums. Application-level S3
integration is not implemented.

## Current Baseline

- Production domain: `https://2026.ai-sahakar.net` (healthy at last verification).
- Merged source: `f05e110`.
- Production release: Redis-enabled immutable image revision from PR #24.
- Legacy custody: 242 PDFs and 45 FAISS files.
- Active custody: 17 PDF rows, 0 PDF files, and 11 FAISS files.
- Reconciliation: 6 PDF paths overlap; active and legacy SQLite databases diverge.

Legacy and active data must never be copied directly. A valid promotion is
quarantine, inventory, conflict classification, staged restore, FAISS
fingerprint validation, and explicit operator promotion.

## Current Minimum Release Record

The current implementation has no generated persistent-data manifest or active
release pointer. For every production release, the operator must retain a
record containing:

- Git SHA;
- tested application image digest;
- Compose file path and hash;
- actual volume identity mounted at `/app/data`;
- backup reference and timestamp;
- migration result and `/readyz` result;
- database, media, FAISS, Chroma, and representative search verification;
- previous known-good image and backup references.

The release workflow publishes image evidence, SBOM, and provenance, but it does
not create this data record.

## Planned Artifact Manifest

Future release tooling must record:

```json
{
  "release_id": "<operator-or-tool-generated-id>",
  "git_sha": "...",
  "image_digest": "sha256:...",
  "schema_version": "<when-defined>",
  "embedding_model": "<when-defined>",
  "index_format": "<when-defined>",
  "files": [{"path": "<relative-path>", "sha256": "<checksum>", "bytes": 0}]
}
```

This JSON is a proposed future artifact contract. It is not currently emitted,
complete, or validated by CI. Do not claim generated artifact manifests exist.

## Artifact Vault Status

**Current, opt-in:** `flowdocs.core.artifact_vault` provides a disabled-by-default
S3-compatible adapter for immutable PDF, generation-bound FAISS, and manifest
objects. It does not replace Django file storage or participate in startup. Set
all `ARTIFACT_VAULT_*` variables explicitly, then run the explicit
`upload_artifact_vault` management command with an existing manifest and selected
`MANIFEST_PATH=LOCAL_PATH` artifacts. Uploads fail closed on missing configuration,
provider errors, missing checksum metadata, or checksum/size mismatches.

**Planned:** automated manifest generation, release promotion, restore, and
retention workflows are not implemented by this adapter.

## Promotion Rules

1. Preserve active and legacy sources in separate quarantine targets.
2. Inventory rows, paths, index files, and checksums without direct copying.
3. Classify conflicts and select an explicit staged restore scope.
4. Verify SQLite integrity and migration state.
5. Verify media references and load FAISS/Chroma indexes.
6. Validate FAISS fingerprints and run representative search tests.
7. Promote only after the operator release record is complete. An atomic active
   release pointer is a future capability, not a current runtime behavior.
8. Retain the previous known-good release and both custody sources.

## Rollback Rules

Rollback must select a compatible application image and data release together.
Never restore only the image when a migration or embedding/index format changed.

## Retention

Garbage collection must protect:

- active release;
- previous known-good release;
- all releases referenced by backup records;
- releases under incident investigation.
