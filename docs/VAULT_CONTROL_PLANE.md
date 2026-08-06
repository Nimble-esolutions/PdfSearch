Status: Historical foundation; active internal compatibility schema remains
Audience: Developer, maintainer
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical replacement: docs/dataops/V3_ARCHITECTURE.md

# Internal VaultOps control schema (historical foundation)
## Current-state pointer

Use the living [HANDOFF.md](HANDOFF.md) for current verified state.
DataOps v3 is the supported operator contract. This file documents the durable
schema and safety primitives that remain in the `vaultops` compatibility layer;
it is not an operator runbook.


The `vaultops` Django app remains a durable internal control-plane foundation.
Its models are projections rather than authority. The package now also contains
selected maintenance endpoints and signed activation/runtime services used by
DataOps v3's temporary bridge. DataOps v3 publishes recovery points directly;
this schema must not be presented as a separate workbench.

## Storage boundary

Application data continues to use the `default` database. Lifecycle
projections, jobs, activation intents, runtime observations, retention plans,
and audit evidence use the `control` database:

```env
DATA_CONTROL_ROOT=/app/data-control
CONTROL_DB_PATH=/app/data-control/control.sqlite3
```

Web and maintenance services must mount the same durable directory at
`/app/data-control`. Both startup paths apply the `control` migrations
explicitly. The web startup path verifies and backs up the control SQLite
database before migration.

The database router prevents `vaultops` tables from being created in the
application database and prevents application tables from being created in the
control database. Control models never reference application models with
foreign keys; actor identity is stored as an ID/name snapshot.

## Authority semantics

Control-database rows are projections, not authority:

- Remote authority remains registration, verified pointer, verified manifest,
  and verified objects.
- Runtime authority remains the observed runtime pointer and readiness
  evidence.
- `ArtifactGeneration.vault_state`, `runtime_state`, and `local_presence` are
  intentionally independent.
- Unknown observations are reported as unknown or degraded, never healthy.

The `status` property on the control-plane generation is a read-only,
one-release compatibility accessor. New code must use the independent fields.

## Legacy data migration

Migration `vaultops.0002_backfill_legacy_lifecycle` copies existing vault-kind
generation, validation, job, and audit records from the application database.
It is idempotent and never mutates the source rows.

Legacy labels are intentionally conservative:

- `active` is projected as a vault candidate with runtime state `unknown`.
- `purged` is projected as retired and does not claim object deletion.
- In-flight legacy vault jobs are stale because their ownership cannot be
  proven.
- Failed legacy jobs receive a typed safe error code; raw exception text is not
  copied.

An audit event records that every legacy `active` label was database-only and
has not been verified against a runtime pointer.

## Job ownership and audit

`VaultJob` claims store only a SHA-256 token hash. Every claim increments a
fencing epoch, and heartbeats require both the token and current epoch. Stale
recovery clears ownership before an explicit requeue.

`VaultAuditEvent` rejects model and queryset updates or deletes. Audit payloads
contain typed reason/error codes and redacted evidence; secrets, lease tokens,
and raw exceptions do not belong in this database.

## Rollback

Before deployment, back up both SQLite databases. To roll back this code:

1. Restore the previous immutable application image.
2. Keep `/app/data-control` mounted and preserve `control.sqlite3`.
3. If a schema rollback is unavoidable, restore the pre-deploy control database
   backup rather than editing lifecycle or audit rows.

Removing the control volume is data loss. Rolling back this foundation never
requires changing a remote authoritative pointer or rewriting an immutable
generation.
