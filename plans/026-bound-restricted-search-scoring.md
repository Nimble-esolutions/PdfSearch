# Plan 026: Remove restricted-search query and matrix amplification

> **Executor instructions**: Run the drift check first and preserve the exact
> authorization result. Stop rather than broadening visibility for speed.
>
> **Drift check (run first)**:
> `git diff --stat f090650..HEAD -- flowdocs/core/utils.py flowdocs/core/views.py flowdocs/core/test_search_performance.py flowdocs/core/test_public_search_routing.py`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH — authorization and ranking share this path
- **Depends on**: none
- **Category**: performance, security
- **Planned at**: commit `f090650`, 2026-08-07

## Why this matters

The signed-corpus fast path issues one `values_list()` query per restricted
folder and uses NumPy advanced indexing, which copies the selected matrix.
Authenticated restricted searches can therefore add N queries and temporarily
duplicate much of the admitted 96 MiB corpus. The fix must reduce work without
changing which PDFs can be searched or the existing per-folder ranking bound.

## Current state

- `flowdocs/core/utils.py:761-776` walks every restricted folder queryset and
  evaluates its PDF IDs separately.
- `flowdocs/core/utils.py:797-805` computes
  `corpus.embeddings[allowed_array] @ normalized_query`; advanced indexing makes
  a new matrix when access is restricted.
- `flowdocs/core/utils.py:803-823` preserves a per-folder candidate limit before
  global ranking. That ordering is a compatibility contract.
- `flowdocs/core/views.py:2248-2297` constructs authorized folder scopes. It is
  the authority boundary; do not infer access from cached corpus metadata.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Focused tests | `env SECRET_KEY=test ALLOW_INSECURE_DEFAULTS=1 APP_ENV=test PDFSEARCH_TEST_EMBEDDINGS=1 .venv/bin/python manage.py test core.test_search_performance core.test_public_search_routing` | exit 0 |
| Query check | add `assertNumQueries` coverage in `core.test_search_performance` | fixed count independent of restricted-folder count |
| Full contract | `bash scripts/ci/fast_pr_contract.sh` | exit 0 |
| Whitespace | `git diff --check` | no output |

## Scope

**In scope**: the authorized-scope representation passed from `views.py` to
`utils.py`, signed-corpus score selection, focused tests, and release docs.

**Out of scope**: permission model changes, result-cache enablement for
restricted users, ranking formula changes, corpus admission limits, database
migration, or a shared cross-worker corpus.

## Steps

1. Materialize restricted PDF authorization once at the view/service boundary
   using a bounded set or one composable query; pass the resolved IDs with the
   folder scopes so cache validation, scoring, and final evidence authorization
   reuse the same request snapshot.
2. Compute the one-dimensional full score vector once. Apply the authorization
   mask/allowed positions to that vector, not to the two-dimensional embedding
   matrix. Preserve per-folder `heapq.nlargest` and final candidate ordering.
3. Keep a fresh final lifecycle/runtime validation before returning evidence;
   the request snapshot optimizes authorization work but is not permission to
   cache away the mutation-race guard.
4. Add tests with many restricted folders proving constant query count,
   identical authorized hits/order, zero unauthorized references, and no
   advanced-index copy of the corpus matrix.

## Done criteria

- [ ] Restricted-folder count does not increase authorization query count.
- [ ] Scoring does not advanced-index the 2-D corpus matrix.
- [ ] Public/admin/restricted result sets remain byte-for-byte equivalent.
- [ ] Final runtime and reference authorization checks remain in place.
- [ ] Focused tests and fast PR contract pass.

## STOP conditions

- Query consolidation changes a custom queryset's filters or lifecycle rules.
- The full score vector exceeds the admitted corpus bound or ranking equivalence fails.
- The solution requires enabling whole-result cache reuse for restricted users.

## Maintenance notes

Any future folder-permission source must feed the same resolved authorization
snapshot. Reviewers should scrutinize negative authorization tests before speed.
