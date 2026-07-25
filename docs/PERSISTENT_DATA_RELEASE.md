Status: Active
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-24
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

- Canonical production domains: `https://ai-sahakar.net` and
  `https://www.ai-sahakar.net` (pending DNS/Traefik cutover).
- Preview domain: `https://2026.ai-sahakar.net` was used for verified preview
  and remains a historical rollback reference, not the canonical production URL.
- Merged source: `2e1ca38`.
- Production release: Redis-enabled immutable image revision from PR #53.
- Legacy custody: 242 PDFs and 45 FAISS files.
- Active custody after reconciliation: 253 PDF rows, 242 PDF files, 53 folders,
  8 users, and 51 rebuilt FAISS indexes with 8,753 vectors.
- Preserved unrecovered target-only rows: 11.

Legacy and active data must never be copied directly. A valid promotion is
quarantine, inventory, conflict classification, staged restore, FAISS
fingerprint validation, and explicit operator promotion.

## Current Minimum Release Record

The current implementation emits an opt-in read-only inventory manifest and
supports an atomic active-release pointer through the `activate` module. For
every production release, the operator must retain a record containing:

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
`manifest_version`, `read_only`, SQLite schema/migrations and SHA-256, PDF rows
and storage files, FAISS files, embedding metadata, and complete configured
Chroma/static trees. A configured tree outside `DATA_ROOT` is explicitly marked
`out-of-contract` and is not silently treated as part of the release. The vault
normalizes that inventory to this immutable object contract:

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

For a release that will be validated on a fresh instance, include an explicit
count policy in the manifest. `preserved_target_only_rows` is the intentional
number of database PDF rows whose file is not present in the custody set; it is
not a permission to ignore arbitrary missing files:

```bash
python flowdocs/manage.py inventory_artifacts \
  --data-root /app/data \
  --expected-count pdf_rows=253 \
  --expected-count pdf_storage_files=242 \
   --expected-count faiss_files=51 \
   --expected-count faiss_vectors=8753 \
   --expected-count preserved_target_only_rows=11 \
  --output /app/data/backups/inventory.json
```

Validation is explicit and opt-in. It opens SQLite read-only, verifies the
database SHA-256, hashes declared files, checks the migration leaf and applied
set, validates PDF row paths and checksums, and loads FAISS indexes when the
dependency and file format permit it to compare dimensions and vector counts
against the database chunk metadata. It also compares every in-contract Chroma
and static file. A missing policy, hash mismatch, unexpected missing PDF,
unsafe path, failed SQLite check, or inconsistent FAISS metadata returns a
non-zero exit status. The command never writes under `--data-root`, and must
not be used to generate or package production data in Git, `init/`, or an image
layer:

```bash
python flowdocs/manage.py validate_data_release \
  --manifest /app/data/backups/inventory.json \
  --data-root /app/data \
  --output /app/data/backups/validation.json
```

For an existing manifest without embedded policy, pass the same values as
repeatable `--expected-count NAME=VALUE` overrides. Treat a failed validation as
a release stop; do not repair the data root from inside this command.

## Fresh Instance Gate

`start.sh` defaults to `DATA_BOOTSTRAP_MODE=strict`. When an empty data volume
is initialized from the declared image seed (`/app/init/db.sqlite3` by default),
the seed's PDF rows are checked against the configured `MEDIA_ROOT` before
Gunicorn starts. A seed with PDF rows and missing or unsafe media fails closed;
the application is never started as an apparently healthy but unsearchable
release. Set `DECLARED_SEED_DB` only to an explicitly reviewed seed path.

For a deliberately empty first run, set `DATA_BOOTSTRAP_MODE=empty` (or
`bootstrap`). That mode does not copy the image seed and is the only supported
way to start without a declared data release. It is not a repair mode for an
existing production volume.

Restore operations use Django's configured `MEDIA_ROOT` and synchronously build
text, chunks, embeddings, and the folder FAISS index. A restore is rolled back
and exits non-zero if any stage cannot complete. Search likewise rejects stale
FAISS dimensions/counts or incomplete database chunk metadata instead of
returning arbitrary zero-score references.

The inventory includes SQLite schema/migrations, PDF database rows and file
hashes, FAISS file hashes, and embedding dimensions. It does not include PDF
contents. Treat titles and other row metadata as restricted operational data.

## Environment Identity

