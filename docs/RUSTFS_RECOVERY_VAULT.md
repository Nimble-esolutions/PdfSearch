Status: Active, constrained
Audience: Recovery, Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-08-03
Canonical source: docs/RUSTFS_RECOVERY_VAULT.md
Supersedes: Earlier claims that S3 restore or scheduled backup is automatic

# RustFS Recovery Vault

## Current rehearsal status

The 2026 recovery lineage has now been exercised against the live legacy
volume's read-only snapshot. The verified source generation is
legacy-20260802T085639Z-86288855 in dataset ai-sahakar-prod-v2. The explicit
clone/rebind destination is clone-legacy-20260802T085639Z-86288855 in dataset
ai-sahakar-stage-2026, with 416 verified objects totalling 1,093,501,777 bytes.

That clone has been restored into stage quarantine, migrated through
0027_pdffile_processing_evidence, OCR-processed where native extraction was
blank, and reconciled so all 242 PDFs are ready and indexed. It is now bound to
a signature-verified stage runtime pointer. Manual stage backup and isolated
recovery rehearsal have succeeded; final acceptance remains pending the final
immutable image and one repeatable fresh-volume restore.

For the exact boundary inventory, environment posture, OCR evidence, Compose
project-name warning, and remaining checklist, see
[STATUS-2026-08-03.md](STATUS-2026-08-03.md).

## Audited verdict

The vault is a working set of storage, publication, and restore primitives, but
the deployed application is **not yet a hands-off backup and disaster-recovery
system**.

Verified against disposable MinIO services and CI certification targets through
2026-07-30:

- checksum-verified object put/get and immutable-key validation work;
- S3 conditional create/replace and concurrent CAS fencing work;
- authoritative dataset registration, global-writer fencing, generation
  publication, and pointer update work;
- the full library-level pipeline can publish, download, validate, sanitize,
  rehearse, activate, and roll back a generation;
- the active Workbench path publishes dataset-scoped generations, performs
  separate CAS promotion, prepares a verified and rehearsed restore workspace,
  and activates it only through a separately confirmed signed intent;
- one CI-enforced journey carries the exact generation and manifest through
  authoritative selection, restore, dual-supervisor cutover, fresh Gunicorn
  readiness, English/Marathi search, and final signed evidence reconciliation;
- the production web and maintenance services receive the same
  lifecycle-critical environment contract, and durable mutation epochs drive
  coalesced scheduled publication.

These are implementation and CI results. They are not a record that production
RustFS passed a capability probe, that an operator accepted a production
recovery drill, or that a live production volume was restored.

Still environment-dependent:

- `RESTORE_POLICY=startup-latest|startup-pinned` is deliberately a fail-closed
  empty-database guard, not an automatic remote restore switch;
- accumulated named-volume redeploy and genuinely fresh-volume restore drills
  remain unrecorded;
- production RustFS capabilities and recovery remain unproved without an
  operator-approved, non-destructive drill.

Treat the vault as an **explicit, operator-controlled recovery component**, not
as the only production backup and not as proof that a fresh deployment can
self-heal.

## What is authoritative today

```text
running application
  └── /app/data named volume
      ├── db.sqlite3
      ├── media/              uploaded PDFs
      ├── faiss_indexes/      active search indexes
      ├── chroma_db/          legacy/derived index data
      ├── staticfiles/        rebuildable collected assets
      ├── backups/            same-volume local snapshots/workspaces
      └── .instance_id        writer/instance identity
```

The named volume is the operational source of truth. RustFS stores immutable,
point-in-time generations only after a successful explicit publication.
Redis is a cache/queue dependency and is not part of the recovery generation.

## What a published generation contains

`sync_active_generation()` currently publishes:

- a SQLite snapshot made with the SQLite backup API;
- PDF files discovered under the configured media tree;
- FAISS index files;
- a manifest with checksums, schema inventory, counts, dataset identity,
  writer epoch, and object keys;
- a CAS-guarded authoritative pointer to the generation.

