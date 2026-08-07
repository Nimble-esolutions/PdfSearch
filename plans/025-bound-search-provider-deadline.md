# Plan 025: Bound provider latency and return truthful search failures

> **Executor instructions**: Follow this plan step by step. Run every
> verification command before continuing. Stop on any condition listed below;
> do not add configuration switches or weaken evidence checks to make a test pass.
>
> **Drift check (run first)**:
> `git diff --stat f090650..HEAD -- flowdocs/core/ai_guard.py flowdocs/core/utils.py flowdocs/core/views.py flowdocs/core/test_search_performance.py flowdocs/core/test_public_search_routing.py browser_tests/classic-search.spec.ts browser_tests/civic-workbench.spec.ts`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED — timeout and retry semantics affect all real-provider searches
- **Depends on**: none
- **Category**: correctness, performance
- **Planned at**: commit `f090650`, 2026-08-07

## Why this matters

Embedding and chat calls are synchronous and have no search-specific deadline.
A slow provider can occupy all four web workers beyond the browser's 60-second
wait, and a provider exception can currently become warning text presented as a
successful evidence answer. Search must fail within one bounded request budget
and return an authored, retryable 503 without claiming source-backed success.

## Current state

- `flowdocs/core/ai_guard.py:83-115` creates real OpenAI clients without a
  timeout or retry budget.
- `flowdocs/core/utils.py:1171-1182` performs the embedding call synchronously.
- `flowdocs/core/utils.py:1585-1596` then performs answer generation on the same
  request; language repair may add another provider call.
- `flowdocs/core/views.py:2291-2330` maps search output to the public typed JSON
  envelope. Both themes already author 503 errors and must keep the same shape.
- Do not add environment variables. The branch's deliberate contract is
  automatic behavior with minimal operator configuration.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused backend | `env SECRET_KEY=test ALLOW_INSECURE_DEFAULTS=1 APP_ENV=test PDFSEARCH_TEST_EMBEDDINGS=1 .venv/bin/python manage.py test core.test_search_performance core.test_public_search_routing` | exit 0 |
| Django checks | `.venv/bin/python manage.py check` | no issues |
| Browser contract | `npx playwright test browser_tests/classic-search.spec.ts browser_tests/civic-workbench.spec.ts` | all pass against a source-backed server |
| Whitespace | `git diff --check` | no output |

## Scope

**In scope**: `flowdocs/core/ai_guard.py`, `flowdocs/core/utils.py`,
`flowdocs/core/views.py`, focused search tests, the two public-theme browser
specs, and the search-latency release note.

**Out of scope**: Gunicorn worker count/timeout, new environment variables,
async framework migration, provider replacement, cache-key changes, DataOps,
deployment, and production traffic.

## Steps

1. Introduce one code-level total search-provider deadline and a small bounded
   retry policy. Pass only the remaining request budget to embedding, chat, and
   optional language repair; do not let independent per-call limits exceed the
   total budget.
2. Add a typed provider-unavailable exception distinct from data-integrity and
   no-evidence outcomes. Provider timeout, connection failure, and exhausted
   deadline must raise it; never return provider-warning prose as an answer.
3. Map that exception to HTTP 503 with the existing typed error envelope and
   secret-free logging. Do not include provider exception text, query text, or
   document content in the response or logs.
4. Add deterministic tests for timeout before embedding, timeout during chat,
   exhausted language-repair budget, and normal success. Test both themes'
   authored retry state and prove no `evidence_answer` is rendered on failure.

## Done criteria

- [ ] All real provider calls share one bounded deadline shorter than 60 seconds.
- [ ] Provider failure returns HTTP 503, never HTTP 200 evidence.
- [ ] No new environment variable exists.
- [ ] Focused backend and both browser-theme gates pass.
- [ ] Logs and responses remain free of query, document, credential, and raw exception content.

## STOP conditions

- OpenAI client semantics cannot enforce the remaining deadline without a
  dependency upgrade; report the exact supported API before changing versions.
- A proposed fix needs to increase browser or Gunicorn timeouts.
- Existing callers depend on provider warning prose as a successful answer.

## Maintenance notes

Keep the deadline below the browser abort boundary and include every new
provider call in the same budget. Plan 028's single-flight waiting time must
also consume this budget rather than extending it.
