# Internal legacy data → artifact vault migration

## Historical 2026-08-02 execution record

The current migration is not the older 2026-07-25 port described in the
historical section below. The live legacy source was read-only snapshotted into
the v2 bucket and dataset, then cloned through the explicit stage rebind path.

    source generation:      legacy-20260802T085639Z-86288855
    stage generation:       clone-legacy-20260802T085639Z-86288855
    destination objects:    416
    destination bytes:      1,093,501,777

The clone was restored to stage quarantine, migrated through
0027_pdffile_processing_evidence, and reconciled to 242 ready/indexed PDFs.
Nine PDFs used bounded local OCR fallback with English, Marathi, and Hindi
language packs. At the time of this snapshot activation and backup were still
pending; both later succeeded. The authoritative current record is
[`HANDOFF.md`](HANDOFF.md), while `STATUS-2026-08-02.md` preserves this dated
execution evidence.

This runbook describes the developer/operator tool for importing a legacy
PdfSearch data root into the S3-compatible artifact vault. It is intentionally
an AI-agent/dev-side repository utility, separate from application startup,
application images and the normal authoritative sync job.

## Safety boundary

The command is dry-run-first. A successful `--publish` creates an immutable,
dataset-scoped candidate generation and its content-addressed blobs. It does
**not** advance `control/authoritative.json`, change the running application,
delete objects, or mutate the source volume. First-time dataset registration
requires the separate `--register-dataset` acknowledgement. Pointer promotion
is a later, separately confirmed invocation.

For the 2026 recovery lineage, use only the new immutable source bucket
`ai-sahakar-prod-flowdocs-artifact-vault-v2` with dataset `ai-sahakar-prod-v2`.
The historical `ai-sahakar-prod-flowdocs-artifact-vault` bucket and its
authoritative pointer are evidence from an earlier port and are explicitly
out of scope: this utility must never write to or promote that pointer.

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

The command first copies the selected source trees into a disposable local
snapshot. It records file identity/size/timestamps before copying, uses
no-follow regular-file reads, creates the SQLite copy through the online backup
API, then repeats the complete source scan. Any added, removed or changed source
entry fails with `consistent_snapshot_unproven` before an S3 client is opened.
Only the stable local snapshot is hashed and uploaded.

The command creates one manifest containing:

- a consistent SQLite backup made with SQLite's backup API;
- files under `media`, including PDFs stored as content-addressed PDF blobs;
- FAISS files;
- Chroma files, when present;
- PDF-cache/index metadata files, when present;
- source inventory, schema/migration information, counts, sizes and SHA-256
  checksums; and
- deterministic snapshot evidence bound to the manifest.

Static files are rebuildable and are excluded by default. Pass
`--include-static` only when a specific deployment requires the captured
static tree. Secrets, environment files, logs, Redis state, caches, temporary
restore workspaces and Docker metadata are never part of this migration.

The generated object namespace is:

```text
datasets/<dataset-id>/generations/<generation-id>/manifest.json
datasets/<dataset-id>/generations/<generation-id>/database.sqlite3
datasets/<dataset-id>/blobs/pdfs/sha256/<digest>.pdf
datasets/<dataset-id>/blobs/files/<digest>
```

The manifest is the restore contract. Each object is uploaded with conditional
create semantics; an existing object is accepted only when its size and
checksum metadata match. A different object under the same immutable key fails.
After upload, every object is HEAD-verified and the manifest is re-read,
schema-validated and digest-compared.

## Completed initial port

The first agent-run port completed against the read-only legacy volume using
the target bucket and stable namespace above. The active destination generation
is `legacy-20260725T204411Z-v2c4d9e1`; the manifest contains 564 files,
including 242 PDFs and 45 FAISS files. The destination registration and
authoritative pointer were verified after upload. The earlier incomplete
generation remains immutable and is not selected by the pointer.

## Local dry run

Run from a repository checkout in a disposable execution environment. The
source volume is mounted read-only; the tool itself is not added to the
application image:

```bash
python scripts/ops/migrate_legacy_volume_to_vault.py \
  --source-root /source \
  --dataset-id ai-sahakar-prod-v2 \
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
python scripts/ops/migrate_legacy_volume_to_vault.py \
  --source-root /source \
  --dataset-id ai-sahakar-prod-v2 \
  --generation-id legacy-20260726T120000Z-a1b2c3d4 \
  --source-label sahakar-dev-frontend-dockerfile-1cubi5-prod-flowdocs \
  --checkpoint /operator-state/legacy-20260726T120000Z-a1b2c3d4.json \
  --register-dataset \
  --publish
```

`--register-dataset` is required only when the reviewed destination has no
registration. On later publications omit it: the existing registration must
match dataset ID, application identity, production source identity and
manifest-schema range exactly.

The durable checkpoint contains no credentials or object bodies. It binds the
generation ID, source root, creation time, manifest digest and per-object
verification progress. Retry with the same generation and checkpoint reuses
only digest/size-matching objects. A changed source produces
`checkpoint_manifest_mismatch` before any new network writes.

The command reports the immutable candidate and upload/deduplication counts and
always reports `pointer_updated: false`. `--candidate-only` is retained as a
deprecated compatibility no-op; candidate-only is now the only publication
behavior. Use a unique dataset namespace for a staging rehearsal when there is
any possibility of confusing production and staging data.

## Promote a verified candidate

Promotion is never part of `--publish`. After independent review, run a new
invocation without a source mount:

```bash
python scripts/ops/migrate_legacy_volume_to_vault.py \
  --dataset-id ai-sahakar-prod-v2 \
  --production-source-id ai-sahakar-prod \
  --promote-generation legacy-20260726T120000Z-a1b2c3d4 \
  --confirm-promotion ai-sahakar-prod-v2:legacy-20260726T120000Z-a1b2c3d4
```

The promotion invocation:

1. validates the immutable registration;
2. re-reads and validates the candidate manifest and every referenced object;
3. validates the current pointer→manifest digest chain, including the initial
   legacy pointer shape used by the completed port;
4. acquires a dataset-scoped writer record with conditional create/replace and
   a new fencing epoch;
5. revalidates registration and candidate after fencing;
6. moves the pointer with exact ETag CAS; and
7. conditionally expires only its own writer record.

A CAS race leaves the candidate intact. Writer release never performs a blind
delete and therefore cannot remove a successor's record.

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

## Future refresh design

The same agent-side tool can be run again against a later read-only snapshot.
Each publication creates a new candidate and uploads only missing
content-addressed objects. A distinct reviewed promotion may then CAS-update
the single `ai-sahakar-prod` authoritative pointer. A failed publication or
promotion leaves the previous pointer untouched. The latest application can
then use the pointer for latest-compatible restore, or a pinned generation for
rollback. A later in-app sync feature should call the same separated
publication/promotion contract through the normal writer-fencing path rather
than duplicating this migration logic.

## Rollback and failure handling

- Failed dry runs make no external changes.
- A failed upload leaves only immutable candidate objects; retry with the same
  generation and checkpoint is safe when manifest/object checksums match.
- A checksum conflict is a stop condition; do not force overwrite or delete.
- Candidate generations can be ignored or retired after confirming they are
  not referenced. The current database-only “purge” label is not object
  deletion and must not be described as such.
- Publication leaves the authoritative pointer unchanged, so the running source
  service continues using its existing local volume and active generation.
- A completed promotion is immutable history. Reversal is a separately
  confirmed CAS promotion of a previously verified generation.
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
