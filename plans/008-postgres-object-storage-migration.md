# Plan 008: Migrate relational and document custody to PostgreSQL plus object storage

> **Executor instructions**: This is a future migration plan. Do not start the
> migration on a live environment without an approved maintenance window,
> verified backups, and an isolated rehearsal. Preserve the current SQLite
> volume until the cutover has passed its rollback window.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: none; Plan 006 release gates must be used
- **Category**: migration
- **Planned at**: commit `d3fc328`, 2026-07-26

## Why this matters

The current application uses SQLite as the relational source of truth and a
Docker named volume for the database, PDFs, extracted metadata, FAISS indexes,
and backups. That is workable for one writer, but it couples data availability
to one host and makes deploy, backup, concurrency, and scale harder to reason
about. The target keeps Django as the application but moves relational state to
PostgreSQL and binary/derived artifacts to versioned S3-compatible object
storage, with checksums and explicit data-release manifests.

## Current state

- `flowdocs/flowdocs/settings.py:134-139` hard-codes the default database to
  SQLite at `SQLITE_DB_PATH`.
- `flowdocs/core/models.py:18-24` stores categories as `Folder` rows with a
  JSON keyword list.
- `flowdocs/core/models.py:34-88` combines PDF metadata, filesystem file
  reference, extracted text, chunks, JSON embeddings, category fields, and
  lifecycle state in `PDFFile`.
- `flowdocs/flowdocs/settings.py:174-186` stores static and uploaded media
  under the `DATA_ROOT` volume.
- `flowdocs/flowdocs/settings.py:318-320` places FAISS, Chroma, and backups
  under local paths.
- `flowdocs/core/maintenance.py:478-567` snapshots SQLite and uploads PDFs,
  FAISS files, and a manifest to the opt-in artifact vault.
- `flowdocs/core/artifact_vault.py:19-26` already defines content-addressed
  PDF/FAISS/database keys and rejects mutable release aliases.
- `docs/DOKPLOY_DATA_PERSISTENCE.md` defines the current volume boundary and
  the required pre/post deployment evidence.

The first migration must treat current JSON embeddings and per-folder FAISS
files as derived data. Do not infer their model or dimension from a filename;
record model, dimension, chunking settings, index type, and source release in
the migration manifest. The repository contains an older Chroma helper using a
different embedding model; it must not be silently mixed with the active
`utils.py` search path.

## Target custody contract

PostgreSQL owns transactional relational state:

- users, roles, departments, and audit identity;
- categories and category hierarchy;
- documents and immutable document versions;
- object keys, checksums, sizes, MIME type, and lifecycle state;
- chunks, chunk ordering, page/section metadata, and embedding provenance;
- ingestion jobs, data generations, validations, and audit events;
- site settings and release pointers.

Object storage owns immutable blobs and rebuildable artifacts:

```text
datasets/{dataset_id}/documents/sha256/{sha256}.pdf
datasets/{dataset_id}/documents/sha256/{sha256}.text.json
datasets/{dataset_id}/generations/{generation_id}/manifest.json
datasets/{dataset_id}/generations/{generation_id}/faiss/{scope}.index
datasets/{dataset_id}/generations/{generation_id}/exports/{name}
```

Use server-side encryption, versioning or immutable retention where supported,
checksum metadata, content type, and lifecycle policies. Never use a mutable
`latest` object as the only pointer; the database or a CAS-protected manifest
pointer names the active generation.

## Migration steps

### Step 1: Freeze the contract and inventory the source

1. Add a migration record containing source Git SHA, image digest, SQLite
   migration leaf, embedding model/dimension, chunk settings, FAISS format,
   counts, and SHA-256 for every PDF and index.
2. Run `inventory_artifacts` and `validate_data_release` against a read-only
   copy. Record all missing files, orphan files, invalid JSON, inconsistent
   chunk/embedding counts, and stale indexes.
3. Quiesce writes during the final cutover only; do not start with a live
   copy-and-hope operation.

**Verify**: the inventory validates, SQLite integrity and foreign keys pass,
and every exception has an explicit disposition.

### Step 2: Provision isolated PostgreSQL and object storage

1. Create a database and role with least privilege; enable TLS, backups,
   point-in-time recovery, connection limits, and a connection pooler where
   needed.
2. Create a dedicated bucket/prefix for the dataset. Enable versioning,
   encryption, lifecycle policy, and restricted service credentials.
3. Run the existing object-store capability probe. Stop if conditional writes,
   checksums, or read-after-write behavior required by the release protocol are
   unavailable.

**Verify**: disposable credentials can create a transaction, upload/download a
   checksum-pinned PDF, read the manifest, and cannot access another dataset.

### Step 3: Introduce a migration-compatible schema

Create additive Django migrations for PostgreSQL-compatible tables. Preserve
source primary keys where safe so references and audit history remain traceable.
Separate document identity from versions:

- `Category` replaces the overloaded vocabulary of `Folder` while retaining a
  source-folder ID mapping;
- `Document` holds stable title, category relationship, access policy, and
  lifecycle;
- `DocumentVersion` holds source checksum, object key, extraction status,
  extracted-text object key, and provenance;
