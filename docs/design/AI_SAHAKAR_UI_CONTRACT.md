# AI Sahakar UI Contract

**Status:** Active and protected
**Audience:** Product, design, frontend, QA, and coding agents
**Owner:** FlowDocs maintainers
**Last verified:** 2026-08-06
**Canonical source:** This document
**Supersedes:** Untracked visual proposals and active-looking historical UI plans

## Decision

AI Sahakar has two deliberately isolated public-search presentations:

- **Classic search** is the safe default and reproduces the approved
  `training.ai-sahakar.net` / `24june2026` service composition with modern,
  safe implementation patterns.
- **Knowledge workbench** is the secondary evidence-led research interface and
  remains available through a superadmin-selected default or the non-persistent
  `?view=workbench` URL override.

The authenticated admin console remains the Hallmark **Operations Cockpit**.
The Classic and Workbench public frontends share Django search, source, locale,
CSRF, authentication, and PDF-authorization contracts only. They do not share
templates, presentation CSS, or application JavaScript.

This contract is a design lock. A future change must either preserve the
principles below as an enhancement, or carry an explicit human request that
authorises a change in direction. A screenshot, framework preference, or agent
opinion is not authorisation.

## Protected product intent

The citizen journey is:

```text
choose a topic or ask a question → search official documents → read an answer
→ inspect sources → ask a follow-up question or contact the department
```

The source documents, not animation or branding, are the trust mechanism.
English is the default and Marathi is a complete supported interface. Existing
backend routes, CSRF, authentication, search response shape, PDF permissions,
feedback, WhatsApp, and locale boundaries remain unchanged.

The response extension is backward compatible: `answer` and `references`
remain stable while `kind` identifies the backend outcome and `language`
identifies the resolved answer language. Answer language follows the question,
not the current page locale. Provider output is dominant-script validated
before caching, repaired once when needed, and fails explicitly after a second
mismatch.

## Public search selection

`PUBLIC_SEARCH_PRIMARY_VIEW` is an allowlisted `SiteSetting`, not an environment
variable. A superadmin selects Classic or Workbench under **Settings → Public
search presentation**. Missing or invalid values fail closed to Classic.

`?view=classic` and `?view=workbench` are shareable, request-only previews. They
must not write a cookie, session value, database value, or deployment setting.
Unknown values fall back to the configured primary view. Language switching
preserves a valid explicit view query.

## Classic public search composition

The Classic presentation preserves the approved service identity and layout:

- dark utility navigation with Admin Login/Dashboard, Home, Locate Us, and the
  English/Marathi session switch;
- Maharashtra and national identity marks framing “AI Enabled Search” and
  “Registrar Co-operative Societies”;
- a quiet grey conversation canvas with the exact approved Sahakar AI help
  message and link;
- a bottom question composer, 30-word count, Search, WhatsApp, Feedback,
  legal-use warning, and department/partner footer.

Desktop composition follows the supplied 1920×1080 reference. Mobile retains
all utility actions, a usable single-row composer, no horizontal overflow, and
approximately 44px controls. The optimized WebP identity and assistant images
are approved exceptions to the Workbench's image restrictions.

Classic search uses `search_classic.html`, `search-classic.css`,
`search-classic.js`, and its own `components/public/classic_header.html`.
The header partial may also be used by Classic public-information pages; it is
never loaded by Workbench. Classic must not load Bootstrap/CDN resources, inline
application JavaScript, Workbench styles/scripts, or unsafe HTML rendering.

## Knowledge Workbench composition

| Viewport | Required composition |
| --- | --- |
| 1440px and wider | Official header; approximately 260px knowledge rail; flexible conversation workspace; approximately 320px evidence/help rail |
| 1024–1439px | Compact navigation rail and main workspace; evidence moves to an accessible drawer |
| 768–1023px | One main column; topics wrap or scroll; sources use accordion/drawer patterns |
| 320–767px | Compact sticky header, one conversation column, sources below answers or in a drawer, safe-area composer |

The main workspace must be useful in the initial viewport. The empty state is
upper-middle and contains “Ask AI Sahakar,” plain-language guidance, a trust
line, and four to six real question starters. The composer is cohesive,
labelled, immediately available, and reports a readable count such as
“6 of 30 words.”

An answer is a document-oriented response: user question, AI explanation,
source summary, contextual disclaimer, and functional actions. Source records
display only backend-provided metadata. Never invent page numbers, titles,
counts, verification labels, or excerpts.

Workbench search owns `search.html`, `civic-workbench.css`, `search.css`,
`search.js`, and `components/public/workbench_header.html`. Its header partial
may also be used by Workbench public-information pages; it is never loaded by
Classic.

## Public information composition

`/terms/`, `/privacy/`, `/disclaimer/`, `/data-policy/`, and `/cookies/` use the
same allowlisted primary/preview resolver as `/`. They render the selected
theme's own header partial inside `legal_base.html`, with shared policy
navigation and isolated `public-legal.css`/`public-legal.js`. They must not
inherit the authenticated admin base, Bootstrap, admin navigation, or either
search workspace's application JavaScript.

