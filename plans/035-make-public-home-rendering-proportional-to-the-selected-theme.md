# Plan 035: Make public home rendering proportional to the selected theme

> **Executor instructions:** This is a performance and correctness plan, not a
> visual redesign. Read `AGENTS.md`, `docs/HANDOFF.md`, and
> `docs/design/AI_SAHAKAR_UI_CONTRACT.md` before editing. Preserve the isolated
> Classic and Workbench templates, styles, and controllers. Do not turn this
> into a cache-everything change or make analytics a dependency of public
> search.

## Status

- **Priority:** P1
- **Effort:** M
- **Risk:** MED
- **Depends on:** Plan 006 verification gate
- **Planned at:** `3591956` on 2026-08-07
- **Roadmap status:** TODO

## Objective

Make the public `GET /` response prepare only the data required by the
resolved public theme. The normal Classic primary experience must not pay for
Workbench-only copy, unrendered document counts, or a second primary-theme
lookup. Preserve public-search results, authentication, localization,
`?view=workbench`, and all consent-led analytics behavior exactly.

## Evidence and root cause

| Finding | Source evidence | Root cause | User impact |
| --- | --- | --- | --- |
| The public route resolves the primary theme more than once. | `flowdocs/core/views.py:search_query()` calls `_public_view_context()` and then `get_primary_search_view()` again; `flowdocs/core/search_ui.py:get_primary_search_view()` reads the persisted setting on cache miss. | Theme resolution is requested independently by the shared context and the page context. | Avoidable database/cache work on the first request. |
| Both theme payloads are built for every public page. | The `GET` branch prepares `welcome_prompts`, `workbench_copy`, and other presentation copy before selecting the template. | Context construction follows the historical combined page rather than the selected template. | More translation work and request allocations than the visitor can use. |
| Scoped document counts are calculated even though the public templates do not render them. | `visible_pdfs(...).filter(indexed=True).count()` and `.count()` are appended to the render context. | A historical dashboard-style context was retained without a consumer contract. | At least two unnecessary database aggregations on the public home response. |
| The public route contains both GET rendering and the full POST search workflow. | `search_query()` is a high-complexity entry point with theme resolution, rendering, validation, rate limiting, retrieval, and response handling. | Rendering concerns were never separated from search execution concerns. | Small GET optimizations are easy to regress without targeted tests. |

The existing route measurements are a warning, not a release metric. Before
changing code, capture a fresh baseline on a representative stage image with
warm and cold application state, recording DNS/connect/TLS separately from
server TTFB. A fast legal page or health endpoint is not evidence that the
public home route is fast.

## Design and scope

```text
GET /?view=workbench
        |
        v
resolve persisted primary view once
        |
        v
resolve allowlisted requested/primary view once
        |
        +--> Classic context builder  --> classic template only
        |
        +--> Workbench context builder --> workbench template only
        |
        v
append shared public contract (language, safe links, consent config)
```

The shared public contract is backend data only: resolved view identity,
localized navigation labels, safe public URLs, and the existing optional
analytics configuration. It must not become a shared public template, CSS
file, or browser controller.

### In scope

1. Resolve the persisted primary view once per public GET and pass that result
   through the existing allowlisted view resolver.
2. Split context construction into small, named, server-side builders for
   shared, Classic-only, and Workbench-only data. Keep translation at request
   time so language choice remains correct.
3. Remove `indexed_count` and `total_count` only after a repository-wide
   template, JavaScript, test, and API-consumer inventory proves they are not
   part of a supported response contract. If a real consumer exists, replace
   the two queries with one documented aggregate that runs only for that
   consumer.
4. Keep the POST search path behavior unchanged in the first slice. A later
   readability refactor may extract it, but must be its own reviewable commit
   after the GET path is measured and protected.
5. Add query-count and rendered-context regression coverage for Classic,
   persisted Workbench, and `?view=workbench` preview cases.

### Explicit non-goals

- No response caching of personalized, permission-sensitive, or consent-aware
  HTML.
- No change to retrieval, embeddings, provider deadlines, source visibility,
  answer language, rate limits, or data generations.
- No theme sharing, SPA/framework adoption, new environment variable, or
  analytics SDK change.
- No stage or production deployment while this plan is being written.

## Implementation slices

### Slice A — establish an honest baseline and consumer contract

1. Add a focused test helper that renders `GET /` under a captured query
   budget. The budget must distinguish a cold primary-view cache from a warm
   one; do not assert an artificial zero-query render.
2. Inventory each context key using repository search and rendering tests.
   Record the result in the PR description: key, Classic consumer, Workbench
   consumer, and whether it is safe to remove.
3. Capture a small stage-like local benchmark with a fixed request set:
   Classic primary, persisted Workbench primary, and Classic primary with
   `?view=workbench`. Record percentile TTFB and SQL query count without
   recording public questions, documents, or credentials.

