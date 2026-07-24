# PdfSearch Improvement Plans

These plans are the durable implementation brief for future agents. They supersede the partially implemented ideas in `docs/ADMIN_UI_REDESIGN_SRS.md` where the two conflict.

## Baseline

- Baseline commit: `0494528` (`feat(admin): polish settings and operations panels`)
- Branch for this planning pass: `docs/admin-search-audit-plan`
- Scope: improve existing behaviour and Hallmark shell; do not remove working workflows or reintroduce alternate themes.
- Application code is unchanged by this planning pass.

## Delivery order

1. `001-registrar-domain-brief.md` — DONE: domain vocabulary, identity, and content provenance.
2. `002-runtime-config-and-settings-center.md` — TODO, P1: typed configuration inventory and safe settings UX.
3. `003-vault-operational-state-and-lifecycle.md` — TODO, P1: truthful vault health and operational workflows.
4. `004-breadcrumb-dashboard-category-workbench.md` — TODO, P1: route-aware navigation and dashboard/category workbench.
5. `005-hallmark-public-search-redesign.md` — TODO, P1: preserve the Hallmark visual direction while rebuilding the search experience.
6. `006-cross-surface-verification.md` — TODO, P1: automated and visual release gates.

Plans 002–004 should land as small cherry-pickable PRs. Plan 005 may proceed in parallel after the domain brief, but its backend/API contract must be agreed before visual work. Plan 006 gates merging.

## Non-negotiable constraints

- Preserve the current Hallmark header, maroon navigation, CC identity mark, typography direction, and information hierarchy unless a measurable improvement is demonstrated.
- Purge competing/legacy search themes only after the canonical Hallmark search has feature parity and responsive coverage.
- Never display secrets, raw `.env` values, tokens, or connection credentials.
- Do not claim an action succeeded until the backend has completed and returned a persisted result.
- Every route must have a breadcrumb or an intentional documented exception.
- Backend changes must be additive/refactoring-oriented and covered by tests; no deletion of working capabilities.
- Branch, commit, verify, push, and open PR. Do not merge without explicit operator approval.
