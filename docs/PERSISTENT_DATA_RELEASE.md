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
integration is opt-in and explicit; it does not replace Django storage or
participate in startup synchronization.

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

The current implementation now emits an opt-in read-only inventory manifest, but
there is still no automatic active-release pointer. For every production release,
the operator must retain a record containing:

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

## Inventory And Artifact Manifest

`core.inventory_artifacts` records a deterministic nested inventory containing
`manifest_version`, `read_only`, SQLite schema/migrations, PDF rows and storage
files, FAISS files, and embedding metadata. The vault normalizes that inventory
to this immutable object contract:

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

The inventory command is read-only:

```bash
python flowdocs/manage.py inventory_artifacts \
  --data-root /app/data \
  --output /app/data/backups/inventory.json
python flowdocs/manage.py inventory_artifacts \
  --compare source.json target.json \
  --output comparison.json
```

The inventory includes SQLite schema/migrations, PDF database rows and file
hashes, FAISS file hashes, and embedding dimensions. It does not include PDF
contents. Treat titles and other row metadata as restricted operational data.

## Artifact Vault Status

**Current, opt-in:** `flowdocs.core.artifact_vault` provides a disabled-by-default
S3-compatible adapter for immutable PDF, generation-bound FAISS, and manifest
objects. It does not replace Django file storage or participate in startup. Set
all `ARTIFACT_VAULT_*` variables explicitly, then run the explicit
`upload_artifact_vault` management command with an existing manifest and selected
`MANIFEST_PATH=LOCAL_PATH` artifacts. The `pdfsearch-artifact-inventory/v1`
manifest shape is accepted directly: its nested PDF, FAISS, and embedding metadata
lists are normalized to the vault's `files` contract. Because inventory manifests
do not normally contain a release id, pass `--release-id <immutable-name>`; the
command rejects missing, mutable-alias, or unsafe ids rather than inferring one.
Uploads fail closed on missing configuration, provider errors, missing checksum
metadata, or checksum/size mismatches.

The adapter accepts duplicate PDF paths when their size and SHA-256 match, since
content-addressed storage safely deduplicates identical bytes. Conflicting
FAISS or metadata keys remain rejected.

**Planned:** automated startup synchronization, release promotion, restore, and
retention workflows are not implemented by this adapter.

## Reconciliation Record: 2026-07-22

The legacy source volume was reconciled into an isolated staging volume before
promotion. The live active volume was never used as the merge workspace.

- Source database: 242 PDF rows, 46 folders, 7 users, schema through `core:0011`.
- Target database: 17 PDF rows, 11 folders, 3 users, schema through `core:0012`.
- Merged database: 253 PDF rows, 53 folders, 8 users, schema through `core:0012`.
- Recovered PDF files: 242.
- Preserved but unrecovered target-only PDF rows: 11.
- Rebuilt FAISS indexes: 51, dimension 1536, 8,753 vectors.
- SQLite integrity and foreign-key checks: passed.
- Production readiness after promotion: database, cache, and migrations `ok`.
- RustFS generation: `reconciled-20260722t0618`; 293 objects and 654,802,243
  bytes verified through the S3-compatible adapter.

The merge preserved target rows, remapped source users/folders by explicit
natural-key rules, allocated fresh PDF IDs for source-only rows, and retained
source/target/RustFS rollback artifacts. Never repeat this as a direct database
or directory copy.

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
9. Bind the application host port to loopback only; public traffic must enter
   through Traefik on ports 80/443.
10. Verify `ss -lntp` or equivalent after recreation and confirm `8000` is
    `127.0.0.1:8000`, not `0.0.0.0:8000` or `[::]:8000`.

## Rollback Rules

Rollback must select a compatible application image and data release together.
Never restore only the image when a migration or embedding/index format changed.

## Retention

Garbage collection must protect:

- active release;
- previous known-good release;
- all releases referenced by backup records;
- releases under incident investigation.
