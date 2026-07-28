# AI Sahakar UI Contract

**Status:** Active and protected
**Audience:** Product, design, frontend, QA, and coding agents
**Owner:** FlowDocs maintainers
**Last verified:** 2026-07-25
**Canonical source:** This document
**Supersedes:** Untracked visual proposals and active-looking historical UI plans

## Decision

AI Sahakar uses the **Civic Knowledge Workbench** theme and the Hallmark
**Workbench** macrostructure. The public search page and the authenticated
admin console are official, calm, evidence-first workspaces. They are not
generic AI landing pages, consumer chat bubbles, or marketing surfaces.

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

## Public search composition

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

Do not add arbitrary colours, decorative tricolour styling, government emblems,
robot imagery, gradients, glass surfaces, fake browser frames, or large visual
assets. Prefer grid, dividers, and typography over a stack of floating cards.
Marathi must not be uppercased, letter-spaced aggressively, clipped, or mixed
with English through concatenated fragments.

## Component and state contract

Every interactive component must retain understandable static states:

| Component | Required states and behavior |
| --- | --- |
| Header/nav | default, focus-visible, current route, compact/mobile, role-aware |
| Prompt starter | hover, focus-visible, active, disabled; keyboard activation and clean wrapping |
| Composer | default, hover, focus-visible, active, disabled, loading, error, success; 30-word limit |
| Loading status | visible stage label, polite live region, no fake progress or counts |
| Answer | complete accessible text, source summary, disclaimer, functional actions |
| Source card/drawer | missing metadata handled, long/Devanagari names wrap, Escape close, focus restore |
| Error/no-result | plain-language cause, retry or next action, no raw exception |
| Admin action | permission-aware, confirmation for destructive work, success/error feedback |

## Machine evidence and operator language

Stable reason codes, safe-error codes, state-machine values, operation names,
job kinds, and audit actions are backend evidence. They are not interface copy.
Every operator-facing error or disabled control must explain, in equivalent
authored English and Marathi:

- what happened or why the control is unavailable;
- the operational consequence; and
- the safest next action.

Dashboard, public, and ordinary-user surfaces never display internal codes.
Authorized superadmins may reveal bounded, redacted evidence through the shared
collapsed **Technical details** component. Machine tokens are prohibited in
headings, badges, buttons, alerts, summaries, and screen-reader descriptions.
Technical codes use English language and left-to-right direction even on a
Marathi page. Generation IDs, hashes, timestamps, workspace IDs, and
correlation IDs may remain visible when they are necessary evidence.

All Dashboard and Workbench presentation resolves through
`core.operator_presentation`. Unknown tokens use neutral review guidance and
retain the exact token only inside Technical details; replacing underscores
with spaces is never an acceptable explanation.

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

The following are prohibited without explicit human direction: replacing the
Workbench with a centred hero, adding a frontend SPA framework, changing the
official identity hierarchy, weakening source visibility, hiding the composer,
introducing decorative motion/assets, changing search/auth/CSRF/PDF contracts,
or making Marathi secondary.

## References

- [Implementation design record](shakar2-civic-workbench.md)
- [Developer guide](../AI_SAHAKAR_DEVELOPER_GUIDE.md)
- [Admin user guide](../AI_SAHAKAR_ADMIN_USER_GUIDE.md)
- [Public shell and evidence diagram](../diagrams/ui-shell-and-evidence.mmd)
- [UI change-control diagram](../diagrams/ui-change-control.mmd)
- [Dev cleanup scope](../DEV_CLEANUP_SCOPE.md)
