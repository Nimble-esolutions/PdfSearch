# Cross-Surface Verification and Release Gates

## Required workflow

Each implementation plan is a separate branch and cherry-pickable commit/PR. Before pushing: run formatting/static checks, targeted Django tests, integration tests, and a clean `git diff --check`. Pull the target base branch before opening the PR. Do not merge without green checks and operator approval.

## Automated gates

- Django system checks and targeted tests for settings resolution, permissions, breadcrumbs, dashboard routes, category workbench, vault health, job idempotency, and search policy.
- Existing smoke scripts for login/admin UI and runtime health.
- Template/static checks, JavaScript syntax checks, and dependency/reference scan before deleting alternate themes/assets.
- Security checks for CSRF, authorization, secret redaction, public folder scope, rate limits, and unsafe action confirmation.
- Performance checks for response size, query count, asset weight, and search request cancellation.

## Visual gates

Using the local dev server and browser automation, capture authenticated/admin pages and public search at 320, 390, 768, 1024, 1440, and a wide viewport. Review:

- Hallmark header, logo clear space, nav active state, footer, and breadcrumb correctness.
- Settings groups, source/restart badges, validation errors, and narrow-screen tables.
- Vault health truthfulness, job progress, disabled/destructive states, and recovery paths.
- Dashboard first viewport, category workbench, empty/error states, and no clipped actions.
- Search identity, query interaction, result/reference hierarchy, Marathi copy, support controls, reduced motion, and no horizontal overflow.

Use axe or equivalent accessibility checks plus screenshot diff review. A page is not done because it returns 200; actions, state transitions, and failure paths must be observed.

## Stop conditions

Stop and return to design review if a change removes a working action, changes public search exposure, introduces a secret-bearing UI, creates a false operational state, breaks the Hallmark header baseline, or cannot be verified at the supported viewport sizes.
