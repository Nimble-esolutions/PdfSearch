# Internal legacy data → artifact vault migration

This runbook describes the developer/operator tool for importing a legacy
PdfSearch data root into the S3-compatible artifact vault. It is intentionally
separate from application startup and from the normal authoritative sync job.

## Safety boundary

The command is dry-run-first. A successful publish creates an immutable,
dataset-scoped generation and its content-addressed blobs. It does **not**
advance `control/authoritative.json`, change the running application, delete
objects, or mutate the source volume. Dataset registration is an optional,
explicit control-record operation.

The source must be mounted read-only, or copied into a disposable workspace.
Do not point the command at a writable live application directory. The stable
production source identified during the initial audit is:

| Item | Value |
| --- | --- |
| Swarm service | `sahakar-dev-frontend-dockerfile-1cubi5` |
| Persistent data volume | `prod_flowdocs` |
| Application mount | `/app/flowdocs` |
| Related volumes | `prod_static`, `prod_backup` |

The service name is stable; individual Swarm task/container IDs are not.
Always inspect the current service and image digest immediately before a
future migration. Never use a mutable `:latest` image for the migration
command; use the exact released image digest and the same application
configuration required by Django.

### Live storage reconciliation

The read-only production audit found the following state at the time of the
feature work:

| Location | Observed state | Meaning |
| --- | --- | --- |
| `ai-sahakar-prod-flowdocs-artifact-vault` | 0 files, about 4 KB | Requested target bucket exists but has no published generation |
| `ai-sahakar-prod-flowdocs-data-volume` | 1,098 files, about 2.6 GB | Existing RustFS data-volume custody contains the `ai-sahakar-prod` dataset and generation objects |
| `sahakar-dev-frontend-dockerfile-1cubi5` task | no `ARTIFACT_VAULT_*`, `DATASET_ID`, `BACKUP_ROLE`, `BACKUP_SYNC_MODE`, or `DATA_MODE` runtime variables | Legacy app cannot publish to or restore from the vault contract |

The difference is expected until the new image and vault environment are
deployed and the internal publisher is run. The existing data-volume bucket
must not be treated as an implicit source for the new artifact-vault bucket:
first reconcile its authoritative pointer, generation identity, checksums and
intended dataset with the legacy-volume inventory. Do not bulk-copy RustFS
internal files or `.xl.meta` objects; use the application manifest contract.

## What is published

The command creates one manifest containing:

- a consistent SQLite backup made with SQLite's backup API;
- PDF files referenced by the database, stored as content-addressed blobs;
- FAISS files;
- Chroma files, when present;
- embedding/index metadata files, when present;
- source inventory, schema/migration information, counts, sizes and SHA-256
  checksums.

Static files are rebuildable and are excluded by default. Pass
`--include-static` only when a specific deployment requires the captured
static tree. Secrets, environment files, logs, Redis state, caches, temporary
restore workspaces and Docker metadata are never part of this migration.

The generated object namespace is:

```text
datasets/<dataset-id>/generations/<generation-id>/manifest.json
datasets/<dataset-id>/generations/<generation-id>/database.sqlite3
datasets/<dataset-id>/generations/<generation-id>/metadata/<relative-path>
datasets/<dataset-id>/blobs/pdfs/sha256/<digest>.pdf
```

The manifest is the restore contract. Each object is uploaded with conditional
create semantics; an existing object is accepted only when its size and
checksum match. A different object under the same immutable key fails.

## Local dry run

Run from the repository's released image or development container with the
same non-secret Django settings as the source application:

```bash
python manage.py publish_legacy_generation \
  --source-root /source \
  --dataset-id legacy-prod-20260726 \
  --source-label sahakar-dev-frontend-dockerfile-1cubi5-prod-flowdocs \
  --output /tmp/legacy-release.json
```

The command prints only generation identity and counts. Review the manifest
for missing files, database integrity, PDF counts, FAISS loadability and
embedding compatibility before publishing. The output file is an inventory;
handle it as sensitive operational metadata and remove it after review.

## Publish a candidate generation

Publishing requires the vault environment to be explicitly enabled and the
target bucket/endpoint credentials to be supplied through the deployment's
secret mechanism. Never put those values in the command line, repository,
logs or screenshots.

For the current server, the target bucket name is known, but the publishing
image, endpoint configuration, dataset ownership and operator credential
posture still require staging confirmation. PR #92 is the implementation
vehicle and is intentionally draft until that review is complete.

```bash
python manage.py publish_legacy_generation \
  --source-root /source \
  --dataset-id legacy-prod-20260726 \
  --generation-id legacy-20260726T120000Z-a1b2c3d4 \
  --source-label sahakar-dev-frontend-dockerfile-1cubi5-prod-flowdocs \
  --publish
```

The command reports the immutable generation and upload/deduplication counts.
It ends with an explicit statement that the authoritative pointer was not
changed. Use a unique dataset namespace for a staging rehearsal when there is
any possibility of confusing production and staging data.

To create a dataset registration as part of the same explicit operation, add
`--register-dataset --production-source-id <non-secret-operator-id>`. The
registration is created conditionally and is never overwritten. Omit this
option when another deployment already owns the dataset registration.

## Restore into a fresh deployment

A fresh empty volume has no Django users or job database, so a browser UI
cannot be the first bootstrap action by itself. The safe sequence is:

1. Start the staging image with a disposable/temporary bootstrap identity and
   the vault configured read-only for the source dataset.
2. Run migrations and log in to the protected operations UI.
3. Select the pinned generation and run the full restore pipeline: download,
   checksum validation, SQLite integrity, compatibility preflight, optional
   sanitisation, migration rehearsal, then activation.
4. Restart the application after activation if the runtime data root or
   SQLite connection is replaced, then verify login, categories, PDFs, search,
   FAISS dimensions/counts and protected source links.
5. Only after staging passes should an operator consider a separate, explicit
   promotion workflow. This tool itself never promotes.

The current restore implementation already has the full pipeline primitive,
but the maintenance worker's legacy `restore_generation` path is not a
substitute for that pipeline. Follow-up work must wire the protected UI job to
`run_restore_pipeline` and persist workspace/activation evidence before
calling the fresh-deployment restore flow complete.

## Rollback and failure handling

- Failed dry runs make no external changes.
- A failed upload leaves only immutable candidate objects; retry with the same
  generation is safe when checksums match.
- A checksum conflict is a stop condition; do not force overwrite or delete.
- Candidate generations can be ignored or purged through the existing audited
  retention workflow after confirming they are not referenced.
- Because the authoritative pointer is unchanged, the running source service
  continues using its existing local volume and active generation.
- If activation later fails, use the activation journal and previous pointer
  rollback procedure; do not restore by copying a legacy directory over an
  active volume.

## Staging acceptance checklist

- source service/image/volume identity captured without secrets;
- dry-run manifest reviewed and stored outside the source volume;
- target bucket and dataset namespace confirmed;
- candidate generation published without pointer advancement;
- restore workspace reaches `activation_ready` after checksum and compatibility
  checks;
- migration rehearsal passes on the staging image;
- activated search returns expected categories, PDFs and source references;
- no missing Devanagari filenames or protected-PDF errors;
- no unexpected S3 writes, deletes, public links or production pointer change;
- evidence records contain identities, counts and checksums, never credentials
  or document contents.