Valid explicit view previews remain on return-to-search, policy-navigation, and
locale-switch links. Invalid values are discarded and never reflected.
Canonical URLs omit preview queries. Policy article text remains explicitly
`lang="en"` until reviewed translations exist; a Marathi shell must not falsely
label English legal copy as Marathi. Tables keep captions, scoped headings, and
a keyboard-focusable horizontal scroll region on narrow screens.

## Admin console composition

The authenticated surface uses one official identity hierarchy: Registrar
Co-operative Societies / Maharashtra State, a compact administration label,
and a dark, restrained console navigation. The dashboard is the **Operations
Cockpit** and prioritises:

- operational summary metrics;
- Category Yard and recent intake;
- indexing/readiness and maintenance status;
- role-appropriate Configuration, Vault, Settings, Users, and Operations
  actions;
- explicit confirmation for destructive category/document actions.

Routine local document care and advanced data custody are separate operator
journeys. Dashboard readiness actions lead directly to **Documents & Search**.
Remote publication, generations, restore, activation, retention, and profile
controls belong to **Vault & Recovery** and use progressive disclosure. Local
validation, stored-index repair, and bounded reindexing must never appear to
require a remote Vault profile.

Role visibility is part of the product contract. Do not expose admin actions by
moving them into decorative menus or by bypassing existing authorization.

## Visual language

Use the named tokens in `flowdocs/core/static/main/css/civic-workbench.css`,
`search.css`, and the admin `style.css`. The direction is warm off-white
canvas, paper workspace, near-black text, stone borders, deep maroon
institutional accent, restrained ochre, and green success states.

Typography uses:

```css
Inter, "Noto Sans Devanagari", "Noto Sans", system-ui, sans-serif
```

These tokens and restrictions apply to the Workbench and admin surfaces. Do not
add arbitrary colours, decorative tricolour styling, government emblems, robot
imagery, gradients, glass surfaces, fake browser frames, or large visual assets
there. Classic uses only its approved optimized legacy identity assets. Prefer
grid, dividers, and typography over a stack of floating cards.
Marathi must not be uppercased, letter-spaced aggressively, clipped, or mixed
with English through concatenated fragments.

## Component and state contract

Every interactive component must retain understandable static states:

| Component | Required states and behavior |
| --- | --- |
| Header/nav | default, focus-visible, current route, compact/mobile, role-aware; Classic never hides the language action |
| Prompt starter | hover, focus-visible, active, disabled; keyboard activation and clean wrapping |
| Composer | default, hover, focus-visible, active, disabled, loading, error, success; 30-word limit |
| Loading status | visible stage label, polite live region, no fake progress or counts |
| Answer | complete accessible text, source summary, disclaimer, functional actions |
| Source card/drawer | missing metadata handled, long/Devanagari names wrap, Escape close, focus restore |
| Public information navigation | current page, valid theme preview preserved, invalid preview discarded, keyboard-visible focus, return to matching search view |
| Public information table | caption, scoped column headers, narrow-screen horizontal scroll, keyboard focus, print-safe layout |
| Error/no-result | plain-language cause, retry or next action, no raw exception |
| Admin action | permission-aware; explicit empty-scope behavior; confirmation for destructive work; success/error feedback; retry hidden or disabled while its prerequisite remains unmet |

## Machine evidence and operator language

Stable reason codes, safe-error codes, state-machine values, operation names,
job kinds, and audit actions are backend evidence. They are not interface copy.
Every operator-facing error or disabled control must explain, in equivalent
authored English and Marathi:

- what happened or why the control is unavailable;
- the operational consequence; and
- the safest next action.

An enabled button is a promise that its default form state has a valid,
documented meaning. Safe bounded discovery actions may define no selection as
all eligible records, but must say so beside the button. A force, destructive,
or explicitly selected action must require scope and must never silently widen
to all records. Server-side checks remain authoritative. When they refuse a
stale request, the response must preserve a stable reason and send the operator
to a real recovery destination. Do not offer retry while the current
capability check proves the same attempt will fail again.

Dashboard, public, and ordinary-user surfaces never display internal codes.
Authorized superadmins may reveal bounded, redacted evidence through the shared
collapsed **Technical details** component. Machine tokens are prohibited in
headings, badges, buttons, alerts, summaries, and screen-reader descriptions.
Technical codes use English language and left-to-right direction even on a
Marathi page. Generation IDs, hashes, timestamps, workspace IDs, and
correlation IDs may remain visible when they are necessary evidence.

All Dashboard and operational Workbench presentation resolves through
`core.operator_presentation`. Unknown tokens use neutral review guidance and
retain the exact token only inside Technical details; replacing underscores
with spaces is never an acceptable explanation.

Marathi operator copy is authored copy, not an English fallback. Translate a
term literally when Marathi has an established, unambiguous equivalent. When a
specialized product identity would become misleading if translated—such as
Vault, embedding, manifest, checkpoint, or garbage collection—use a consistent
Marathi-script term and explain its effect in ordinary Marathi. Ordinary
operational concepts are literal: `कार्यरत प्रणाली` for runtime,
`जोडणी रूपरेषा` for connection profile, `परिचालक` for operator, and
`मागील आवृत्ती पुनर्स्थापित करा` for a rollback action. Do not leave these
words in Latin script merely because they originated in English. The only
intentional English/LTR exceptions are immutable machine evidence: exact
codes, API field names, hashes, UUIDs, filenames, paths, versions, and
correlation identifiers.

