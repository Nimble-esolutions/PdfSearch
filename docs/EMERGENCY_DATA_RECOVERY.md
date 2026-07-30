# Emergency Data Recovery

Status: Active

Owner: Operations

Last reviewed: 2026-07-27

Vault generations are the canonical full-recovery artifact. Local recovery
sets are bounded, same-volume, database-only emergency points created before
pending migrations, activation, bulk index mutation, or a manual operator
request. Their presence does not prove that media or indexes are restorable.

| Incident or objective | Use | Important boundary |
|---|---|---|
| Bad application image, data is sound | Roll back the immutable image digest | Do not restore a database merely to roll back code |
| Bad signed runtime activation | Existing signed runtime rollback | Active and previous runtime remain protected |
| Application SQLite damage or bad migration | `emergency_db prepare`, validate, then approved isolated recovery | Never copy directly over a live database |
| Control SQLite damage | Prepare the same recovery set and validate `control.sqlite3` | Control history may be newer than the database-only point |
| Media file without a database row | `reconcile_media_pdfs` | Imports orphan media; it does not repair an existing dangling row |
| Database row whose PDF media is missing | Explicit **Mark unavailable** action, then recover the exact verified file | Preserves identity and history; never remap or delete automatically |
| Missing/corrupt FAISS with valid stored embeddings | Repair Stored Indexes | No external embedding calls |
| Missing chunks or embeddings | Reindex Needed/Selected | Creates local derived change; publish a new Vault candidate |
| Complete dataset recovery | Verified Vault restore and signed activation | Canonical route for database, media, indexes, and manifests |
| Host or volume loss | Immutable image plus verified Vault generation | Local recovery sets are expected to be lost with the volume |

## Emergency database CLI

```sh
python manage.py emergency_db create --reason manual
python manage.py emergency_db list --json
python manage.py emergency_db verify --set <set-id>
python manage.py emergency_db prune
python manage.py emergency_db prune --apply --confirm <plan-id>
python manage.py emergency_db prepare --set <set-id> \
  --target-root /isolated/recovery/<set-id> --ack-database-only
python manage.py emergency_db validate --workspace /isolated/recovery/<set-id>
```

`prune` is read-only without `--apply`. A changed plan is rejected. The default
policy keeps the newest three verified sets, expires unheld sets after seven
days, caps them at 5 GiB, and never automatically removes an incident-held set.
If protected sets exceed the cap, required risky operations fail with
`recovery_capacity_degraded`.

Each atomically published `recovery-set.json` is secret-free and records both
database hashes and sizes, SQLite integrity and foreign-key results, migration
leaves, deployment/dataset/image/runtime identities, the creation reason,
companion-artifact posture, and holds. Preparation rejects symlinks, existing
targets, active data/control/recovery volumes, and insufficient capacity.

Before activation, validate recovery authentication in the isolated workspace
and reconcile referenced media. A database-only workspace always reports that
indexes require rebuilding. Do not activate it merely because SQLite integrity
passes.

### Validation and readiness

`prepare` copies the verified databases into a new isolated workspace and
reports its initial state. It does not make the workspace activation-ready.
`validate` then performs read-only checks against those copied SQLite files:

- database integrity, foreign keys, and recovery-set hashes;
- application and control migration leaves against the running immutable
  image;
- deployment, dataset, and image identity compatibility without printing
  identity values;
- the configured recovery-superadmin login without printing credentials,
  password hashes, or user records;
- referenced-media completeness without printing document paths; and
- whether indexes still require rebuilding.

The JSON result deliberately separates
`database_verification_state: verified` from `recovery_ready`. The top-level
`verification_state` is `blocked` whenever any readiness blocker exists, so
automation cannot mistake database-only integrity for a usable recovery.
Migration checks reject both required migrations that are absent and applied
migrations unknown to the running image. Typed entries in `blockers` explain
why the workspace is not ready, including `required_migrations_unapplied`,
`unknown_applied_migrations`, `migration_evidence_incompatible`,
`recovery_superadmin_unproven`, `referenced_media_missing`, and
`index_rebuild_required`.

`emergency_db validate` prints this secret-free report and exits nonzero while
any blocker remains. The prepared workspace is preserved for investigation and
approved reconciliation; validation never migrates it, overwrites it, or opens
the configured live Django database aliases. Rebuild indexes through the local
Documents & Indexes workflow and use the existing signed runtime
activation/rollback workflow for any eventual cutover.

## Unavailable document media

When a database row references media that cannot be verified, an administrator
may enter the expected SHA-256 and byte size from approved custody evidence,
choose a controlled reason, record a bounded case reference, and explicitly
type `MARK UNAVAILABLE` on
the document page. This is a
non-destructive quarantine: the row ID, metadata, stored derived evidence, and
maintenance history remain intact, while the document is excluded from search,
index repair, readiness counts, candidate media requirements, and activation
file checks. The action is audited and is never applied automatically.

Restore availability only after the exact verified file has been returned to
the row's approved storage path. The restore action rejects symlinks and
non-regular or changing files, verifies the complete SHA-256 and byte size,
keeps the row unavailable on any mismatch, and returns a verified row to its
preserved prior lifecycle. It records a second bounded audit event when
successful. Validation and bounded reindexing must follow.
Hard deletion remains a separate irreversible retention decision.

`reconcile_media_pdfs` scans files already present under `MEDIA_ROOT` and
creates rows for paths not represented in the database. It does not discover
missing bytes, rewrite an existing row's path, or prove that a renamed file is
the same document.
