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
| Missing/misnamed PDF media | `reconcile_media_pdfs` | Media reconciliation is not database disaster recovery |
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
