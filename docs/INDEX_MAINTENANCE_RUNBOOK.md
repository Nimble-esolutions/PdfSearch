# Index Maintenance Runbook

Status: Active

Owner: Operations

Last reviewed: 2026-07-27

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
