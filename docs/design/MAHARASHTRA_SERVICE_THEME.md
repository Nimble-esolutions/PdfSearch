# Maharashtra Service Theme

**Status:** Active design and implementation contract
**Audience:** Product, design, frontend, QA, maintainers, and coding agents
**Owner:** FlowDocs maintainers
**Last verified:** 2026-08-08
**Canonical visual reference:** [Commissioner for Cooperation and Registrar,
Cooperative Societies, Maharashtra](https://sahakarayukta.maharashtra.gov.in/Site/Home/Index.aspx)

## Purpose

Maharashtra Service is the third isolated AI Sahakar public-search theme. It is
for society members, office-bearers, auditors, public officials, and other
visitors who need to ask one question and inspect the supporting documents on a
budget phone or desktop without learning a research workbench.

It combines:

- Classic's wide, immediately understandable question-and-answer composition;
- Workbench's structured answers, typed loading/recovery states, safe source
  handling, and evidence-first trust boundary; and
- the Maharashtra Commissionerate's blue civic identity.

It does not copy the official portal's dense multi-column layout. Its identity
is official and familiar; its interaction model is a modern single-task search
desk.

## Design direction

| Token role | Value | Use |
| --- | --- | --- |
| Maharashtra blue | `#0262b6` | Primary masthead, primary action, active navigation |
| Supporting blue | `#0c67ad` | Secondary emphasis and source controls |
| Pale blue | `#d4e8fc` | Selected/expanded civic surfaces |
| Ice | `#f7fcff` | Page canvas |
| Charcoal | `#24282b` | Utility navigation and primary text |
| White | `#ffffff` | Answer, composer, and document surfaces |
| Seal gold | `#c68536` | Restrained identity accent only |
| Error red | semantic token | Errors and destructive warnings only |

Gold is not body text on white. Blue links and controls must meet contrast in
every state. The theme uses quiet dividers and restrained shadows, not glass,
large floating card stacks, purple AI gradients, decorative tricolour bands, or
fake government-verification seals.

Typography begins with the system UI stack and a Devanagari-capable fallback.
Self-host Noto Sans Devanagari only if device testing demonstrates a real
shaping or coverage gap. Do not introduce a runtime font CDN. Body text starts
at 16px with approximately 1.6 line height; answer prose stays near 72
characters per line while answer and source surfaces may remain wide.

## Information architecture

```text
charcoal utility bar
  -> bilingual official identity masthead
  -> simple public navigation
  -> wide conversation and answer document
       -> question
       -> structured answer
       -> inline Sources used folio
  -> wide question composer
  -> How to Ask / WhatsApp contact / Feedback service actions
  -> policy and attribution footer
```

The utility/navigation layer keeps Home/Search, Locate Us, English/Marathi, and
role-aware Login/Dashboard available without becoming a second toolbar. The
existing How to Ask, WhatsApp, Feedback, Locate Us, and protected-document URLs
are reused; the theme renders them as labelled controls with one coherent
line-icon treatment and visible external-link semantics.

There are no standalone WhatsApp or Feedback logo assets. The service name may
appear in accessible link text, but its brand image is not a floating action.

## Search and answer contract

- The wide composer is the primary action and retains the server-defined word
  limit, CSRF, loading, disable/re-enable, validation, and rate-limit behavior.
- Use one document scroll. Never lock `body`, hide the footer, or put long
  answers in a nested transcript scroller.
- A second question must remain possible after short, long, error, and
  no-evidence responses.
- Render structured answers with safe DOM nodes and text nodes. Never pass model
  output to unsanitized `innerHTML`.
- Preserve semantic headings, paragraphs, unordered and ordered lists, bold
  emphasis, links, and responsive tables from the allowlisted response format.
- Apply the backend `language` to answer content. Theme or page locale must not
  override the question-derived answer language.
- Evidence answers end with an inline, expandable **Sources used** folio.
  Source titles and URLs come only from backend records; never invent pages,
  excerpts, counts, official verification, or document metadata.
- Loading, validation, no-evidence, rate-limit, network, malformed-response, and
  server-error states explain what happened and the next safe action.
- Copy answer, open source, retry, and new-question actions must be keyboard and
  touch operable and synchronize with request state.

## Explicit exclusions

Maharashtra Service must not contain:

- Suggested Questions;
- the Civic Knowledge Desk label;
- the phrase “Ask about Maharashtra cooperative law”;
- a permanent evidence rail;
- standalone WhatsApp or Feedback logo assets;
- a generic chatbot dashboard, oversized marketing hero, or floating robot;
- Bootstrap/CDN presentation dependencies, inline application JavaScript, a new
  frontend framework, or Workbench/Classic selectors; or
- a claim that a generated answer is formally approved by government.

## Official identity assets

The Maharashtra seal and national emblem remain separate official marks. Use an
authoritative vector source where available. Otherwise, make a faithful manual
vector reconstruction from the approved reference; do not use generative image
reconstruction or redesign symbols, wording, geometry, proportions, or color.

Every delivered SVG must:

- remove editor metadata, hidden objects, embedded bitmaps, and redundant paths;
- define an explicit `viewBox`, intrinsic dimensions, and reserved layout size;
- preserve recognizable geometry at mobile masthead size;
- expose appropriate alternative text through the surrounding markup rather
  than duplicating inaccessible SVG text;
- receive an optimized PNG/WebP fallback only where browser/device evidence
  requires one; and
- pass side-by-side identity review before release.

The combined compressed masthead identity target is 50KB or less. A fidelity
exception requires a recorded reason, measured transfer size, and performance
review. Official marks are identity, not decorative content, and must not be
used as a verification badge on answers.

## Responsive and accessibility contract

| Viewport or mode | Required behavior |
| --- | --- |
| 1440px and wider | Centered wide civic desk; answer prose retains readable measure; no permanent evidence rail |
| 768–1439px | Fluid single column; service actions wrap without hiding labels |
| 320–767px | Compact identity/nav, full-width composer, at least 44x44px targets, sources below answer |
| Compact landscape | Preserve composer and question path; reduce nonessential spacing without hiding required actions |
| 200% zoom/reflow | No clipped controls, horizontal page scroll, or inaccessible source content |
| Reduced motion / Save-Data | No decorative motion; content and status appear immediately |

Additional requirements:

- visible high-contrast `:focus-visible` treatment;
- semantic landmarks, heading order, labels, status messages, and
  `aria-live="polite"` for asynchronous updates;
- keyboard order matching visual order;
- no hover-only functionality;
- safe-area padding and virtual-keyboard-aware composer behavior;
- Devanagari text that is not uppercased, aggressively letter-spaced, clipped,
  or assembled from English fragments; and
- zero serious or critical Axe findings in required browser states.

## Performance contract

No SPA framework, Bootstrap, animation library, external font CDN, or large icon
package is introduced for this theme.

| Budget | Target |
| --- | --- |
| Theme CSS | 35KB or less uncompressed, unless measured evidence documents an exception |
| Theme JavaScript | 20KB or less uncompressed |
| Compressed masthead marks | Target 50KB or less combined |
| LCP | 2.5s or less at p75 |
| INP | 200ms or less at p75 |
| CLS | 0.1 or less at p75 |

Validate with constrained mobile network and CPU throttling as well as normal
desktop conditions. Reserve dimensions for identity marks and asynchronous
surfaces. Analytics remains consent-led and must never block rendering,
interaction, readiness, search, or error recovery.

## Theme selection and ownership

The existing allowlisted `PUBLIC_SEARCH_PRIMARY_VIEW` setting accepts
`maharashtra`; no new environment key or migration is introduced.

- `/?view=maharashtra` is a shareable, request-only preview.
- The preview never writes a cookie, session, SiteSetting, or environment value.
- Language, legal, and return-to-search links preserve a valid explicit preview.
- Invalid values fail to the effective primary and ultimately to Classic.
- If the environment key is absent, Settings may own the saved selection.
- If the environment key is defined, deployment owns it and Settings is
  read-only.

Classic, Workbench, and Maharashtra Service remain independent frontends. A
theme repair must not modify another theme merely to share presentation code.
Only backend/security contracts may be shared.

## Validation and release evidence

Before making Maharashtra Service primary, capture:

1. Django resolver, persistence, ENV-ownership, permission, invalid-value,
   preview, policy-page, safe-error, and asset-isolation tests.
2. Browser evidence for empty, loading, evidence, no-evidence, validation,
   malformed, network, rate-limit, and server-error states.
3. Long-answer and second-question continuity at 320, 360, 390, 412, 768, 1024,
   1366, 1440, and 1920 widths plus compact landscape.
4. English/Marathi shell switching and mismatched page/query language cases.
5. Keyboard, screen-reader, 200% zoom/reflow, reduced-motion, and Axe checks.
6. Link assertions for How to Ask, WhatsApp, Feedback, Locate Us, policies, and
   protected documents without standalone brand-logo assets.
7. Static assertions for the excluded labels/elements and cross-theme assets.
8. Asset transfer sizes, Core Web Vitals, throttled CPU/network evidence, and
   visual screenshots of the required states.
9. Stage preview canary from the exact built image before changing the saved or
   deployment-owned primary selection.

## Rollback and repair handoff

Presentation rollback is data-neutral:

1. Preview Classic and verify one search plus one protected source.
2. Restore Classic through Settings when SiteSetting-owned, or through the
   existing deployment key when ENV-owned.
3. Recheck `/`, legal pages, public errors, locale switching, a long answer, and
   a second question.
4. Record the exact image/revision and canary result in `docs/HANDOFF.md`.

Do not alter documents, indexes, volumes, recovery points, signed generations,
or search provider configuration to recover a presentation defect. Preserve the
failing theme and browser evidence until root-cause review is complete. The
architecture source is
[`../diagrams/public-search-three-theme-architecture.mmd`](../diagrams/public-search-three-theme-architecture.mmd).
