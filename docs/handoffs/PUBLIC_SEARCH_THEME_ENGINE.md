# Public Search Theme Engine Handoff

**Updated:** 2026-08-07
**Status:** Historical / completed delivery handoff
**Superseded by:** [`../HANDOFF.md`](../HANDOFF.md) for current deployment
evidence and [`../design/AI_SAHAKAR_UI_CONTRACT.md`](../design/AI_SAHAKAR_UI_CONTRACT.md)
for the active UI contract

This file records why the original dual-theme delivery and the later isolated
Maharashtra Service extension were shaped as they were. It contains no current
image, rollout, or pending-release authority.

## Maharashtra Service extension (2026-08-08)

The theme engine now defines three isolated presentations:

- Classic remains the safe default and fail-closed recovery view;
- Knowledge Workbench remains the evidence-led secondary view; and
- Maharashtra Service adds the official-blue, India-first wide search view at
  `/?view=maharashtra`.

The extension reuses the existing `PUBLIC_SEARCH_PRIMARY_VIEW` contract and
adds only the allowlisted value `maharashtra`. It adds no new environment key,
database migration, search endpoint, PDF-authorization path, or data/recovery
behavior. Request previews are non-persistent. ENV owns the choice when the
existing key is explicitly defined; otherwise the role-authorized Settings
control can persist the selection.

Maharashtra Service has its own template, header, stylesheet, JavaScript, and
optimized official identity assets. It shares only backend/security contracts.
It combines Classic's wide conversation layout with Workbench's structured
answer and evidence behavior, presenting sources inline rather than in a
permanent rail. It does not include Suggested Questions, Civic Knowledge Desk,
“Ask about Maharashtra cooperative law”, standalone WhatsApp/Feedback logos, or
another theme's assets. Existing Help, WhatsApp, Feedback, Locate Us, policy,
and protected-document destinations remain available as labelled theme-native
controls.

Before selecting it as primary, certify empty/loading/answer/error states, long
answers and a second question, English/Marathi behavior, all service links,
asset isolation, 320px and compact-landscape layouts, accessibility, constrained
mobile performance, public information pages, and safe public errors from the
exact release image. Record deployment evidence in the living handoff.

Rollback is presentation-only: first verify `/?view=classic`, then restore
Classic through Settings when SiteSetting-owned or through the reviewed existing
environment key when ENV-owned. Recheck search, one protected source, locale,
legal/error pages, and second-question continuity. Do not alter documents,
indexes, volumes, recovery points, or signed generations to recover a theme.

Canonical contracts:

- [`../design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md`](../design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md)
- [`../design/MAHARASHTRA_SERVICE_THEME.md`](../design/MAHARASHTRA_SERVICE_THEME.md)
- [`../diagrams/public-search-three-theme-architecture.mmd`](../diagrams/public-search-three-theme-architecture.mmd)
  ([rendered SVG](../diagrams/public-search-three-theme-architecture.svg))

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

## Original two-theme delivery (historical)

- Classic is the safe default and matches the approved training/24 June
  composition with optimized local WebP assets and no CDN Bootstrap or inline
  application JavaScript.
- Workbench remains intact at `/?view=workbench`.
- At that delivery revision, superadmins could persist Classic or Workbench
  under Settings; no ENV or deployment change was required. The current
  allowlist also includes Maharashtra Service as described above.
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
- Public-information coverage at that revision covered both then-existing themes
  for canonical/asset isolation, responsive overflow, allowlisted preview
  propagation, and serious/critical accessibility checks. Maharashtra Service
  requires its own equivalent evidence before rollout.
- Visual review: Classic 1920×1080, Classic 390×844, Workbench 1440×900.

## Deployment and rollback

Existing databases required no theme-engine migration. Classic is the default
unless a superadmin saves Workbench, and either view remains available through
its request-only `?view=` override.

Rollback by selecting Workbench in Settings, or by reverting the feature PR.
Neither action changes documents, indexes, search sources, backups, or runtime
generations.

### 2026-08-07 local re-check (requested)

Current local state has reverted to the classic layout intentionally:

- Branch: `feat/persistent-stage-analytics`
- Working tree: clean (`git status --short` empty)
- Running service: `pdfsearch-web-1` on `127.0.0.1:8000`
- Homepage marker checks confirm:
  - body class is `classic-search`
  - `/static/main/css/search-classic.css` is linked from root route
- Served static hashes currently match repo hashes after rebuild:
  - `flowdocs/core/static/main/css/search-classic.css` -> `ff87998a5507136be6d4107a4f56f49406133c39`
  - `flowdocs/core/static/main/js/search-classic.js` -> `65ac3772ae4c3a7151d57d2d9da1e61988d916c4`

If this “old UI” is observed again, use this decision path:

1. **Quick recovery (no UX behavior change):**
   - restart/rebuild web to flush stale container cache:
     - `docker compose -f docker-compose.dev.yml build --no-cache web`
     - `docker compose -f docker-compose.dev.yml up -d --force-recreate --no-deps web`
     - hard-refresh browser cache.
2. **Reapply the compact/viewport UX improvement bundle:**
   - `git revert 76c1e39` (replays `df2552e`)
   - run the same rebuild + recreate steps above
3. **Force-revert to reverted baseline:**
   - `git reset --hard 76c1e39`
   - rebuild/recreate + hard-refresh browser cache

Notes:
- The reverted baseline currently includes `components/public/classic_header.html`
  and `components/public/classic_footer.html`.
- The commit sequence that introduced/stabilized the reverted viewport fixes is:
  `a346a11` → `31d2692` → `d5afb79` → `df2552e`.
- The explicit revert is commit `76c1e39`, which is why the old UI returned.

## Historical language evidence

The delivery replaced substring conversational matching with normalized exact
intent, question-derived answer language, provider-output validation, and one
bounded repair attempt. A stage rehearsal showed a Marathi document question
with an English client locale returning a Marathi/Devanagari evidence answer,
and the inverse English question with a Marathi client locale returning an
English/Latin evidence answer. This dated observation is not current deployment
evidence; use [`../HANDOFF.md`](../HANDOFF.md) for that.
