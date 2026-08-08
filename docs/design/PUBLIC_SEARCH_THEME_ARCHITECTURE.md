# Public Search Theme Architecture

**Status:** Active
**Owner:** FlowDocs maintainers
**Last verified:** 2026-08-08
**Scope:** Public search, public information, and safe public-error presentation

## Outcome

AI Sahakar has three isolated public presentations over one secured backend:

- **Classic** is the safe default and fail-closed recovery presentation.
- **Knowledge Workbench** is the evidence-led research presentation.
- **Maharashtra Service** is the official-blue, India-first service
  presentation with a wide conversation layout and inline evidence.

They share request context, search/PDF authorization, locale, CSRF, typed answer,
and protected-source contracts. They do not inherit or load one another's
templates, presentation CSS, application JavaScript, or theme identity assets.

## Request decision flow

```text
GET /?view=classic|workbench|maharashtra
        │
        ├─ valid query value ──> render requested view for this response only
        │
        └─ absent/invalid ─────> resolve PUBLIC_SEARCH_PRIMARY_VIEW
                                  │
                                  ├─ explicit ENV value, when defined
                                  ├─ otherwise saved SiteSetting
                                  ├─ otherwise application default
                                  └─ missing/invalid ──> Classic
```

The query override writes no cookie, session, database value, or deployment
setting. The existing `PUBLIC_SEARCH_PRIMARY_VIEW` key may hold `classic`,
`workbench`, or `maharashtra`; the third theme introduces no new environment
variable and no migration. When the key is absent, the role-authorized Settings
control may persist an allowlisted SiteSetting. When it is present, ENV owns the
control and Settings is read-only.

## Isolation map

| Boundary | Classic | Knowledge Workbench | Maharashtra Service | Shared |
| --- | --- | --- | --- | --- |
| Django search template | `search_classic.html` | `search.html` | `search_maharashtra.html` | View context contract |
| Public header | Classic partial | Workbench partial | Maharashtra partial | No shared theme header |
| Presentation CSS | `search-classic.css` | `civic-workbench.css`, `search.css` | Dedicated Maharashtra stylesheet | No cross-theme selectors |
| Application JS | `search-classic.js` | `search.js` | Dedicated Maharashtra script | Search API only |
| Layout | Wide legacy service composition | Multi-region evidence workspace | Wide civic search desk with inline sources | None |
| Public information | Classic shell identity | Workbench shell identity | Maharashtra shell identity | Policy articles and legal-only assets |
| Safe public errors | Classic recovery identity | Workbench recovery identity | Maharashtra recovery identity | Error semantics; no tracker/search script |
| Search POST | Existing typed request | Existing typed request | Existing typed request | `search_query` backend |
| Sources/PDFs | Safe DOM records | Evidence records/drawer | Inline expandable source folio | Protected backend URLs |
| Interface language | Django locale session | Django locale session | Django locale session | English/Marathi catalog |
| Answer language | Backend `language` | Backend `language` | Backend `language` | Question-derived and script-validated |
| Security | CSRF, escaping, URL allowlist | CSRF, escaping, URL allowlist | CSRF, escaping, URL allowlist | Django policy |

The Maharashtra theme may reuse the backend-provided URLs for How to Ask,
WhatsApp contact, Feedback, Locate Us, and protected documents. Reusing a
destination does not authorize importing Classic's raster action logos or
Workbench UI. Each link is rendered as a labelled, theme-native control.

## Public information and safe errors

`/privacy/`, `/terms/`, `/data-policy/`, `/cookies/`, and `/disclaimer/` use the
same allowlisted resolver as `/`. They render the selected theme's identity in a
standalone legal-document shell and do not inherit the authenticated admin base,
Bootstrap, or any search workspace's JavaScript.

A valid `?view=classic|workbench|maharashtra` remains on return-to-search,
policy-navigation, and locale-switch URLs. Unknown values are discarded and
never reflected. Canonical URLs omit the preview query. Policy article text
remains explicitly `lang="en"` until reviewed translations exist; responsive
tables retain captions, scoped headings, and a keyboard-focusable narrow-screen
scroll region.

Standard public 400, 403, 404, and 500 responses use the matching theme identity
without loading the composer, search script, analytics tracker, or failed URL.
If theme resolution itself fails, the recovery path fails closed to Classic.

## Shared response contract

All themes POST the same question and render the same backward-compatible JSON
shape. `answer` and `references` remain stable; `kind` exposes the backend
outcome and `language` identifies the resolved answer language.

