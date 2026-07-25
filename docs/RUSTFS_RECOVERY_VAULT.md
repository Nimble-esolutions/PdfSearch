Status: Active, constrained
Audience: Recovery, Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-26
Canonical source: docs/RUSTFS_RECOVERY_VAULT.md
Supersedes: Earlier claims that S3 restore or scheduled backup is automatic

# RustFS Recovery Vault

## Audited verdict

The vault is a working set of storage, publication, and restore primitives, but
the deployed application is **not yet a hands-off backup and disaster-recovery
system**.

Verified against a disposable MinIO service on 2026-07-26:

- checksum-verified object put/get and immutable-key validation work;
- S3 conditional create/replace and concurrent CAS fencing work;
- authoritative dataset registration, global-writer fencing, generation
  publication, and pointer update work;
- the full library-level pipeline can publish, download, validate, sanitize,
  rehearse, activate, and roll back a generation.

Not yet operationally connected:

- `RESTORE_POLICY=startup-latest|startup-pinned` is parsed and validated, but
  neither application entrypoint invokes the restore pipeline;
- the admin/maintenance `restore_generation` job calls the older
  `stage_generation()` path, not `run_restore_pipeline()`;
- publication writes
  `datasets/{dataset}/generations/{generation}/manifest.json`, while the
  admin staging path reads `manifests/{generation}.json`;
- admin “promote” and “rollback” update `ArtifactGeneration` database status;
  they do not activate the staged database, media, or indexes;
- scheduled backup requires a dirty flag, but current application mutations do
  not call `mark_data_dirty()`;
- the production `maintenance` Compose service does not explicitly receive the
  complete vault, restore, scheduler, and release-identity environment contract;
- the checked-in integration Compose command is not a reliable gate: the image
  entrypoint ignores the runner command, its Django test label conflicts with
  `core/tests.py`, and its services are split across networks.

Until Plan 003 closes these gaps and a clean-volume restore drill passes, treat
the vault as an **explicit, operator-controlled recovery component**, not as the
only production backup and not as proof that a fresh deployment can self-heal.

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
3. writes a local SQLite startup snapshot under `BACKUP_DIR`;
4. applies migrations;
5. reconciles an incomplete activation journal;
6. starts the application.

It does not compare the volume with the vault, publish accumulated changes, or
restore a newer generation. Local startup snapshots are on the same volume and
do not protect against volume or host loss.

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

It does **not** pull from RustFS. Setting `DATA_MODE=s3-restore`,
`RESTORE_POLICY=startup-latest`, or `DATA_MODE=s3-pinned` currently validates
identity only; it does not perform startup restoration.

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

Do not use the current admin “Pull to Staging”, “Promote”, or “Rollback” labels
as proof that active bytes changed. Those controls remain blocked for disaster
recovery until Plan 003 connects them to the full pipeline.

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

`RESTORE_POLICY` and `DATA_PINNED_GENERATION` are currently validation and
future-orchestration inputs; they do not trigger startup restore.
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

The disposable integration stack required manual entrypoint and network
workarounds. Therefore these results validate the underlying primitives, not
the checked-in Compose gate or deployed Dokploy wiring.

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

Plan 003 must connect one namespace and one orchestrator from publication
through restore and activation, pass the complete environment to the worker,
wire mutation dirty-state or another reliable change detector, repair the
integration Compose gate, and prove both:

1. an intact accumulated volume is preserved across redeploy; and
2. a fresh disposable volume can restore the selected generation with no
   hidden host-only state.
