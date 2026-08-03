Status: Active implementation gate
Audience: developers, reviewers, release and operations owners
Owner: FlowDocs maintainers
Last verified: 2026-08-03

# Data Operations v3 impact analysis

This analysis is required before DataOps v3 changes are deployed. It separates
operator simplification from the low-level protections that must remain intact.

## Current evidence

The 2026 stage control database has no DataOps v2 profiles, operations, or
recovery points. Its older control plane has records from attempted recovery
work, but stage still has no signed active runtime pointer. The verified
production-to-stage clone exists remotely and the active application remains
unchanged after safely failed restore attempts.

The running web container currently receives more than one hundred lifecycle
environment keys across DataOps, VaultOps, and stage exceptions. That is an
operability defect: the safety decision is distributed across Compose, web,
maintenance, profile records, restore code, compatibility code, and activation
flags.

This evidence permits an aggressive v3 simplification on stage, but not an
untested deletion of the primitives that protect data and runtime activation.

Real local certification on 2026-08-03 has now proved the production-v2
read-only importer, destination-owned signed v3 publication, idempotent object
reuse, isolated current-schema migration, local English/Marathi/Hindi OCR,
selective embedding repair, complete FAISS reconstruction, and candidate
validation against all 242 documents. The active local and production runtimes
were not changed. Signed activation, one real stage backup, and isolated stage
round-trip restore remain release gates; compatibility surfaces must stay
read-only until those gates pass.

The rehearsal also proved two failure boundaries that are now enforced in
code. Candidate preflight rejects a configured embedding model whose default
dimension differs from stored vectors before OCR, reindex, or index quarantine
begins. Worker success state and the corresponding audit receipt share one
control-database transaction; `activation=null` is valid for a non-activating
rehearsal, while an audit write failure rolls the operation back to running.

## Keep, adapt, replace, remove

| Existing area | Decision | Reason and required proof |
| --- | --- | --- |
| DataOps operation/audit/recovery records | Adapt | Useful v3 history, idempotency and evidence boundary |
| DataOps profile selectors and roles | Remove after migration | Operators should not route ordinary operations manually |
| DataOps clone/rebind UI and flag | Remove after v3 import proof | Rebind becomes an automatic foreign-dataset import transition |
| DataOps mirror/job UI | Remove from normal product | Not part of backup/restore/import contract |
| Stable legacy two-scan importer | Keep and adapt | Required to prevent a mutating source from becoming trusted |
| Content-addressed objects and immutable manifests | Keep and version | Foundation of deduplicated backup and integrity verification |
| Conditional registration and CAS pointers | Keep | Prevent collisions, stale workers and concurrent pointer loss |
| Quarantine/new-generation restore | Keep | Failed restore must not overwrite active data |
| SQLite integrity, FK and migration rehearsal | Keep | Required before candidate activation |
| Signed activation and rollback supervisor | Adapt behind DataOps | Valuable safety primitive; VaultOps UI/API does not return |
| VaultOps profile/workbench/readiness surfaces | Retire after v3 proof | Duplicate product and configuration system |
| Environment-direction special cases | Replace | Dataset provenance and intent determine routes; environment sets gate strength |
| Repacked-release and same-dataset flags | Remove | Compatibility and ownership become deterministic plan decisions |

## Code boundaries affected

| Boundary | Expected change | Main regression risk | Required tests |
| --- | --- | --- | --- |
| `dataops/lifecycle.py` | New pure v3 planner | Incorrect route or missing gate | Full scenario decision table and deterministic digest |
| `dataops/package.py` | Add v3 manifest/provenance | Old manifest incompatibility | v2 import fixtures, canonical digest, secret exclusion |
| DataOps models/migrations | Connection/policy and v3 operation metadata | Control DB migration/rollback | Forward migration, empty-stage migration, fixture import |
| DataOps worker/executor | One resumable state machine | Duplicate/stale publication | Lease loss, restart, idempotent retry, process death |
| Embedding/index contract | Preflight stored and configured dimensions | Mixed vector spaces or runtime query mismatch | Known model dimensions, model change, mixed vectors, deterministic provider |
| DataOps views/templates/API | Three primary actions and plan preview | Authorization or unsafe hidden defaults | Superadmin, CSRF, rejected secret fields, browser workflow |
| VaultOps services | Temporary internal adapters only | Coupling old flags into v3 | Adapter contract tests with explicit capability inputs |
| Settings/Compose/env examples | Remove lifecycle key explosion | Web/maintenance drift | Effective Compose parity and redacted config digest |
| Readiness | Separate application and DataOps posture | Website unavailable because backup is unconfigured | `/readyz` application status plus action-specific DataOps status |
| Docs/runbooks | Replace v2/VaultOps operator language | Operators use stale instructions | Documentation link/key/contract checks |

## Data safety impact

No v3 path may:

- write to a legacy source;
- use the active runtime directory as a restore target;
- mutate an immutable source generation;
- publish a recovery point before every required object verifies;
- activate without the exact generation and manifest digest;
- let a stale lease holder publish or swap a pointer;
- delete failed targets before evidence review;
- place credentials or document contents in audit logs.

The previous runtime pointer remains authoritative until signed activation
succeeds. A failure during inspection, transfer, verification, migration,
reindex, smoke tests, or confirmation leaves it unchanged.

