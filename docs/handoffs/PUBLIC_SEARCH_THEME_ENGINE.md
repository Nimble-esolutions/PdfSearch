# Public Search Theme Engine Handoff

**Updated:** 2026-08-06
**Branch:** `feat/public-search-theme-engine`
**Base:** `origin/dev` at `0f95a69`

## Delivered

- Classic is the safe default and matches the approved training/24 June
  composition with optimized local WebP assets and no CDN Bootstrap or inline
  application JavaScript.
- Workbench remains intact at `/?view=workbench`.
- Superadmins can persist Classic or Workbench under Settings; no ENV or
  deployment change is required.
- Both views retain English/Marathi session switching and the supplied Help,
  Locate Us, and Feedback destinations.
- Frontend files are isolated; the secured Django search/PDF contract is shared.

## Verification evidence

Completed locally in the isolated development Compose stack:

- Django system check: pass.
- Targeted Django theme/SEO/search/language/auth tests: 38 pass.
- Migration drift: none.
- Marathi catalog compile/fuzzy check and operator-language validation: pass.
- Classic/Workbench/motion Playwright matrix: 72 pass across desktop, laptop,
  tablet, and mobile.
- Visual review: Classic 1920×1080, Classic 390×844, Workbench 1440×900.

## Deployment and rollback

No stage or production deployment has been performed. Deploy the reviewed image
through the normal immutable-image pipeline after the PR is green. Existing
databases need no migration. The first render defaults to Classic unless the
superadmin has saved Workbench.

Rollback by selecting Workbench in Settings, or by reverting the feature PR.
Neither action changes documents, indexes, search sources, backups, or runtime
generations.

## Remaining publication steps

1. Refresh the code graph and audit ledger.
2. Commit in reviewable chunks, push, and open a PR into `dev`.
3. Do not merge until CI is green and the operator authorizes merge.
