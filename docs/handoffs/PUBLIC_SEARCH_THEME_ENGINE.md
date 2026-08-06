# Public Search Theme Engine Handoff

**Updated:** 2026-08-06
**Merged:** PRs #185 and #186; stage revision `1067c054edd6a21a7881371ca0670e428dc4cc81`
**Stage artifact:** `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:8649af368c2ba84f272942d7ab969055f0c2eaa502b4032f7bd52aa955cc52cc`

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
- Public policy/information pages use the active or explicitly previewed theme
  through a standalone visitor shell. They do not inherit admin UI assets.

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

## Current evidence

PR #186 replaced substring conversational matching with normalized exact intent,
question-derived answer language, provider-output validation, and one bounded
repair attempt. On stage, both themes returned HTTP 200. A Marathi document
question submitted with an English client locale returned a Marathi/Devanagari
`evidence_answer` with three references; the inverse English question submitted
with a Marathi client locale returned an English/Latin `evidence_answer` with
three references. The signed active generation and indexing ratio remained
unchanged.
