# Public Search Theme Engine Handoff

**Updated:** 2026-08-06
**Merged:** PR #185 at `690ed888b30c0b61ce2ac3bc5824457469b83cf0`
**Stage artifact:** `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:b38d887f784a141fe5c3d2d2ca68e721b96d92b76a50e71129d7dcbac120323c`

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
- Shared search responses are typed as `small_talk`, `evidence_answer`,
  `no_evidence`, `validation`, or `error`; themes render the outcome but do not
  classify user intent themselves.
- The shared backend resolves answer language from each question and both
  themes apply the returned `language` as accessible DOM metadata; changing the
  primary theme cannot change answer language.

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

PR #185 is deployed on stage. Existing databases required no migration. Classic
is the default unless a superadmin saves Workbench, and either view remains
available through its request-only `?view=` override.

Rollback by selecting Workbench in Settings, or by reverting the feature PR.
Neither action changes documents, indexes, search sources, backups, or runtime
generations.

## Current follow-up

The shared backend currently deployed on stage can misclassify document queries
as greetings because its legacy conversational fast path uses substring
matching. The verified follow-up replaces that behavior with normalized exact
intent matching, question-derived answer language, provider-output validation,
and the typed contract above. After rollout, canary both themes and prove that
the reported `updated rules` query reaches document search, exact English and
Marathi greetings remain source-free, and answer language follows the question
even when it differs from the selected UI language.
