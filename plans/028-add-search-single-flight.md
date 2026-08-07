# Plan 028: Coalesce identical concurrent search work across web workers

> **Executor instructions**: Treat lock failure as a performance degradation,
> not search downtime. Run concurrency and cache-outage tests before publication.
>
> **Drift check (run first)**:
> `git diff --stat f090650..HEAD -- flowdocs/core/utils.py flowdocs/core/test_search_performance.py flowdocs/flowdocs/settings.py docker-compose.yml`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH — distributed coordination can deadlock or amplify failures
- **Depends on**: Plan 025, Plan 027
- **Category**: performance, architecture
- **Planned at**: commit `f090650`, 2026-08-07

## Why this matters

Exact-result, query-embedding, and answer caches use ordinary get-then-compute.
Concurrent identical misses can issue duplicate paid provider calls in every
Gunicorn worker. Redis already exists for cache and rate limiting, so a bounded
token-owned single-flight layer can coalesce misses without introducing a queue
or a new service.

## Current state

- `flowdocs/core/utils.py:1154-1193` gets then computes query embeddings.
- `flowdocs/core/utils.py:1469-1494` gets then validates exact results.
- `flowdocs/core/utils.py:1669-1677` gets then computes answer-cache misses.
- The process-local corpus lock does not coordinate workers.
- Search utility-cache errors intentionally fail open; the anonymous rate
  limiter remains separately fail-closed.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused tests | `env SECRET_KEY=test ALLOW_INSECURE_DEFAULTS=1 APP_ENV=test PDFSEARCH_TEST_EMBEDDINGS=1 .venv/bin/python manage.py test core.test_search_performance` | exit 0 |
| Redis integration | run the repository Compose smoke with its documented test image | concurrent duplicate provider calls = 1 |
| Cache outage | focused test with cache operations raising | search still follows authoritative path |

## Scope

**In scope**: one reusable Redis/cache-backed single-flight helper, exact-result
and provider-cache miss integration, deterministic concurrency tests, telemetry,
and documentation.

**Out of scope**: durable queue semantics, Celery, Redis as custody/audit store,
corpus distribution, new operator flags, or changing cache isolation keys.

## Steps

1. Implement token-owned acquisition with atomic add, short lease, bounded
   follower wait with jitter, cache recheck, and compare-owner safe release.
   Lease and wait must be fixed code constants subordinate to Plan 025's deadline.
2. Integrate one layer at a time: query embedding, answer cache, then exact
   result. Preserve provider/release/language/generation/authorization key scope.
3. On Redis error, lease expiry, owner crash, or follower timeout, continue via
   the existing authoritative computation. Never wait indefinitely.
4. Add multi-thread/process-shaped tests for one owner, followers receiving the
   cache result, owner exception, expired lease, token mismatch on release,
   cache outage, and no cross-scope coalescing.

## Done criteria

- [ ] Identical concurrent misses make one provider call per correctly scoped key.
- [ ] Wait plus provider work cannot exceed Plan 025's total deadline.
- [ ] Only the owner token can release a lease.
- [ ] Redis failure reduces speed, not availability or authorization safety.
- [ ] Concurrency and Compose integration gates pass.

## STOP conditions

- The cache backend lacks an atomic primitive and safe owner-checked release.
- Any lock key omits provider, model, language, runtime, or authorization scope.
- A test requires sleeps long enough to make CI timing-dependent; use controllable clocks/events.

## Maintenance notes

Do not generalize this into a task queue. Revisit only when measured concurrent
misses justify it; retain simple cache hits as the fastest path.
