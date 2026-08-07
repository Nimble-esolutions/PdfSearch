Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: docs/dataops/V3_ARCHITECTURE.md
Supersedes: profile-driven v2 rollout instructions in this file

# Data Operations v3 rollout

This is the reusable rollout procedure. Exact deployments, generations,
counts, digests, receipts, and current decisions belong in
[HANDOFF.md](../HANDOFF.md); dated status documents remain historical evidence.

## Current environment boundary

- Legacy `https://www.ai-sahakar.net` remains authoritative production and must
  not be changed by a 2026 stage operation.
- `https://2026.ai-sahakar.net` is the active non-production stage/rehearsal
  host.
- The future 2026 production project is not deployed. Production activation is
  currently rejected before runtime-pointer mutation.
- The stage public-authentication exception was approved for its rehearsal.
  That approval does not authorize production cutover, DNS changes, credential
  disclosure, or unsandboxed email/webhook/payment effects.

## Operator rollout

1. **Identify the boundary.** Record the target host, environment/dataset ID,
   exact running web and maintenance image digests, data/control volume
   identities, signed active/previous pointer evidence, and rollback authority.
2. **Preserve recoverability.** Before a persistent-data change, retain a
   recoverable paired-volume snapshot or an accepted DataOps recovery point and
   record its receipt. Never use the active or legacy volume as a restore
   target.
3. **Configure one owned connection.** DataOps v3 uses the environment's
   dataset identity and one primary RustFS connection. Bootstrap only endpoint,
   bucket, region, prefix, and credential reference; resolve secret values in
   the worker. Do not configure source/destination profiles, clone switches, or
   same-dataset exceptions as operator choices.
4. **Prove storage capability.** Let DataOps probe read, write, conditional
   write, metadata, and ownership semantics before publication. A failed or
   stale capability result blocks the operation without creating a trusted
   recovery point.
5. **Choose one intent.** Use **Back up**, **Restore**, **Import**, or **Test
   recovery**. Preview is read-only and binds the exact plan digest and, when
   required, confirmation token. Start rejects changed plans, stale tokens,
   secret fields, and unsupported execution routes.
6. **Import legacy or foreign data safely.** For an existing legacy/v2/v3
   object-store source, select the exact source and let DataOps verify and
   rebind foreign lineage into the owned dataset. A mounted legacy volume must
   first be captured by the reviewed read-only migration tool; direct
   mounted-volume execution is not exposed by the v3 start API.
7. **Prepare an isolated candidate.** Restore into a unique quarantine/runtime
   generation. Verify manifest signature and object digests, SQLite integrity
   and foreign keys, migrations, media paths, embedding dimension, and derived
   artifact compatibility. Rebuild indexes in quarantine when reuse is unsafe.
8. **Exercise representative behavior.** Reconcile database/media/index state,
   require the configured indexing policy, and test representative English,
   Marathi, and Hindi retrieval, source links, and PDF access. The exact test
   suite and workflow at the release revision are the exhaustive inventory.
9. **Activate stage separately.** Only an explicitly confirmed stage plan may
   schedule the signed activation bridge. The intent binds the exact image,
   generation, manifest, and plan; supervisors coordinate web/maintenance,
   preserve the previous pointer, apply a compare-and-swap switch, and require
   exact `/readyz` evidence. Failure restores the previous signed pointer and
   records a signed rollback result.
10. **Prove recovery without activation.** Restore a selected recovery point to
    disposable data/control roots. `Test recovery` must never import into or
    activate the live runtime. Retain failed targets until evidence review.
11. **Record the outcome.** Capture plan/configuration digests, source lineage,
    recovery point and manifest digests, operation/activation receipts, image
    digests, readiness result, and rollback reference without secrets or
    document contents.

## Compatibility boundary

DataOps v3 is the sole supported operator UI and lifecycle contract. The
installed `vaultops` package still owns durable compatibility/control records,
selected authenticated maintenance endpoints, and signed activation/runtime
primitives. Legacy profile, sync, retention, GC, and mutation APIs remain
default-off code-removal debt; they are neither a second workbench nor fully
removed. Remove or reroute them only in a separately reviewed impact-analysis
change with migration and runtime-supervisor coverage.
