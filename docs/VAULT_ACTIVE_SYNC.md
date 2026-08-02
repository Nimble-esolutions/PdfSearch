# Vault Active Sync
## Current-state pointer

For the verified state as of 2026-08-02, use [STATUS-2026-08-02.md](STATUS-2026-08-02.md).
Earlier dated sections in this page remain historical evidence and must not
be used as current deployment state without reconciling them to that record.


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
5. A stale copied FAISS folder index is derived again inside the isolated
   snapshot from the frozen database's retained embeddings.
6. Every snapshot artifact is re-hashed before upload.
7. Content-addressed objects are conditionally created or verified for reuse.
8. An immutable dataset-scoped manifest is conditionally published and
   re-read.
9. The result is a candidate generation.
10. Pointer promotion is a separate, confirmation-gated CAS operation.

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

Candidate FAISS reconciliation never changes the live database or live index
tree and never calls an embedding provider. Searchable rows are read from the
frozen SQLite snapshot in deterministic folder, document, and chunk order.
Every stored chunk and embedding must be complete, finite, non-zero, and
dimensionally consistent. A coherent copied folder index is retained
byte-for-byte only after its supported inner-product index type, dimensions,
count, vector values, and deterministic vector order match the normalized
frozen-database batches byte-for-byte as canonical float32 values; a
near-tolerance numeric match is not accepted. A missing, unreadable,
unsupported, reordered, or
stale folder index is atomically derived only under the incomplete snapshot
workspace. Indexes for folders with no searchable rows are omitted from the
candidate. A row contributes to candidate FAISS evidence only when its
lifecycle is searchable and its persisted `indexed` flag is true. Uploaded
documents that still need indexing remain in the frozen database, inventory,
and index-debt counts but do not supply vectors. An indexed row with empty,
malformed, non-finite, or dimensionally inconsistent stored evidence still
fails closed. Legacy schemas without an `indexed` column retain the lifecycle
filter and strict stored-evidence validation. SQL row and source-cell sizes
are bounded before JSON parsing;
copied index file size is bounded before FAISS loads it, and projected
per-document chunks, dimensions, total vectors, and resident vector bytes are
checked before allocation or index addition. Verification and rebuilding use
separate passes so a copied index and its replacement are not resident
together. Per-PDF remaining vector and byte budgets are checked before NumPy
vectors are accumulated or stacked, and the rebuilt in-memory index is
released before the written candidate is loaded for verification.
Cancellation is checked around parsing,
materialization, index addition, and writing. Cancellation or derivation
failure leaves no published candidate and cannot modify source artifacts.

The default per-document JSON-cell ceiling is 128 MiB. This admits an
observed 85,794,946-byte retained-embedding cell while
remaining below the independent 512 MiB normalized-vector and 1 GiB aggregate
source ceilings. The cell ceiling is not a worker-memory estimate: during one
PDF batch the source JSON strings, decoded Python values, float64
normalization workspace, and float32 batch can overlap. Operators must size
worker memory for that transient expansion and lower this ceiling when the
deployment has a smaller verified memory budget.

Snapshot evidence, the immutable generation manifest, and the publication
validation record bind each folder's copied or rebuilt disposition, vector
count, dimensions, and digest. Source folder identifiers and digests are also
bound so the removed set must be exactly the source set absent from the
candidate. A canonical digest of those pre-reconciliation source records is
stored independently on the control-database `SourceSnapshot`; publication
requires workspace evidence to match that trusted digest. Publication also
rechecks computed PDF, vector, and byte totals against configured limits. This
evidence is used by the existing restore,
activation, runtime, and certification gates; it does not weaken their
independent count and digest checks.

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

The operator interface distinguishes three retry modes. A sync job resumes a
verified snapshot checkpoint only when the finalized workspace, trusted
control-plane evidence, immutable configuration sidecar, and completed snapshot
step agree on deployment identity, snapshot identity, digest, epoch, and the
current safety-setting fingerprint. Legacy, missing, malformed, forged, or
configuration-mismatched evidence fails closed to a fresh snapshot; there is
no operator override. Child evidence is opened relative to an already-opened,
non-symlink workspace directory so a path replacement cannot redirect
verification. The small `snapshot-configuration.json` sidecar and the
`checkpoint_binding` in authenticated `snapshot-evidence.json` must agree
exactly. Primary evidence is size-bounded, parsed as one complete JSON object,
and required to use the canonical encoding produced by the snapshot writer.
Its digest and semantic binding are both derived from that same anchored read;
leading, trailing, non-JSON numeric constants, or noncanonical bytes make the
checkpoint ineligible.
An ineligible workspace receives a durable cleanup intent and the worker
creates a fresh snapshot. Reclamation runs only after the retry
transaction commits, after a grace period and fenced-owner recheck, and within
configured item, byte, and time bounds. Failed snapshot rows and cleanup
evidence remain in the control database; partial files never become publishable.
The reclaimer scans a separately bounded candidate window, backs off live
owners, and quarantines invalid paths so an old blocked row cannot starve later
eligible cleanup.
Non-sync operations use neutral retry guidance unless that operation separately
proves a reusable durable checkpoint.

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
VAULT_SNAPSHOT_FAISS_MAX_VECTORS=1000000
VAULT_SNAPSHOT_FAISS_MAX_DIMENSIONS=4096
VAULT_SNAPSHOT_FAISS_MAX_BYTES=536870912
VAULT_SNAPSHOT_FAISS_MAX_SOURCE_BYTES=1073741824
VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES=134217728
VAULT_SNAPSHOT_FAISS_MAX_PDFS=100000
VAULT_SNAPSHOT_FAISS_MAX_CHUNKS_PER_PDF=100000
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
A retry after a failed snapshot creates a fresh snapshot identity and derives
candidate indexes again from the then-current frozen source. A retry after
snapshot finalization reuses that immutable snapshot only when its bounded,
non-secret configuration fingerprint still matches every inventory, FAISS, and
publication safety input; otherwise it creates a fresh snapshot first.
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
