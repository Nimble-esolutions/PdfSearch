# Plan 005: Reconcile and protect the Hallmark Civic Workbench

> **Executor instructions**: The full-screen Civic Knowledge Workbench is now
> the canonical implementation. Verify and enhance it in place. Do not restore
> the historical banner/chat layout described below.

## Status

- **Priority**: P1
- **Effort**: S/M
- **Risk**: MED
- **Depends on**: Plans 001 and 006 gate
- **Category**: UI / accessibility / performance
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: RECONCILE

## Drift check

```bash
git diff --stat f742b59..HEAD -- \
  flowdocs/core/templates/search.html \
  flowdocs/core/static/main/css/civic-workbench.css \
  flowdocs/core/static/main/css/search.css \
  flowdocs/core/static/main/js/search.js \
  flowdocs/core/views.py flowdocs/core/tests.py browser_tests
```

## Historical defect and current evidence

- `flowdocs/core/templates/search.html` now provides the responsive workbench
  shell, conversation, source rail/drawer, and composer.
- `flowdocs/core/static/main/css/civic-workbench.css` contains the canonical
  Hallmark layout/tokens; `search.css` is the remaining cascade.
- `flowdocs/core/static/main/js/search.js` now contains adaptive motion,
  loading/error handling, source interactions, answer formatting, copy, and
  multi-channel sharing.
- `flowdocs/core/tests.py:719-833` and browser tests cover the accessible shell,
  public scope, word limits, integrity failures, and request-state behavior.
- `flowdocs/flowdocs/settings.py:204-226` contains public-search visibility and rate-limit policy that must remain a server-side safety boundary.

## Target experience

Make the search surface an official, calm document-service interface: identity and purpose first, prominent search input, language-aware query affordance, transparent result/source metadata, and a useful empty/error/no-result state. Preserve the current Hallmark identity direction and purge competing themes only after parity and visual QA. Do not turn the page into a generic AI chat screen.

Use responsive CSS grid/flex, intrinsic sizing, logical properties, `content-visibility` only where measured, optimized local assets, explicit image dimensions, lazy loading for below-fold media, and a controlled font strategy. Support 320px–1440px+ widths, touch targets, keyboard navigation, reduced motion, Marathi/English text, and high contrast.

## Backend and performance boundaries

Keep public folder scoping, authentication/authorization, rate limiting, query limits, and retrieval semantics on the server. Add bounded request cancellation, request IDs, safe error categories, and server-timed metadata. Preserve streaming/progressive results only if the API contract supports it; never expose internal prompts, stack traces, storage keys, or hidden folders.

The Hallmark visual system and citizen-facing behavior are product boundaries;
the implementation behind them is not. The public response remains compatible
through an adapter while Plans 011/012 allow evidence bundles, generation
manifests, retrieval providers, and model providers to evolve independently.

## Reconciliation and enhancement steps

1. Capture the current empty, loading, answer, no-result, error, source drawer,
   Marathi, authenticated, and reduced-motion states as the baseline.
2. Run browser/axe checks at the configured projects plus 320px, mobile
   landscape, 1440px, 1920px, and 200% zoom.
3. Measure CSS/JS/image/font transfer size, long-answer main-thread work,
   layout shift, and slow-network behavior. Establish budgets before tuning.
4. Fix only reproduced defects: overflow, sticky composer obstruction, focus
   order, drawer trapping/restoration, answer formatting, or stale actions.
5. Inventory alternate templates/assets/styles. Delete only references proven
   unreachable by source scan, browser tests, and a clean image build.
6. Update the UI contract and visual evidence when a deliberate enhancement
   changes the baseline.

## Commands and scope

```bash
python manage.py test core.tests.SearchAndAuthenticationTests \
  core.tests.LanguageAndPublicUiTests core.tests.SeoAeoTests
node --check flowdocs/core/static/main/js/search.js
npx playwright test
python manage.py check
git diff --check
```

In scope: canonical search template/CSS/JS, locale catalogs, focused views,
browser/Django tests, and the UI contract. Out of scope: retrieval algorithms,
OpenAI invocation, authentication policy, protected PDF authorization,
unapproved identity changes, and production content.

## Done criteria

- Search works at mobile, tablet, desktop, and widescreen sizes without clipping, horizontal scroll, or tiny support controls.
- First meaningful render and interaction remain within agreed budgets on a cold and warm cache; large assets are measured and optimized.
- Keyboard, screen-reader labels, focus states, reduced motion, Marathi text, and error recovery are covered.
- Public visibility and rate-limit tests remain green; no server-side safety policy is weakened.

## STOP conditions

Stop if a proposed visual change contradicts the UI contract; if test fixtures
need production data; if a CSS cleanup cannot prove reference safety; if source
metadata required by the UI is fabricated; or if a backend response change is
required without Plan 012's compatibility contract.
