# Bounded automatic recovery contract

Automatic recovery is deliberately narrow. It keeps ordinary operations
hands-off without hiding evidence or taking an irreversible production action.

## Allowed actions

- refresh a stale health/inventory observation;
- retry transient network/S3 failures with bounded exponential backoff;
- recover an orphaned maintenance job;
- repair a derived index from valid source embeddings;
- reindex affected documents, subject to per-run and per-day budgets;
- remove abandoned temporary workspaces after their grace period.

Every action emits an append-only audit event with the operation ID, reason,
attempt number, budget before/after, and resulting evidence pointer.

## Approval gates

Automatic recovery must stop and request an operator decision for missing source
objects, database integrity failures, schema or release identity mismatch,
credential errors, an active restore, or production activation. A staging
restore can auto-activate only when integrity, migration rehearsal, media/index
reconciliation, and search smoke checks all pass.

## Budget semantics

Budgets are hard limits, not advisory counters. A run cannot enqueue more than
`DATAOPS_AUTO_HEAL_REINDEX_PER_RUN` documents and the reconciler cannot exceed
`DATAOPS_AUTO_HEAL_REINDEX_PER_DAY` across all runs for the UTC day. A denied
attempt is recorded as `budget_exhausted` and is retried on the next scheduled
window, never by an unbounded loop.
