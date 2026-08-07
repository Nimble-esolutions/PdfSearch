# Public Search Theme Engine Handoff

**Updated:** 2026-08-07
**Status:** Historical / completed delivery handoff
**Superseded by:** [`../HANDOFF.md`](../HANDOFF.md) for current deployment
evidence and [`../design/AI_SAHAKAR_UI_CONTRACT.md`](../design/AI_SAHAKAR_UI_CONTRACT.md)
for the active UI contract

This file records why the dual-theme delivery was shaped as it was. It contains
no current image, rollout, or pending-release authority.

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

The repair was completed in the theme-engine delivery. Use the living handoff
to determine whether a particular image and stage canary include it.

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

## Representative verification evidence at delivery

The following are representative delivery checks, not the complete current
route or test inventory. Consult the exact workflow and tests at the revision
being released.

- Django system check: pass.
- Targeted Django theme/SEO/search/language/auth tests: pass.
- Migration drift: none.
- Marathi catalog compile/fuzzy check and operator-language validation: pass.
- Classic/Workbench/motion Playwright coverage across desktop, laptop, tablet,
  and mobile: pass.
- Public-information coverage across both themes includes canonical/asset
  isolation, responsive overflow, allowlisted preview propagation, and
  serious/critical accessibility checks.
- Visual review: Classic 1920×1080, Classic 390×844, Workbench 1440×900.

## Deployment and rollback

Existing databases required no theme-engine migration. Classic is the default
unless a superadmin saves Workbench, and either view remains available through
its request-only `?view=` override.

Rollback by selecting Workbench in Settings, or by reverting the feature PR.
Neither action changes documents, indexes, search sources, backups, or runtime
generations.

## Historical language evidence

The delivery replaced substring conversational matching with normalized exact
intent, question-derived answer language, provider-output validation, and one
bounded repair attempt. A stage rehearsal showed a Marathi document question
with an English client locale returning a Marathi/Devanagari evidence answer,
and the inverse English question with a Marathi client locale returning an
English/Latin evidence answer. This dated observation is not current deployment
evidence; use [`../HANDOFF.md`](../HANDOFF.md) for that.