The `environment` module (`flowdocs/core/environment.py`) defines the runtime
environment contract. New environment variables:

- `APP_ENV` — deployment environment (development, staging, production)
- `PRODUCTION_SOURCE_ID` — canonical production source identifier
- `AUTHORITATIVE_DATASET_ID` — authoritative dataset reference
- `DATASET_ID` — current dataset identifier
- `BACKUP_ROLE` — backup role (`writer`, `reader`, `disabled`)
- `EXTERNAL_SIDE_EFFECTS_MODE` — external side-effect safety mode
- `DATA_MODE` — data posture (`empty`, `seed`, `local`, `s3-restore`,
  `s3-pinned`, `sanitized-production`, `exact-production`)

## Global Writer Fencing

The `global_writer` module provides global writer fencing to prevent concurrent
writes across instances. Only one writer may hold the active lease at any time.

## Dataset Registration

The `registration` module registers datasets with the authoritative source,
recording identity, schema version, and compatibility metadata.

## Restore Pipeline

The `restore_pipeline` and `restore_workspace` modules orchestrate isolated
restore operations. A restore workspace is a disposable staging directory;
the pipeline coordinates compatibility checks, sanitization, migration
rehearsal, and activation in sequence.

## Compatibility Checks

The `compatibility` module verifies image, schema, embedding model, and index
format compatibility before staging or promotion.

## Sanitization

The `sanitize` module removes sensitive or out-of-contract data from a staged
dataset before promotion.

## Migration Rehearsal

The `rehearsal` module performs a dry-run migration against a disposable copy
to detect schema conflicts before touching active data.

## Activation Journal

The `activation_journal` module records every activation step with an immutable
audit trail, including timestamps, operator identity, and gate results.

## Writer Lease

The `lease` module manages writer leases with TTL-based expiry, ensuring that
stale writers cannot corrupt active data after a lease expires.

## Backup Policy

The `backup_policy` module enforces backup retention, scheduling, and
verification policies.

## Object Store Capabilities

The `object_store_capabilities` module detects and verifies S3-compatible
storage capabilities, including multipart upload, versioning, and encryption
support.

## Namespace

The `namespace` module manages logical namespace isolation for datasets,
preventing cross-contamination between environments.

## Metrics

The `metrics` module collects and exposes operational metrics, including
data health, lease status, and pipeline throughput.

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

**Current boundary:** explicit superadmin sync creates an immutable generation;
the current admin pull uses a legacy flat-manifest staging path, while sync
publishes a dataset-scoped manifest. The `restore_pipeline`,
`restore_workspace`, `compatibility`, `sanitize`, `rehearsal`,
`activation_journal`, and `activate` modules provide a separately tested full
promotion pipeline, but that pipeline is not called by startup or the admin
maintenance restore job. Admin promotion currently changes generation metadata
without activating runtime bytes. Automated startup synchronization and
production-ready scheduled publication are not implemented end to end. See
[`RUSTFS_RECOVERY_VAULT.md`](RUSTFS_RECOVERY_VAULT.md) for the audited boundary.

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
7. Promote only after the operator release record is complete. The `activate`
   module performs an atomic active-release pointer switch after all gates pass.
8. Retain the previous known-good release and both custody sources.
9. Bind the application host port to loopback only; public traffic must enter
   through Traefik on ports 80/443.
10. Verify `ss -lntp` or equivalent after recreation and confirm `8000` is
    `127.0.0.1:8000`, not `0.0.0.0:8000` or `[::]:8000`.

## Entrypoint And Bootstrap Boundary

The two startup scripts have different privilege boundaries and are not
duplicates:

- `docker-entrypoint.sh` runs as root, creates the persistent directories,
  repairs ownership/permissions, and drops to `appuser`.
- `start.sh` runs as `appuser`, validates configuration, performs explicit data
  bootstrap/migrations/static collection, and starts Gunicorn.

Do not remove or merge the scripts casually. A future consolidation should move
the application bootstrap phases into tested Python commands while preserving
the root-to-unprivileged boundary. The current runtime smoke must remain the
contract test for the actual image entrypoint.

## Rollback Rules

Rollback must select a compatible application image and data release together.
Never restore only the image when a migration or embedding/index format changed.

## Retention

Garbage collection must protect:

- active release;
- previous known-good release;
- all releases referenced by backup records;
- releases under incident investigation.
