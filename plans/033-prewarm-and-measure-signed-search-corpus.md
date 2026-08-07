# Plan 033: Prewarm and measure the signed search corpus

> **Executor instructions**: Implement only after Plan 027 exposes cold-corpus
> time and worker memory. Prewarming is an optimization, never a new readiness
> requirement that can make the conservative search path unavailable.
>
> **Drift check (run first)**:
> `git diff --stat 796bdd5..HEAD -- flowdocs/core/utils.py flowdocs/core/views.py flowdocs/core/metrics.py start.sh worker-entrypoint.sh scripts/ci flowdocs/core/test_search_performance.py`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED — worker startup, memory, and signed-runtime identity interact
- **Depends on**: 027
- **Category**: performance, operations
- **Planned at**: commit `796bdd5`, 2026-08-07

## Why this matters

The first request handled by each Gunicorn worker currently performs two
database passes and constructs its own normalized matrix. This can create cold
tail latency and multiply memory by worker count. The first remedy is bounded
prewarm with evidence—not a vector database or corpus microservice.

## Steps

1. Measure cold-build duration, admitted vectors/bytes, worker RSS, fallback
   reason, and cold-request frequency without content or identity labels.
2. Add an idempotent generation/manifest/mutation-epoch-bound prewarm function.
   Invoke it after worker initialization without blocking normal startup beyond
   a fixed short budget; failure preserves safe lazy/conservative behavior.
3. Measure stage cold p95 and aggregate worker RSS before/after. Keep the
   process-local corpus if it meets the release budget.
4. Only if duplicate RSS or load time remains material, spike one immutable
   digest-verified `.npy` float32 artifact loaded with `mmap_mode="r"` and
   `allow_pickle=False`. Bind cleanup to generation retirement; never mutate it.
5. Reject the mmap spike unless tree/digest identity, crash recovery, rollback,
   and conservative fallback all pass with lower measured cost.

## Done criteria

- [ ] Cold and warm p50/p95/p99 plus aggregate worker RSS are recorded.
- [ ] Prewarm cannot change authorization, ranking, source validation, or readiness truth.
- [ ] Startup/prewarm failure retains the current safe fallback.
- [ ] No new service, queue, database, environment variable, or operator step exists.
- [ ] Memory mapping is adopted only with measured benefit and immutable lifecycle proof.

## STOP conditions

- Stage data does not show cold corpus work as a material latency contributor.
- Prewarm requires extending startup/readiness timeouts or weakening signed identity.
- A shared artifact cannot be proven immutable and rollback-safe.