It does not publish:

- non-PDF media files;
- Chroma files, despite inventorying their counts;
- collected static files, despite inventorying their counts;
- local backup history or restore workspaces;
- `.instance_id`, environment values, credentials, logs, or Redis state.

Static files are expected to come from the immutable application image.
Whether Chroma is required for a given release must be resolved by the
compatibility gate; do not assume that an inventory count means bytes were
uploaded.

Publication is a point-in-time recovery set, not a live mirror. The SQLite
snapshot is consistent, but PDF and FAISS files are read separately while the
application may still accept writes. A successful publication therefore needs
post-publication count/checksum and representative-search evidence until a
coordinated local snapshot boundary is implemented.

## Deployment scenarios

| Scenario | What starts the application | What the vault does | Operator conclusion |
| --- | --- | --- | --- |
| Normal redeploy, same named volume | Existing SQLite/media/indexes | Nothing automatically | Expected path; verify volume identity and data counts |
| Existing volume with months of accumulated changes | Latest local state | Contains only the last successfully published generation | Measure recovery-point age; unpublished changes are not recoverable from the vault |
| Fresh empty volume, no usable vault generation | Migrations and optional declared seed/empty mode | Nothing automatically | Service may be empty; stop before accepting traffic |
| Fresh empty volume, valid vault generation exists | Migrations/seed still start first | Full restore works only when invoked through an approved isolated workflow | Bootstrap credentials and manual orchestration are currently required |
| Volume lost but RustFS survives off-host | New empty volume | Can supply the last validated generation | Restore into isolation, validate, then activate explicitly |
| Host lost and RustFS is on the same host without replication | Neither local volume nor independent vault is guaranteed | May be lost with the host | This is not disaster recovery |
| Existing volume is healthy but older than a vault generation | Local volume remains authoritative | No automatic pull or merge | Never overwrite or merge automatically; reconcile in a disposable target |

## Existing-volume behavior

A normal Dokploy deploy recreates containers while retaining the named
`/app/data` volume. Startup:

1. validates and prepares the existing directories;
2. runs SQLite integrity checks;
3. applies migrations;
4. reconciles an incomplete activation journal;
5. starts the application.

Current entrypoints do not create the retired flat
`db_backup_YYYY-MM-DD_HHMMSS.sqlite3` file on every start. Older copies may
still occupy the same volume and require the bounded, separately confirmed
cleanup described in `RECOVERY_CERTIFICATION.md`. Startup does not compare the
volume with the vault, publish accumulated changes, or restore a newer
generation.

For an accumulated volume, record:

- active volume identity and mount mode;
- SQLite integrity and schema/migration state;
- database/PDF/FAISS counts;
- last successful immutable vault generation and its timestamp;
- changes since that generation;
- independent backup location and restore-drill date.

## Fresh-deployment behavior

With a genuinely empty `/app/data` volume, `start.sh` either:

- starts empty for `DATA_BOOTSTRAP_MODE=empty|bootstrap`;
- copies the declared image seed when one exists and strict rules permit it; or
- fails in strict mode when no approved starting data exists.

It does **not** pull from RustFS. Setting `RESTORE_POLICY=startup-latest` or
`startup-pinned` makes both entrypoints stop before database creation when the
database is absent or zero bytes. A non-empty existing database is preserved.
`DATA_MODE=s3-restore` and `DATA_MODE=s3-pinned` still do not perform startup
restoration.

The current safe recovery sequence is:

1. preserve the empty/new volume and record its identity;
2. select an immutable image compatible with the source generation;
3. verify the vault endpoint, bucket, registration, authoritative pointer, and
   conditional-write capabilities without exposing credentials;
4. restore into a disposable application or uniquely named volume;
5. run SQLite integrity, manifest checksums, migration rehearsal, PDF/FAISS
   counts, index loading, representative search, and source-link checks;
