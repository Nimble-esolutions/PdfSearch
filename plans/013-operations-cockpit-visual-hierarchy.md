# Plan 013: Restore a task-first Operations Cockpit hierarchy

> **Executor instructions**: Follow this plan step by step. Run every verification
> command and confirm the expected result before moving on. If a STOP condition
> occurs, report it instead of improvising.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `6c9a262`, 2026-07-28

## Why this matters

The deployed Dashboard is visually polished but task order is wrong: the same
maintenance CTA appears in the posture banner, the attention header, and the
attention item, while the Category Yard and recent intake are below the first
viewport. The Workbench similarly spends the first viewport on identity and
authority chrome before showing the Documents & Indexes controls. Operators must
scroll past repeated calls to action before reaching the work they came to do.

## Current state

- `flowdocs/core/templates/dashboard.html:15-86` renders the posture CTA and a
  second maintenance/Vault action group before the attention list.
- `flowdocs/core/templates/dashboard.html:94-201` repeats “Maintain Documents &
  Indexes” in the attention item and places Category Yard after the entire hero
  and attention panel.
- `flowdocs/core/templates/vaultops/workbench.html:1-94` renders the large
  heading, eight-cell authority summary, and section navigation before the
  maintenance panels at `:95-289`.
- `flowdocs/core/static/main/css/style.css:682-740` gives the four decision
  metrics a five-column desktop grid, leaving unused space at the observed
  desktop width.
- `flowdocs/core/static/main/css/vault-workbench.css:47-84` gives the summary
  and section navigation substantial vertical space before task content.

The visual audit at `http://127.0.0.1:8000/dashboard/` and
`/dashboard/operations/?section=maintenance` confirmed the duplication and
above-the-fold ordering. Preserve the existing maroon/ochre visual language and
server-rendered progressive-enhancement approach; do not replace the UI with a
SPA.

## Commands you will need

| Purpose | Command | Expected result |
|---|---|---|
| Django checks | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py check` | exit 0 |
| Template/UI tests | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test flowdocs.core.tests flowdocs.vaultops.tests.test_workbench` | all tests pass |
| Browser gate | `npx playwright test browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts` | all tests pass |
| Compose smoke | `PDFSEARCH_IMAGE=pdfsearch-web REDIS_IMAGE=redis:7-alpine bash scripts/ci/run_compose_smoke.sh` | smoke gate passes |

## Scope

**In scope**: `flowdocs/core/templates/dashboard.html`,
`flowdocs/core/templates/vaultops/workbench.html`, the two related CSS files,
their focused tests, and browser specs.

**Out of scope**: data models, maintenance worker semantics, remote Vault
publication, navigation labels outside these surfaces, and a SPA rewrite.

## Steps

### Step 1: Define one primary action per state

Keep one primary posture action. Remove duplicate maintenance/Vault links from
the attention header or item, retaining a contextual item action only when its
destination differs. The first viewport should expose the posture, metrics, and
the first actionable attention row without repeating the same label three times.
For an empty/healthy state, retain the “no action required” message.

**Verify**: render the authenticated Dashboard browser spec and assert the
maintenance destination exists, the duplicate action count is at most one per
attention reason, and the Category Yard heading remains present.

### Step 2: Make task content visible sooner

Reorder or compact the Dashboard hero so Category Yard or the first attention
row is visible without excessive scrolling. On the Workbench, keep identity and
authority evidence but make the section nav compact and expose the maintenance
operation grid in the first desktop viewport. Preserve keyboard order and
semantic headings.

**Verify**: add screenshot/DOM assertions at desktop and 390px mobile widths for
the first viewport; no horizontal overflow; all section-nav links remain
keyboard reachable.

### Step 3: Fix responsive density and visual consistency

Use a four-column decision-metric grid when there are four metrics, normalize
the Add Category treatment with the established action palette, and ensure long
summary labels wrap without splitting values awkwardly. Keep 44px touch targets,
`prefers-reduced-motion`, and current contrast guarantees.

**Verify**: browser Axe checks report no serious/critical violations and the
existing overflow assertion passes at desktop and mobile.

## Test plan

Extend `browser_tests/operations-cockpit.spec.ts` and
`browser_tests/vault-workbench.spec.ts` with semantic assertions for action
deduplication, first-viewport task visibility, section navigation, and mobile
layout. Add a Django template test only where server state determines whether a
CTA is present.

## Done criteria

- [ ] No repeated identical primary CTA in the same Dashboard state.
- [ ] Category Yard/attention work is visible in the first desktop viewport.
- [ ] Documents & Indexes operation cards are visible without passing a full
  authority summary on desktop.
- [ ] Desktop and 390px browser checks pass with no overflow or serious Axe
  violations.
- [ ] No source files outside Scope are modified.

## STOP conditions

- The design contract requires the current ordering; stop and report the exact
  contract clause instead of overriding it.
- A first-viewport assertion requires browser-specific pixel coordinates; replace
  it with semantic visibility or stop for operator direction.

## Maintenance notes

Any new Dashboard attention reason must declare one primary destination and a
compact detail string. Any new Workbench section must be added to the same
responsive navigation and first-viewport budget.
