# Index Maintenance Runbook

Status: Active

Owner: Operations

Last reviewed: 2026-07-28

Use **Vault Operations → Documents & Indexes**. This section changes or checks
local content/search artifacts; it never publishes, promotes, restores, or
moves remote Vault authority.

| Operation | When to use | External embeddings |
|---|---|---|
| Validate Files | Check SQLite rows, media, and stored-artifact availability | No |
| Repair Stored Indexes | Chunks and embeddings are valid but FAISS is missing or corrupt | No |
| Reindex Needed | Search artifacts are missing or invalid | Yes |
| Reindex Selected | Deliberately regenerate selected documents | Yes; typed confirmation required |

All controls remain visible. A disabled control shows a machine-readable reason:
`runtime_read_only`, `bulk_reindex_disabled`, `external_embeddings_disabled`,
`snapshot_in_progress`, or `recovery_point_required`.

The Dashboard only summarizes local maintenance under **Active Work** and
**Needs attention**. Use those links to enter this Workbench. The Dashboard
does not publish Vault generations, inspect remote manifests, or directly
cancel/retry jobs.

## Readiness and local-development posture

The Workbench alert is evidence-driven. Each blocking code has one safe
destination instead of an implicit mutation:

| Code | Review next |
|---|---|
| `profile_unavailable`, `inventory_unavailable`, `inventory_unverified` | Configuration → run a read-only probe or verify authoritative inventory |
| `runtime_observation_unavailable`, `critical_job_unhealthy` | Jobs & Audit → inspect the durable job and checkpoint |
| `runtime_not_ready` | Restore & Activation → review prepared workspace evidence |
| `capacity_degraded` | Documents & Indexes → restore free-space and inode reserve before queueing |

In local development, publication, restore, production activation, and
external embedding calls are disabled or explicitly gated by environment
policy. The Workbench remains useful for inspecting redacted evidence and
preparing a candidate, but no local screen silently changes remote or runtime
authority. Disabled operation cards retain their typed reason so operators can
distinguish policy from an outage.

## Preview and confirmation

1. Select categories and optionally explicit documents.
2. Apply indexed-state, category, subject, keyword, and upload-date filters.
3. Create the server preview. It records exact folder/PDF identities, normalized
   filters, counts, affected folders, estimated work, runtime/source digest,
   external-call posture, state version, expiry, and idempotency key.
4. Review the preview within 15 minutes.
5. For Reindex Selected, type `REINDEX SELECTED`; other operations use ordinary
   confirmation.
6. Queue. The server recalculates selection and authority first; `stale_plan`
   requires a new preview.

The selected preview is addressed by its public plan identifier and displays
matching documents, affected folders, estimated work, expiry, and external
embedding posture. Refreshing or following the post/redirect/get response keeps
that preview in focus without trusting client-supplied document identities.

Repair and reindex require a verified `pre-bulk-maintenance` recovery set.
Failure to create it blocks the job. Duplicate confirmation returns the
original job.

## Execution, retry, and rollback

Repair and reindex snapshot the application database, media, and stored indexes
under `MAINTENANCE_WORKSPACE_ROOT`, then run in a separate process whose
database and artifact paths point only at that workspace. Document extraction
and embeddings checkpoint per item. A retry does not recompute completed items.
After every selected document in one folder is finished, the worker performs
one final temporary FAISS build for that folder.
An item failure restores the document from `processing` to a valid prior
lifecycle state. Failure codes and append-only audit events remain visible with
job progress; superadmins can cancel active work or retry failed jobs.
The Workbench calculates allowed actions from the current job lifecycle and
binds each action to a job state version. A stale browser submission is rejected
instead of applying an action to a newer job state.

A signed/read-only active runtime is never an eligible mutation target
(`runtime_read_only`). Maintenance must operate from a mutable source snapshot
and produce a derived, validated candidate. Activation remains a separate
signed confirmation and rollback workflow. Keep the active and previous runtime
unchanged until SQLite, media references, embedding dimensions/counts, FAISS
coherence, and derived hashes validate.

Successful reindexing makes the prior Vault generation stale. Publish and
verify a new immutable Vault candidate before remote authority reflects the
new searchable artifacts. Publication and promotion are separate explicit
operations.

## Local artifact cleanup

Preview cleanup without changing disk state:

```bash
python manage.py artifact_cleanup plan
```

The plan inventories recovery sets, source snapshots, quarantine, maintenance
workspaces, and runtime generations. Active/previous runtimes, incident holds,
activation references, resumable checkpoints, and activation-ready maintenance
candidates are protected. Apply only the current plan identifier:

```bash
python manage.py artifact_cleanup apply --confirm <plan-id>
```

Application is rejected when the inventory changes or the proposed deletion
exceeds the separately approved 20 GiB boundary. The command never follows
symlinks or removes paths outside the declared artifact roots.

The Workbench reports local recovery health, verified Vault generation count,
maintenance candidate state, the latest persisted restore-rehearsal result,
held/prunable bytes, and the current byte/inode reserve independently. Restore,
maintenance workspace creation, and activation use phase-aware capacity plans;
an insufficient reserve blocks the risky operation before mutation.
