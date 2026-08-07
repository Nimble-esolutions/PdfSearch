# Plan 029: Keep both search composers reachable through mobile viewport changes

> **Executor instructions**: Preserve separate Classic and Workbench templates,
> CSS, and JavaScript. Share behavioral acceptance criteria, not visual code.
>
> **Drift check (run first)**:
> `git diff --stat f090650..HEAD -- flowdocs/core/static/main/css/civic-workbench.css flowdocs/core/static/main/css/search-classic.css flowdocs/core/static/main/js/search.js flowdocs/core/static/main/js/search-classic.js browser_tests/civic-workbench.spec.ts browser_tests/classic-search.spec.ts`

## Status

- **Priority**: P2
- **Effort**: S/M
- **Risk**: MED — viewport fixes can regress desktop scroll ownership
- **Depends on**: none
- **Category**: bug, tests, accessibility
- **Planned at**: commit `f090650`, 2026-08-07

## Why this matters

Current four-viewport tests cover static mobile and short landscape layouts,
but not the dynamic visual-viewport shrink caused by a software keyboard or a
multiline composer. Workbench reserves a fixed 11rem transcript bottom area on
mobile. A taller composer or reduced visual viewport can cover the latest
answer or make the next question hard to reach even though static screenshots pass.

## Current state

- `flowdocs/core/static/main/css/civic-workbench.css:327-390` uses fixed
  `100dvh` workspace heights and 11rem transcript bottom padding on mobile.
- `browser_tests/civic-workbench.spec.ts` now proves mobile reset reachability,
  malformed-success handling, and static 320px overflow.
- `browser_tests/classic-search.spec.ts` proves long-answer scrolling and short
  landscape continuity. Treat it as the behavioral exemplar, not shared styling.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Workbench | `npx playwright test browser_tests/civic-workbench.spec.ts` | all viewport projects pass |
| Classic | `npx playwright test browser_tests/classic-search.spec.ts` | all viewport projects pass |
| Accessibility | existing Axe assertions in both specs | no serious/critical violations |
| JS syntax | `node --check flowdocs/core/static/main/js/search.js && node --check flowdocs/core/static/main/js/search-classic.js` | exit 0 |

## Scope

**In scope**: viewport/composer CSS and minimal JS needed to observe
`visualViewport`, dynamic-viewport tests, focus/scroll assertions, and UI docs.

**Out of scope**: merging theme assets, redesigning either theme, changing the
backend contract, native-app behavior, or adding a frontend framework.

## Steps

1. Add Playwright tests that focus the textarea, shrink the viewport to emulate
   a keyboard, grow the textarea to multiple lines, submit, receive a long
   answer, and submit a second question. Assert the visible composer, transcript
   scroll ownership, focused input, and unobscured latest answer.
2. Prefer CSS dynamic viewport and intrinsic layout fixes. If CSS cannot express
   the actual visual viewport, add one small requestAnimationFrame-throttled
   `visualViewport` listener that updates a CSS custom property and cleans up.
3. Make reserved transcript space derive from actual composer height rather
   than a fixed guess. Keep `min-height: 0` through scroll-owning ancestors.
4. Run both theme suites at all viewport projects and reduced-motion mode.

## Done criteria

- [ ] Focused multiline composer remains visible after simulated keyboard shrink.
- [ ] Long transcript remains independently scrollable and latest answer is not covered.
- [ ] A second query succeeds without viewport reset.
- [ ] Classic and Workbench stay visually and structurally isolated.
- [ ] All browser and accessibility gates pass.

## STOP conditions

- The fix requires global body overflow rules shared by both themes.
- Playwright cannot model the failure reproducibly; capture a manual device
  trace and report instead of landing untestable viewport code.

## Maintenance notes

Review every composer-height or header-height change against this dynamic test,
not only static screenshots.
