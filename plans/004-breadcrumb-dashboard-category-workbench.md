# Breadcrumbs, Dashboard, and Category Workbench

## Evidence and defect

`flowdocs/core/templates/base.html:109-121` renders breadcrumbs only when a view supplies `breadcrumb_items`. Context is supplied in a few paths (`flowdocs/core/views.py:536, 573, 923, 947, 977, 1642, 1821`), but the vault view and several admin/subpages do not provide it. This creates missing or inconsistent trails and makes deep pages harder to navigate.

The current Hallmark dashboard is the visual baseline and already contains useful metrics, category work, recent intake, jobs, and configuration. The improvement must organize that information rather than revert to the older blue default shell or add a decorative sidebar that competes with the existing header/navigation.

## Target architecture

Add a route-aware breadcrumb builder shared by views or a context processor. It should accept route metadata and object labels, generate safe URLs, mark the current item with `aria-current`, and gracefully handle missing/deleted objects. Define intentional exceptions for login and public search. Add regression coverage for dashboard, settings, operations, vault, category detail, user pages, and legal pages.

Refactor dashboard sections into maintainable includes while preserving behaviour. Make the first viewport answer: what needs attention, what is ready, and what can I do next? Use metric definitions and links rather than unexplained counts. Add a category workbench/detail route that supports category summary, document list/readiness, upload/index state, bulk actions, provenance, and return-to-dashboard navigation. Keep permissions enforced server-side.

## Implementation steps

1. Inventory every URL and template, classify breadcrumb parent/current labels, and add route tests before changing markup.
2. Implement shared breadcrumb context and update all admin/subpage views, including vault and category paths.
3. Split dashboard markup into includes with stable IDs/data attributes for tests; do not remove existing actions.
4. Add category workbench route/template using existing category/document services and query bounds; support empty/error/loading states.
5. Improve dashboard action hierarchy, metric definitions, job visibility, and mobile stacking within the current Hallmark shell.
6. Add secondary tabs or scoped navigation only where it reduces deep-link friction; keep primary navigation and logo/header intact.

## Tests and done criteria

- Every authenticated admin route has a valid breadcrumb or documented exception.
- Breadcrumb links never point to empty or incorrect routes; current item is not a link.
- Dashboard preserves existing actions, role visibility, and job polling.
- Category workbench is accessible, paginated/bounded, mobile-safe, and permission-tested.
- No horizontal overflow or clipped controls at 320px, 390px, tablet, desktop, and widescreen widths.