6. record the exact generation and explicit promotion decision;
7. activate using the full restore/activation pipeline;
8. verify `/livez`, `/readyz`, `/health/data/`, login, document listing, search,
   and source access before routing traffic.

Vault promotion changes the authoritative object-store pointer, not runtime
bytes. Restore preparation produces an `activation_ready` workspace. Only the
separately confirmed activation action, followed by runtime pointer and
readiness verification, is evidence that active bytes changed. Production
disaster recovery remains unproved until the Plan 003 deployment drills pass.

## Required service environment

The process that performs the operation must receive the configuration. Setting
values only on the web service is insufficient when the maintenance worker runs
the job.

Common vault variables:

```text
ARTIFACT_VAULT_ENABLED
ARTIFACT_VAULT_ENDPOINT
ARTIFACT_VAULT_BUCKET
ARTIFACT_VAULT_REGION
ARTIFACT_VAULT_ACCESS_KEY
ARTIFACT_VAULT_SECRET_KEY
```

Publication additionally requires:

```text
APP_ENV=production
DEPLOYMENT_ID
DATASET_ID
AUTHORITATIVE_DATASET_ID
PRODUCTION_SOURCE_ID
BACKUP_ROLE=writer
BACKUP_SYNC_MODE=manual|scheduled|event-driven|hybrid
APP_IMAGE_DIGEST
APP_RELEASE_VERSION
```

Restore identity may use:

```text
RESTORE_SOURCE_DATASET_ID
RESTORE_POLICY=disabled|manual|startup-latest|startup-pinned
DATA_PINNED_GENERATION
BACKUP_ROLE=reader|disabled
```

`RESTORE_POLICY` is a consumed startup posture input, not an automatic restore
switch. `startup-*` prevents implicit empty-database creation; it never contacts
the Vault or activates bytes. `DATA_PINNED_GENERATION` is required for
`startup-pinned` and remains an input to future approved restore orchestration.
`ARTIFACT_VAULT_AUTO_SYNC`, `ARTIFACT_VAULT_AUTO_PULL_ON_EMPTY`, and
`ARTIFACT_VAULT_RETENTION_COUNT` are not consumed runtime controls.

## Verification evidence

The 2026-07-26 local audit ran:

```text
27 focused artifact-vault, health, audit, and job tests: passed
16 real MinIO conditional-write/concurrency tests: passed
5 real MinIO application-publication tests: passed
7 real publish/restore/sanitize/rehearse/activate/rollback tests: passed
```

The checked-in disposable gates supply their own entrypoints, bucket
initialization, shared MinIO/Redis network, immutable dependency-image
requirements, process-death proof, and automatic cleanup. The maintenance
lifecycle additionally proves one exact generation and manifest through
publication, authoritative selection, restore rehearsal, signed activation,
fresh Gunicorn readiness, and bilingual search. This validates the local
lifecycle mechanism, not deployed Dokploy wiring or production RustFS.

A production readiness claim additionally requires a non-destructive,
clean-volume restore drill against the configured RustFS service. This audit
did not access or mutate production RustFS data.

## Safety rules

- Never run a first restore drill against the active volume.
- Never treat a bucket-health badge as backup freshness.
- Never equate a database generation status with active filesystem state.
- Never enable more than one authoritative writer for a dataset.
- Never publish with mutable image identity or missing source identity.
- Never delete local accumulated data until a separately restored generation
  has passed acceptance and the rollback window has expired.
- Keep vault storage independent of the application host or replicate it
  off-host; same-host object storage is not disaster recovery.
- Do not delete or rewrite a generation referenced by an incident, release, or
  restore record.

## Future completion gate

The active `vaultops` path now provides dataset-scoped publication,
activation-ready restore preparation, separately confirmed runtime activation,
durable mutation epochs, snapshot barriers, a fail-closed startup posture, and
a CI-enforced same-generation disposable proof. Plan 003 must still prove both
deployment boundaries:

1. an intact accumulated volume is preserved across redeploy; and
2. a fresh disposable volume can restore the selected generation with no
   hidden host-only state.