Prefer precise Marathi vocabulary for ordinary concepts. For specialist
product and infrastructure concepts, use the reviewed Marathi-script forms:
व्हॉल्ट (Vault), रायटर लीज (writer lease), एम्बेडिंग (embedding),
मॅनिफेस्ट (manifest), चेकपॉइंट (checkpoint), and जीसी (garbage collection).
Explain the effect in ordinary Marathi. Do not substitute physical-vault or
property-lease vocabulary for a named technical concept.

Do not transliterate an ordinary operator word merely because the English
source is familiar to developers. Where the meaning is direct, use the literal
Marathi term consistently:

| English operator term | Required Marathi copy |
| --- | --- |
| unavailable | उपलब्ध नाही |
| warning | इशारा |
| failed | अयशस्वी |
| active | सक्रिय |
| previous | मागील |
| reason | कारण |
| action | कृती |
| result | परिणाम |
| observed | निरीक्षण केले |
| search readiness | शोध तयारी |
| technical details | तांत्रिक तपशील |

These human labels are translated; exact codes and identifiers remain
English/LTR technical evidence. Review the full sentence for grammar instead
of mechanically substituting individual words.

Every registry title, detail, consequence, action, and state label must have a
non-empty, non-fuzzy Marathi catalog entry before merge. Review older
Dashboard and Workbench translations in the same affected area; gettext fuzzy
matches are suggestions, not approved translations. Reviewers must check
semantic accuracy rather than accepting a catalog that only compiles. The
operator-language CI gate inventories the complete registry and rejects a
missing, fuzzy, empty, or unchanged-English Marathi entry.
Reviewed legacy-catalog corrections for specialist terms and contextual
file/media copy are exact-string contracts too. Pin corrected complete
sentences in the validator so `मॅनिफेस्ट`, `जीसी`, `चेकपॉइंट`, and ordinary
`संचिका` usage cannot silently regress to an older synonym or raw English.

Unavailable document media is an explicit, reversible operator state. The
interface must preserve the document identity and history, explain exclusion
from search/index/runtime readiness, require typed confirmation before marking
the file unavailable, collect expected digest/size plus a human reason and case
reference without accepting custody paths, and refuse restoration until
configured storage proves the exact,
stable, regular, non-symlink file. Idempotent requests must say that no state
changed. A successful restoration returns to the preserved prior lifecycle.
It must never infer or apply this state automatically.

Motion is utility only. Shared motion tokens are micro-interactions around
120–160ms and surface/state transitions around 180–240ms, using opacity and
transform rather than layout dimensions. `prefers-reduced-motion`, Save-Data,
slow connections, and low capability devices must resolve to light or reduced
behavior. Long answers render immediately on light/reduced profiles. Screen
readers receive one coherent completed answer, not character-by-character
noise.

## Accessibility and India-first requirements

- Preserve the correct HTML `lang` and complete English/Marathi catalogs.
- Keep focus-visible styling high contrast and keyboard order logical.
- Use real labels, landmarks, `aria-live="polite"`, and associated errors.
- Keep touch targets approximately 44×44px and do not use hover-only actions.
- Support Escape-to-close and focus restoration for mobile source drawers.
- Keep `html`/`body` horizontally safe with `overflow-x: clip`; fix layout
  tracks with `minmax(0, 1fr)` rather than hiding overflow defects.
- Validate 320×568 through 1920×1080, mobile landscape, and 200% zoom.
- Critical and serious axe violations must remain zero; core keyboard flows
  must remain unblocked.

## Change control

An enhancement is acceptable when it improves clarity, evidence access,
readability, accessibility, performance, or first-time usability while
preserving the contract. Before implementation, record the affected component,
state, breakpoint, locale impact, and backend boundary. After implementation,
update the relevant guide/tests and run the narrowest browser and Django gates.

The following are prohibited without explicit human direction: merging Classic
and Workbench frontend assets, removing either presentation, adding a frontend
SPA framework, changing the approved identity hierarchy of either view,
weakening source visibility, hiding the composer, introducing decorative
motion/assets beyond the approved Classic imagery, changing
search/auth/CSRF/PDF contracts, or making Marathi secondary.

## References

- [Implementation design record](shakar2-civic-workbench.md)
- [Public-search theme architecture](PUBLIC_SEARCH_THEME_ARCHITECTURE.md)
- [Developer guide](../AI_SAHAKAR_DEVELOPER_GUIDE.md)
- [Admin user guide](../AI_SAHAKAR_ADMIN_USER_GUIDE.md)
- [Public shell and evidence diagram](../diagrams/ui-shell-and-evidence.mmd)
- [UI change-control diagram](../diagrams/ui-change-control.mmd)
- [Dev cleanup scope](../DEV_CLEANUP_SCOPE.md)