### Slice B — make view resolution and context construction single-purpose

1. Change `_public_view_context()` or introduce a narrowly named resolver so
   the primary view is fetched once and the selected view is returned with the
   common context. Do not add a module-global mutable theme state.
2. Create server-side context builders with explicit names, for example
   `_classic_public_context()` and `_workbench_public_context()`. Each builder
   returns only values its own template consumes. Keep each view's template,
   CSS, and JavaScript isolated as required by the UI contract.
3. Build the template context by merging the common contract with exactly one
   presentation-specific contract. Preserve existing keys temporarily where a
   template expects them; remove aliases in a later cleanup commit only after
   the browser suite proves no breakage.
4. Remove the unused `visible_pdfs()` counts after Slice A proves that they are
   dead. Do not replace them with a cached number merely because the original
   work was expensive.

### Slice C — certify outcome and retain a regression signal

1. Compare cold/warm SQL count and server TTFB against Slice A. State the
   environment and sample count in the PR; do not present local timings as
   public-host proof.
2. Add a budget assertion that protects the eliminated query path without
   relying on exact database internals unrelated to the change.
3. Confirm the request still emits no analytics configuration on local,
   preview, disabled, GPC, or no-consent pages. Analytics must remain
   best-effort and must not change response eligibility or cache headers.

## File-level implementation map

| File / area | Intended change | Guardrail |
| --- | --- | --- |
| `flowdocs/core/views.py` | Isolate public GET context assembly and reuse one resolved primary view. | Do not alter POST search execution in the same commit. |
| `flowdocs/core/search_ui.py` | Reuse existing allowlisted resolution; change only if tests prove a narrow API is needed. | Persisted setting remains the source of truth; Classic remains safe default. |
| `flowdocs/core/templates/search*.html` | Remove a context use only when the consumer inventory proves it is unused. | No cross-theme include or CSS/JS sharing. |
| `flowdocs/core/tests*.py` | Add request/query-contract and view-resolution coverage. | Test both anonymous and authenticated scope where current behavior differs. |
| `browser_tests/classic-search.spec.ts`, Workbench equivalent | Prove page render, answer continuity, language, and `?view=workbench` remain intact. | Browser evidence must use the source-backed runtime. |

## Impact analysis

| Area | Expected effect | Required guardrail |
| --- | --- | --- |
| First load | Fewer database counts, translations, and allocations before first paint. | Measure server TTFB separately from browser assets and proxy time. |
| Theme selection | One deterministic persisted lookup and one request override decision. | `?view=workbench` stays shareable and non-persistent; invalid overrides fall back safely. |
| Localization | Only selected-theme copy is translated. | English/Marathi snapshots and language switching remain correct. |
| Authorization | Removing counts must not change `visible_pdfs()` used by POST retrieval. | No permission logic moves into a cache or client. |
| Analytics | No new event or payload is introduced. | Consent/GPC/no-local hard-disable tests remain green. |
| Rollback | The refactor is reversible without a migration or data operation. | A single commit can restore the former context builder if a hidden consumer is found. |

## Verification matrix

| Layer | Required proof |
| --- | --- |
| Unit/request | Classic default, persisted Workbench, valid/invalid override, local/anonymous/authenticated rendering, localized labels, and context-key consumer contract. |
| Database | Cold and warm query budgets prove the duplicate lookup and unused counts are gone without removing required authorization queries. |
| Browser | Classic and Workbench initial render, long answer continuity, composer visibility/focus, keyboard navigation, Marathi UI, responsive viewports, and `?view=workbench`. |
| Analytics | Disabled/no-choice/GPC/outage conditions leave home rendering and search usable. |
| Release | `git diff --check`, `python manage.py check`, focused tests, source-backed Playwright, then a documented stage canary. |

## Rollout, rollback, and stop conditions

Deploy first to stage using a resolved immutable image digest. Compare the
baseline and candidate for 30 or more requests per selected view after a warmup
period; inspect status codes, TTFB, worker CPU, and database query metrics.
Roll back by redeploying the prior image if public rendering, language
selection, source visibility, or request routing differs.

Stop and obtain review if the inventory finds a consumer for a supposedly
unused context key, if resolving the theme requires a new shared front-end
layer, if a proposed cache could expose permission-scoped data, or if the
measured bottleneck lies in proxy/TLS/retrieval rather than GET rendering.

## Done criteria

- The selected public theme alone determines the presentation context built for
  `GET /`.
- No unused document-count queries remain on the public home path.
- Classic stays primary by default; Workbench override is preserved and
  non-persistent.
- English/Marathi, accessibility, public links, consent behavior, and search
  POST behavior are regression-tested.
- The PR contains before/after measured evidence and a simple image rollback.