- `Chunk` holds version, ordinal, page/section metadata, text checksum, and
  source location;
- `EmbeddingSet` records provider, model, dimension, normalization, and
  chunking configuration;
- `ChunkEmbedding` is initially a PostgreSQL vector column if `pgvector` is
  approved; otherwise it stores an object key to a versioned embedding file;
- `IndexGeneration` records index type, scope, metric, vector count, checksum,
  and compatibility metadata.

Keep `ArtifactGeneration`, validation, maintenance job, and audit semantics;
adapt them to point at PostgreSQL rows and object manifests.

**Verify**: `makemigrations --check`, PostgreSQL migrations on an empty
database, and rollback rehearsal on a disposable clone all pass.

### Step 4: Bulk copy relational state

Load users, folders/categories, PDFs, lifecycle values, audit records, and
settings in dependency order. Preserve source IDs in explicit `legacy_id`
columns if primary-key preservation is unsafe. Convert JSON fields to typed
rows, not opaque JSON blobs. Use batched `COPY`/bulk inserts, transactions,
conflict reports, and an idempotency key per source row.

**Verify**: row counts, foreign-key checks, lifecycle counts, category mapping,
user role mapping, and a deterministic source-to-target ID report match.

### Step 5: Copy PDFs and derived files

For every `PDFFile.file`, calculate the source SHA-256, upload to the
content-addressed object key, set metadata, and write the target object record.
Do the same for extracted text/chunk exports and FAISS files. Never copy a
filesystem path into production as if it were an object key. Keep source and
target files until checksums and byte counts agree.

**Verify**: sample and full-manifest hash comparison, download/read tests,
MIME/content validation, and no untracked source files.

### Step 6: Reconstruct or import embeddings and indexes

For the first cutover, import the existing embedding arrays only after their
model/dimension/chunk order is proven. Rebuild one folder from target chunks
and compare top-k results with the source. Prefer rebuilding all indexes in a
staging environment rather than trusting opaque FAISS files.

Do not expose FAISS as the authoritative store. Treat it as a generated,
versioned acceleration artifact that can be rebuilt from PostgreSQL chunks and
the approved embedding set.

**Verify**: dimensions, vector counts, checksums, representative query top-k
overlap, Marathi/English queries, and restricted-scope access tests pass.

### Step 7: Shadow-read and rehearse cutover

Run the new stack beside the current stack using a copy of the data. Compare:

- category and PDF counts;
- source visibility and authorization decisions;
- query embedding dimensions;
- top-k document IDs and scores within an agreed tolerance;
- generated-answer source IDs;
- PDF open/download behavior;
- admin CRUD and maintenance job state transitions.

Run migrations, failure injection, worker restart, object-store outage,
partial-upload, and rollback drills. Do not compare generated prose byte-for-
byte; compare citations, grounded source identity, and deterministic retrieval
results.

### Step 8: Controlled cutover

1. Announce a short maintenance window and disable writes.
2. Take a final SQLite snapshot and upload the final source manifest.
3. Apply the final delta to PostgreSQL and object storage.
4. Run all release validations and record the target release ID.
5. Switch the application configuration to PostgreSQL and object storage.
6. Start web/worker services against the target and verify health, login,
   search, source links, and admin workflows.
7. Keep the old SQLite volume mounted nowhere but preserved read-only for the
   rollback window.

### Step 9: Rollback and decommission

Rollback means switching the application to the last known-good image and data
release, not copying files backwards by hand. If PostgreSQL writes occurred,
freeze the target and reconcile the delta before any rollback. Decommission
the SQLite volume only after the agreed observation period, two successful
restore drills, and an operator-signed custody record.

## Test plan

- Unit tests for row mapping, object-key derivation, checksums, and idempotency.
- Integration tests against disposable PostgreSQL, pgvector (if selected),
  and S3-compatible storage.
- Full manifest comparison before and after migration.
- Migration rehearsal from the real sanitized snapshot.
- Search contract tests for English, Marathi, mixed-language, empty, no-result,
  and restricted-folder queries.
- Failure tests for missing object, checksum mismatch, stale index, duplicate
  delta, database outage, object-store outage, and worker restart.
- Browser tests for login, PDF viewing, source links, upload status, and search.

## Scope and stop conditions

In scope: schema, migration tooling, data manifests, object storage adapter,
cutover/rollback runbooks, and tests.

Out of scope: changing answer prompts, changing the public API contract,
deleting the current SQLite implementation, or importing legacy data without a
separate reconciliation decision.

Stop and report if source counts cannot be reconciled, the embedding model or
dimension is unknown, object-store conditional operations fail, a PostgreSQL
migration requires destructive changes, or rollback cannot restore the exact
previous release.

## Done criteria

- A sanitized production-shaped rehearsal migrates with zero unexplained row,
  file, checksum, or authorization differences.
- A target release manifest validates and can be restored into a disposable
  environment.
- Search retrieval and source references pass the agreed parity threshold.
- Cutover and rollback have each been executed successfully in rehearsal.
- The active release is identified by PostgreSQL migration state, object
  manifest digest, image digest, and generation ID.