## Availability impact

| Operation | Web reads | Writes | Expected interruption |
| --- | --- | --- | --- |
| Backup | Available | Available | None; retry if snapshot cannot converge |
| Import preparation | Available | Available | None |
| Restore preparation/reindex/test | Available | Available | None; isolated generation |
| Stage activation | Usually available | Briefly gated if required | Short readiness interruption accepted |
| Production activation | Available where safe | Brief mutation barrier | Bounded write pause; no production deployment in this rollout |
| Isolated recovery test | Available | Available | None; disposable volumes only |

## Security impact

- Production-derived authentication data on public stage is an approved
  exception for this rehearsal; the owner, monitoring, incident response, and
  rollback authority still belong in redacted activation evidence.
- Original PDFs stay on the server/storage boundary. Local OCR remains the
  extraction path; extracted text may follow the existing embedding policy.
- Runtime credentials are least-privilege secret references. Root RustFS
  credentials remain provisioning-only.
- Plans and receipts contain dataset IDs, digests, counts, capability results,
  and credential aliases only.
- The API rejects fields named `access_key`, `secret_key`, `password`, `token`,
  or equivalent secret-bearing input.

## Storage and cost impact

Every recovery point is logically full. Physical upload is incremental because
unchanged blobs are reused. A changed SQLite database initially produces one
new database blob. Derived indexes may be omitted or rebuilt when incompatible.

Retention must be manifest-aware mark-and-sweep. Bucket age policies must not
delete shared blobs because an old blob may still be referenced by a retained
manifest. Garbage collection requires an immutable plan, grace period, second
mark pass, and receipt.

## Compatibility impact

| Source | v3 behavior |
| --- | --- |
| Native v3, same dataset | Restore directly through quarantine |
| Native v3, foreign dataset | Copy missing blobs, rebind ownership, preserve lineage |
| DataOps v2 generation | Read-only compatibility import to a new v3 point |
| Existing VaultOps generation | Read-only compatibility import to a new v3 point |
| Mounted legacy app | Stable two-scan canonical import |
| Flat/unknown S3 objects | Inventory and refuse ambiguity; never guess active data |

V2 objects and pointers are never rewritten. During transition, v2 and
VaultOps sources are read-only; only one engine may publish v3 recovery points.

## Configuration impact

Mandatory lifecycle settings converge on `APP_ENV`, `DEPLOYMENT_ID`,
`DATASET_ID`, and `DATAOPS_ENABLED`. Owned connection metadata and policy live
in one control-plane record or optional bootstrap document. Secrets are
provider references. Paths derive from existing data/control roots.

Removal candidates after compatibility import and stage proof include:

- `DATAOPS_BACKUP_PROFILE` and `DATAOPS_RESTORE_PROFILE`;
- all source/destination profile selectors;
- exploded `DATAOPS_PROFILE_*` variables and profile manifest;
- clone/rebind, same-dataset restore, repack mismatch, and stage auto-activate
  switches;
- ordinary retry, interval, concurrency, and path tuning from environment;
- public VaultOps enablement, profile configuration, and mutation flags.

Do not remove a key merely because it is undesirable. First trace its callers,
provide a v3 replacement/default, update web and maintenance together, run
Compose parity checks, and prove the affected scenario.

## Rollout and rollback

1. Add and test the pure v3 planner and manifest contract.
2. Add read-only source inspection and v2/VaultOps compatibility import.
3. Add v3 backup publication behind `DATAOPS_ENABLED`, with v2/VaultOps
   publication disabled.
4. Add quarantine restore and signed activation adapters under the DataOps
   operation state machine.
5. Prove all local unit/integration/process-death cases.
6. Build a disposable stage candidate from the feature branch; do not push or
   change active stage yet.
7. On live stage, import the fresh production-v2 generation into v3, restore
   all 242 PDFs, migrate/reindex, run multilingual checks, and sign activation.
8. Publish a real stage recovery point and restore it into isolated disposable
   data/control volumes.
9. Confirm exact manifest, generation, document/media counts, search behavior,
   receipts, and readiness evidence.
10. Only then remove old UI/routes/selectors in separate reviewable commits.

Rollback before activation is cancellation: active state never changed.
Rollback after activation uses the recorded previous image-generation pair and
signed compare-and-swap evidence. V2 objects, old control snapshots, failed
candidates, and audit evidence remain until review is complete.

## Acceptance matrix

- First and later backups publish complete manifests; later backups reuse
  unchanged objects.
- Source mutation, incomplete objects, digest mismatch, permissions failure,
  collision, process death, and lease loss cannot publish a trusted point.
- Same-dataset restore needs no exception flag.
- Foreign-dataset restore automatically becomes import/rebind.
- Legacy source remains read-only and requires a stable two-scan result.
- Migration/reindex/search failure leaves the active pointer unchanged.
- Unknown, changed, or mixed embedding dimensions fail before candidate mutation; model changes require a reviewed full reindex.
- A success receipt and its audit event commit atomically; a missing activation result is not an error.
- Production restore planning includes a pre-restore backup and mutation
  barrier even though production execution is outside this rollout.
- Web and maintenance share the same secret-free compiled configuration digest.
- Stage activates 242 documents with signed generation/manifest evidence.
- Stage backup restores successfully into disposable volumes.
- Legacy production remains unchanged throughout.
