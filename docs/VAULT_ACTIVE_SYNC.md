# Vault Active Sync

Vault Active Sync is the publication control plane for coherent application
data generations. It is deliberately disabled by default and is separate from
runtime activation.

## Safety contract

An Active Sync run follows this authority chain:

1. A durable source mutation epoch identifies the bounded set of changes.
2. A cooperative barrier drains web and maintenance mutations.
3. SQLite is copied with the online backup API and checked for integrity.
4. Media, PDF cache, FAISS, Chroma, and custody-only static files are reconciled
   into an immutable local snapshot.
5. Every snapshot artifact is re-hashed before upload.
6. Content-addressed objects are conditionally created or verified for reuse.
7. An immutable dataset-scoped manifest is conditionally published and
   re-read.
8. The result is a candidate generation.
9. Pointer promotion is a separate, confirmation-gated CAS operation.

Publication never changes the authoritative pointer. A cancellation after
manifest publication preserves the immutable candidate rather than claiming
that publication was undone.

## Durable control state

The `vaultops` models are routed to the stable control database configured by
`CONTROL_DB_PATH`. Active Sync adds:

- `SourceMutationState` for the current epoch, active mutation count, and
  barrier ownership;
- `MutationJournalEntry` for changed-path evidence;
- `SourceSnapshot` for snapshot identity, digest, evidence, and failure codes;
- `VaultJobStep` upload checkpoints; and
- `SyncPolicy` scheduling and completed-epoch projections.

Application-data activation must not replace this database.

## Mutation barrier

Relevant Django write routes and maintenance jobs enter a mutation scope.
FAISS-building functions also enter a scope, including deferred
`transaction.on_commit` index promotion. Nested scopes count as one source
mutation.

Audited unavailable-media transitions enter their own service-level scope so
the contract also applies to supported management-shell and recovery callers
that do not pass through request middleware. A successful
`media_unavailable`, `media_evidence_bound`, or `media_restored` transition
advances the epoch only after its row change and audit event commit. An
idempotent no-op, validation failure, or rolled-back audit advances nothing.
If request middleware already owns a scope, the nested service scope shares
its outcome and the committed transition still advances exactly once.
These services reject entry from a caller-owned transaction on the application
database with `media_transition_outer_atomic_unsupported`. This fail-closed
boundary prevents an outer rollback from occurring after the independently
durable control epoch advances. `ATOMIC_REQUESTS` must remain disabled for the
application database; callers must invoke each supported media transition as
the outermost application-database unit of work.

Snapshot finalization changes the barrier from `open` to `requested`, waits
for active scopes to drain, then changes it to `active`. New writes receive
`snapshot_barrier_active`. The barrier is released in a `finally` block and
only by its owning job.

Direct filesystem changes outside these cooperative paths are not accepted as
proof of consistency. A final source metadata scan detects such changes and
fails with `snapshot_untracked_source_mutation`.

## Publication and resume

Each job binds to:

- deployment and dataset identity;
- an environment-managed vault profile fingerprint;
- a source snapshot ID and digest; and
- a stable generation ID assigned before upload.

On retry, completed objects are not trusted merely because a checkpoint says
they exist. Their size and SHA-256 metadata are checked again. Upload requests
also include `Content-MD5`, so an object store rejects bytes that differ from
the verified local snapshot. Manifest creation uses `If-None-Match: *` and a
retry must reproduce the same canonical manifest bytes.

The retry POST is also a control-plane transaction. It locks the job, checks
the submitted state version, and records a durable idempotency receipt.
Replaying the same key and request returns the original receipt; concurrent
submissions increment the retry counter and append `job_requeued` exactly once.
Reusing a key for another actor or state version fails closed.

The operator interface distinguishes two retry modes. A job with a finalized
snapshot resumes its verified object-upload checkpoint. A job that failed
before snapshot finalization never calls that a checkpoint resume: its failed
or hard-kill `.incomplete` workspace is removed and the worker creates a fresh
snapshot. Failed snapshot rows and bounded cleanup evidence remain in the
control database; partial files do not remain publishable.

Writer ownership is renewable and fenced by epoch. Release re-reads the writer
record, verifies the token hash and epoch, and expires it with `If-Match`.
It cannot delete or release a successor.

## Scheduling

Supported modes are:

- `disabled`: no manual or scheduled jobs;
- `manual`: an explicit caller queues a job;
- `scheduled`: evaluate at the configured interval;
- `continuous_coalesced`: wait for the quiet period and publish one job for the
  latest mutation epoch.

Legacy `event-driven` and `hybrid` values map to
`continuous_coalesced` for one release and emit a startup warning.

The maintenance scheduler is active only when both
`MAINTENANCE_SCHEDULER_ENABLED=1` and `VAULT_SYNC_ENABLED=1`. Repeated triggers
for the same deployment, profile fingerprint, and epoch return the same
idempotent job.

## Configuration

The minimal disabled posture is:

```env
VAULT_SYNC_ENABLED=0
VAULT_SYNC_MODE=manual
VAULT_SYNC_PROMOTION_MODE=manual
DATA_CONTROL_ROOT=/app/data-control
CONTROL_DB_PATH=/app/data-control/control.sqlite3
VAULT_SNAPSHOT_ROOT=/app/data-control/snapshots
```

Production publication additionally requires the existing authoritative
writer identity and artifact-vault settings. Production dataset identities
are locked to `ai-sahakar-prod`. Automatic promotion is rejected in
production.

The initial release does not expose browser-entered credentials, runtime
activation, or garbage collection. Those remain independently feature-gated.

## Failure and recovery

Jobs persist typed `safe_error_code` values rather than raw exception text.
Worker claims use a token hash, fencing epoch, and heartbeat. A stale claim is
recovered only after the configured heartbeat timeout and receives a new
fencing epoch.

Typical fail-closed outcomes include:

- `mutation_barrier_drain_timeout`;
- `snapshot_untracked_source_mutation`;
- `snapshot_artifact_digest_mismatch`;
- `profile_fingerprint_changed`;
- `conditional_operations_unsupported`;
- `registration_identity_mismatch`;
- `published_object_digest_mismatch`; and
- `fresh_validation_required`.

An interrupted pre-manifest upload can retry the same job and generation.
A manifest already published remains a candidate. A pointer already promoted
requires a separately confirmed compensating promotion.

## Initial rollout

1. Deploy the shared control volume and migrations with Active Sync disabled.
2. Verify the control database and snapshot root survive container restart.
3. Probe the locked profile and conditional-operation capability read-only.
4. Enable mutation tracking and run a manual disposable publication.
5. Verify object metadata, manifest digest, and candidate projection.
6. Exercise retry, worker death, and pointer CAS conflict.
7. Enable manual production publication only after snapshot evidence is
   reviewed.
8. Keep production auto-promotion, runtime activation, and garbage collection
   disabled.
