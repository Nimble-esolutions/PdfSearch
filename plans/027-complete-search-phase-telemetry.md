# Plan 027: Make every material search phase observable without content logging

> **Executor instructions**: Preserve the current secret-free telemetry rule.
> Metrics diagnose phases; they must never capture query or document content.
>
> **Drift check (run first)**:
> `git diff --stat f090650..HEAD -- flowdocs/core/utils.py flowdocs/core/views.py flowdocs/core/test_search_performance.py docs/releases/2026-08-07-search-answer-latency.md`

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: performance, dx
- **Planned at**: commit `f090650`, 2026-08-07

## Why this matters

Current logs expose embedding, retrieval, answer, total time, and cache/corpus
counters, but corpus loading, authorization, cache validation, final evidence
validation, and provider waiting are folded into broader phases. A stage canary
cannot identify the next bottleneck reliably. Add bounded numeric evidence,
not another dashboard or configuration layer.

## Current state

- `flowdocs/core/utils.py:1437-1451` initializes diagnostics counters.
- `flowdocs/core/utils.py:1460-1520` performs cache and corpus work before
  `embedding_ms` begins.
- `flowdocs/core/utils.py:1597-1602` performs final evidence validation without
  its own timing.
- `flowdocs/core/views.py:476-500` owns the structured completion log.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused tests | `env SECRET_KEY=test ALLOW_INSECURE_DEFAULTS=1 APP_ENV=test PDFSEARCH_TEST_EMBEDDINGS=1 .venv/bin/python manage.py test core.test_search_performance core.test_public_search_routing` | exit 0 |
| Operator language | `.venv/bin/python scripts/ci/check_operator_language.py` | exit 0 |
| Docs contract | `.venv/bin/python scripts/ci/docs_contract.py` | exit 0 |

## Scope

**In scope**: numeric diagnostics for `cache_lookup_ms`, `corpus_load_ms`,
`authorization_ms`, `provider_wait_ms`, `final_validation_ms`, `failure_phase`,
and public view (`classic`/`workbench`) if available server-side; structured log
tests and release/canary documentation.

**Out of scope**: query/document logging, tracing vendor adoption, dashboards,
new environment variables, persisted telemetry tables, or changing search work.

## Steps

1. Add timers at the smallest existing boundaries; do not duplicate work to
   measure it. Use integer milliseconds and a small allowlisted failure-phase enum.
2. Extend `_log_search_completed` with allowlisted numeric/boolean fields and
   the selected public view. Omit absent phases or emit zero consistently.
3. Add tests that patch the logger and assert field names/types, cache-hit early
   return coverage, fallback coverage, provider failure, and absence of query,
   answer, exception, credential, and document strings.
4. Update the stage canary table to say which phase indicates which remediation.

## Done criteria

- [ ] Every material pre-provider and final-validation phase has numeric timing.
- [ ] Cache-hit and failure paths produce internally consistent diagnostics.
- [ ] No content or raw exception reaches logs.
- [ ] Focused tests, operator-language, and docs gates pass.

## STOP conditions

- A metric requires serializing a queryset, answer, reference excerpt, or raw exception.
- The server cannot determine the selected view without trusting arbitrary input;
  omit it and report the limitation rather than weakening validation.

## Maintenance notes

Plan 028 should reuse these fields for lock wait/owner/follower outcomes instead
of inventing a second telemetry shape.
