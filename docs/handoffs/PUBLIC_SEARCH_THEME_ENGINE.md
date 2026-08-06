# Public Search Theme Engine Handoff

**Updated:** 2026-08-06
**Merged:** PRs #185, #186, and #187
**Stage artifact:** `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:8649af368c2ba84f272942d7ab969055f0c2eaa502b4032f7bd52aa955cc52cc`
**In repair:** Classic long-answer continuity regression on branch
`fix/classic-search-continuity`; merge and stage verification are pending

## Classic long-answer regression (2026-08-06)

The first real long structured response exposed two Classic-only frontend
defects. The body grid had a minimum height but no definite viewport height, so
the nominally scrollable transcript expanded with its content and pushed the
composer below the viewport. Because that element also contained overscroll,
wheel and touch input over the conversation could not reach the document
scroll. Separately, Classic wrote the answer with `textContent` paragraphs,
which correctly blocked HTML injection but displayed Markdown markers instead
of headings, lists, and bold text.

The repair keeps the approved visual composition and theme isolation while:

- bounding the page to the dynamic viewport and assigning vertical scrolling
  to the transcript;
- keeping the composer and footer reachable after long and repeated answers;
- rendering an allowlisted Markdown subset with DOM nodes and text nodes only;
- preserving hostile HTML as inert visible text;
- requiring the typed success envelope and allowing source cards only for
  evidence answers;
- accepting only same-origin protected PDF routes, with the correct anonymous
  public-PDF fallback;
- keeping retry controls synchronized with request state and presenting
  malformed successful responses as authored retry errors; and
- making the transcript keyboard-scrollable.

Regression coverage now exercises a long structured answer, hostile markup,
protected sources, transcript scroll ownership, a visible focused composer, a
second search, malformed payload recovery, 320px portrait, short landscape,
and serious/critical accessibility findings across the browser viewport
matrix. The required image smoke gate includes Classic search so this primary
public journey cannot be skipped again.

Do not claim this repair as deployed until its PR is green and merged, the
resulting image is running on stage, and the long-answer plus second-query
canary succeeds there.

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
- PR #187 prepares public policy/information pages to use the active or
  explicitly previewed theme through a standalone visitor shell. They do not
  inherit admin UI assets; deployment remains pending at this handoff point.

## Verification evidence

Completed locally in the isolated development Compose stack:

- Django system check: pass.
- Targeted Django theme/SEO/search/language/auth tests: 39 pass for PR #187.
- Migration drift: none.
- Marathi catalog compile/fuzzy check and operator-language validation: pass.
- Classic/Workbench/motion Playwright matrix: 72 pass across desktop, laptop,
  tablet, and mobile.
- Public information Playwright matrix: 16 pass across five routes, both
  themes, four viewport classes, canonical/asset isolation, responsive
  overflow, allowlisted preview propagation, and zero serious/critical Axe
  findings.
- Visual review: Classic 1920×1080, Classic 390×844, Workbench 1440×900.

## Deployment and rollback

PRs #185 and #186 are deployed on stage. Existing databases required no migration. Classic
is the default unless a superadmin saves Workbench, and either view remains
available through its request-only `?view=` override.

Rollback by selecting Workbench in Settings, or by reverting the feature PR.
Neither action changes documents, indexes, search sources, backups, or runtime
generations.

PR #187 is locally verified and in review. Do not claim its public-information
shell is deployed until the merged image revision is running on both stage web
and maintenance and both theme variants of every public information route pass.

## Current evidence

PR #186 replaced substring conversational matching with normalized exact intent,
question-derived answer language, provider-output validation, and one bounded
repair attempt. On stage, both themes returned HTTP 200. A Marathi document
question submitted with an English client locale returned a Marathi/Devanagari
`evidence_answer` with three references; the inverse English question submitted
with a Marathi client locale returned an English/Latin `evidence_answer` with
three references. The signed active generation and indexing ratio remained
unchanged.
