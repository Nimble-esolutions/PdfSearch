# Plan 004: Reconcile breadcrumbs, dashboard, and category workbench

> **Executor instructions**: The Hallmark admin shell, folder cockpit, and most
> breadcrumb contexts already exist. Verify route coverage and close specific
> gaps; do not replace the dashboard composition.

## Status

- **Priority**: P1
- **Effort**: S/M
- **Risk**: MED
- **Depends on**: Plan 006 gate
- **Category**: UI / navigation
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: RECONCILE

## Drift check

```bash
git diff --stat f742b59..HEAD -- \
  flowdocs/core/views.py flowdocs/core/urls.py \
  flowdocs/core/templates/base.html flowdocs/core/templates/dashboard*.html \
  flowdocs/core/tests.py
```

## Evidence and defect

`flowdocs/core/templates/base.html` still renders supplied breadcrumb context,
but current views now provide it for dashboard/folder, users, registration,
operations, and settings paths (`flowdocs/core/views.py:449-588,892-1002,
1710-1870`). The folder cockpit and category list already expose operational
state and actions. The remaining task is a route-to-breadcrumb inventory,
semantic/current-item verification, bounded lists, and responsive regression.

The current Hallmark dashboard is the visual baseline and already contains useful metrics, category work, recent intake, jobs, and configuration. The improvement must organize that information rather than revert to the older blue default shell or add a decorative sidebar that competes with the existing header/navigation.

## Target architecture

Add a route-aware breadcrumb builder shared by views or a context processor. It should accept route metadata and object labels, generate safe URLs, mark the current item with `aria-current`, and gracefully handle missing/deleted objects. Define intentional exceptions for login and public search. Add regression coverage for dashboard, settings, operations, vault, category detail, user pages, and legal pages.

Refactor dashboard sections into maintainable includes while preserving behaviour. Make the first viewport answer: what needs attention, what is ready, and what can I do next? Use metric definitions and links rather than unexplained counts. Add a category workbench/detail route that supports category summary, document list/readiness, upload/index state, bulk actions, provenance, and return-to-dashboard navigation. Keep permissions enforced server-side.

## Reconciliation steps

1. Build a URL inventory and mark each authenticated HTML route as covered or
   an intentional exception.
2. Add tests for parent URLs, current item semantics, missing/deleted objects,
   translated labels, role visibility, and 404/redirect behavior.
3. Verify the existing folder/category cockpit meets bounded pagination,
   readiness/provenance, empty/error, mobile, and permissions requirements.
4. Patch only uncovered routes or missing semantics. Introduce a shared builder
   only if three or more live views still duplicate error-prone construction.
5. Run responsive browser coverage before marking the plan done.

## Commands and scope

```bash
python manage.py test core.tests.DashboardTests \
  core.tests.MutationAuthorizationTests
python manage.py check
npx playwright test --project=mobile --project=desktop
git diff --check
```

In scope: URL inventory, breadcrumb context, dashboard/folder templates,
responsive styling, and tests. Out of scope: new global navigation, identity
redesign, search backend changes, and unrelated admin CRUD.

## Tests and done criteria

- Every authenticated admin route has a valid breadcrumb or documented exception.
- Breadcrumb links never point to empty or incorrect routes; current item is not a link.
- Dashboard preserves existing actions, role visibility, and job polling.
- Category workbench is accessible, paginated/bounded, mobile-safe, and permission-tested.
- No horizontal overflow or clipped controls at 320px, 390px, tablet, desktop, and widescreen widths.

## STOP conditions

Stop if route ownership or parentage is ambiguous; if a shared builder would
change public/login/legal behavior; if a UI change removes an existing action;
or if browser fixtures cannot represent authenticated roles safely.