| `kind` | Meaning | Source behavior |
| --- | --- | --- |
| `small_talk` | Exact standalone greeting, thanks, identity, or unsupported live date/time request | Always empty |
| `evidence_answer` | Answer grounded in indexed documents | One or more protected source records |
| `no_evidence` | Document search ran but found no support | Empty; show authored refinement guidance |
| `validation` | Empty or otherwise incomplete request | Empty |
| `error` | Policy, rate-limit, integrity, or unexpected failure | Empty; use safe server detail |

Small-talk matching is Unicode-normalized and whole-query only. Answer language
follows the question, not the selected theme or page locale. Provider output is
dominant-script validated before caching, repaired once when needed, and fails
explicitly after a second mismatch. Themes render the contract; they do not
reclassify the request or invent evidence.

## Admin and URL behavior

| Scenario | Result |
| --- | --- |
| Fresh database | Classic |
| Saved `classic` / `workbench` / `maharashtra` | Corresponding theme |
| Valid deployment-owned value | Corresponding theme; Settings field locked |
| Corrupt or unknown effective value | Classic |
| `/?view=maharashtra` | Maharashtra Service for that request; no persistence |
| `/?view=workbench` | Workbench for that request; no persistence |
| `/?view=classic` | Classic for that request; no persistence |
| Unknown query value | Effective primary, otherwise Classic |
| English/Marathi switch | Locale changes and valid explicit `view` remains |
| Public information route | Effective or valid preview theme; standalone shell |
| Safe public error | Matching identity when available; otherwise Classic |

## India-first presentation contract

- Operate from 320px upward and preserve document scrolling in portrait and
  compact landscape.
- Keep the composer and footer reachable after long answers and repeated
  questions; never lock `body` or create a nested answer scroller.
- Use at least 16px body copy, Devanagari-capable font fallbacks, approximately
  72-character answer measure, and at least 44x44px touch targets.
- Preserve visible focus, semantic landmarks, real labels, polite live regions,
  200% zoom/reflow, reduced motion, and zero serious/critical Axe findings.
- Test constrained networks and budget-class Android CPU throttling. Maharashtra
  Service targets LCP at or below 2.5s, INP at or below 200ms, and CLS at or
  below 0.1 at the 75th percentile.

## Impact, rollback, and handoff

The third theme changes presentation selection and static identity only. It adds
no migration, new ENV key, Compose change, data-custody change, search-model
change, OCR change, backup/restore change, or activation change.

Operational rollback is immediate and data-neutral:

1. Preview `/?view=classic` and confirm search/source continuity.
2. If Settings owns the value, save Classic as primary. If ENV owns it, restore
   the reviewed existing `PUBLIC_SEARCH_PRIMARY_VIEW` value and redeploy.
3. Verify `/`, public information routes, one evidence answer, a protected PDF,
   English/Marathi switching, and the composer after a second question.
4. Record the image/revision and result in the living handoff. Do not alter
   documents, indexes, data volumes, recovery points, or runtime generations.

A failed theme render must never mutate search data or the saved primary. A code
rollback may remove the Maharashtra allowlist/template/assets after Classic is
selected; the independent Classic and Workbench bundles remain valid.

## Review gates

- Django tests prove default, persistence, ENV ownership, permissions, invalid
  values, preview propagation, public pages, safe errors, and isolation.
- Browser tests prove all three themes, typed outcomes, safe source rendering,
  long answers, second questions, English/Marathi behavior, and 320px layout.
- Asset assertions prove each route loads only its theme bundle and that no
  standalone WhatsApp/Feedback logo or excluded Workbench element is present in
  Maharashtra Service.
- Accessibility covers keyboard, focus restoration, zoom/reflow, reduced motion,
  responsive source disclosure, and serious/critical Axe findings.
- Performance covers asset budgets, low-capability CPU/network profiles, and
  Core Web Vitals targets without making analytics an availability dependency.
- Visual review covers empty, loading, answer, no-evidence, validation, error,
  long-answer, desktop, tablet, portrait-mobile, and compact-landscape states.

## Diagrams and related contracts

- [Three-theme architecture source](../diagrams/public-search-three-theme-architecture.mmd)
  ([rendered SVG](../diagrams/public-search-three-theme-architecture.svg))
- [Public shell and answer-language flow](../diagrams/ui-shell-and-evidence.mmd)
  ([rendered SVG](../diagrams/ui-shell-and-evidence.svg))
- [Maharashtra Service theme](MAHARASHTRA_SERVICE_THEME.md)
- [Active UI contract](AI_SAHAKAR_UI_CONTRACT.md)
- [Theme-engine handoff](../handoffs/PUBLIC_SEARCH_THEME_ENGINE.md)
