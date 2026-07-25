# AI Sahakar Civic Knowledge Workbench

## 2026-07-25 approved visual refinement: unified composer row

The screenshot audit identified a structural alignment defect in the public
search desk: the submit action was laid out beside the complete field block,
which included the label and word-count metadata. The action therefore looked
detached from the textarea and its arrow resembled an external-link affordance.

This refinement keeps the locked Workbench composition and backend contract,
but places the textarea and submit control in one shared control row. The
heading now uses the same reading gutter as the composer and states the
official-document/source relationship in the workspace itself. The official
status remains static and text-labelled, so it is not dependent on motion.

Affected surface: workspace heading and composer. Breakpoints: shared desktop
layout, tablet single-column layout, and mobile stack below 430px. States:
default, focus-visible, disabled, loading (`aria-busy`), error, and completed
search remain owned by the existing search script. Locale impact is limited to
existing translated strings; no new mixed-language copy was introduced.

Accessibility: the existing label, live word count, submit button semantics,
focus ring, and keyboard Enter behavior are preserved; the decorative icon is
hidden from assistive technology. Performance: one inline SVG replaces the
ambiguous text arrow and adds no request or asset dependency.

## 2026-07-25 follow-up: answer actions, formatting, and partner disclosure

Answer actions are part of the evidence workflow, not generic social buttons.
Copy must reflect the complete generated answer and report failure when the
browser clipboard is unavailable. Share uses the native device share sheet
when available so citizens choose WhatsApp, Telegram, Teams, or another
target; desktop fallback options are explicit and never target the department
contact number. Shared content includes the original question, formatted
answer, and backend-provided source-document links.

The answer renderer supports a deliberately small safe formatting subset:
paragraphs, headings, lists, and bold text. It builds DOM nodes and text
content; model output is never inserted as raw HTML. The complete answer
remains in the accessible DOM and long answers still use immediate rendering on
light/reduced profiles.

The public footer retains its closed height. “Technology partners” reveals an
accessible, keyboard/touch-friendly popover containing lightweight text
wordmarks and approved partner links. Partner logos remain approval-gated and
are not scraped or hotlinked. The department footer identity remains primary.

> **Canonical lock:** This implementation record is retained for context. The
> active, protected design and change-control rules are in
> [`AI_SAHAKAR_UI_CONTRACT.md`](AI_SAHAKAR_UI_CONTRACT.md).

## Design record

The public home route remains a Django-rendered, English-default civic document
search service. This redesign changes the information architecture and visual
composition only; the existing `/search/` request, CSRF protection, public
scope, authentication boundaries, protected PDF URLs, and locale context remain
unchanged.

## Users and intent

Citizens, cooperative-society members, office-bearers, auditors, researchers,
and public officials need a calm way to ask about Maharashtra cooperative law
and inspect the documents behind an answer. The primary journey is: choose a
topic or ask a question, receive an explanation, then inspect the evidence.

## Current defects observed

- A single large card leaves a dead central area at widescreen sizes.
- The introduction, prompt chips, composer, support links, legal warning, and
  document count read as unrelated blocks.
- The header presents department identity and product identity as competing
  mastheads, with cramped public navigation.
- The robot asset makes the civic service feel like a generic chatbot.
- Sources have no persistent evidence surface before or after an answer.
- The cookie notice can obscure the empty state and composer, especially on
  mobile.

## Hallmark direction

- Macrostructure: Workbench.
- Theme: Civic Knowledge Workbench.
- Philosophy: official, evidence-led, quiet, and useful before decorative.
- Surfaces use warm paper, white workspace, maroon institutional accents, and
  restrained ochre focus/progress states.
- Layout relies on grid, dividers, and typography rather than a stack of
  floating rounded cards. No gradients, robot branding, invented metrics, or
  decorative government motifs are introduced.

## Information architecture

- Unified header: department identity, AI Sahakar product label, compact public
  navigation, language control, and secondary admin access.
- Knowledge rail: new question, common topics, useful-question guidance, and
  human/official help.
- Conversation workspace: empty state or query/answer history with a sticky
  composer.
- Evidence rail: how the service works before an answer; source documents,
  answer context, and support after an answer.
- On tablet/mobile the rails collapse into topic rows and an accessible source
  drawer; sources also appear inline below an answer.

## Responsive behavior

- 1440px+: three columns, approximately 260px / minmax(0, 1fr) / 320px.
- 1024–1439px: compact left rail and main workspace; evidence becomes a drawer.
- 768–1023px: one main column with horizontal topic navigation and source drawer.
- 320–767px: sticky compact header, single-column conversation, inline sources,
  safe-area composer, and no hover-only actions.

## Conversation and source decisions

Answers remain text-node rendered from the existing JSON response. References
use only fields returned by the backend (`title`, `folder`, `uploaded_at`,
`page`, `excerpt`, and protected `url` when supplied); missing metadata is not
fabricated. Desktop evidence is a persistent rail; smaller layouts expose the
same records through a labelled drawer and inline summary.

## English/Marathi mapping

All visible copy is Django locale context or translated template copy. The
client receives translated labels through `json_script`; it never concatenates
translated fragments and never changes user-entered questions. The document
language remains `en` or `mr` for the existing endpoint.

## Accessibility and interaction

- One main landmark, labelled navigation/rails, logical heading order, and
  visible focus rings.
- Composer uses an accessible textarea and descriptive word count (`6 of 30
  words`).
- Loading/error status uses polite live regions without character-by-character
  announcements.
- Source drawer supports Escape, focus restoration, and a labelled close action.
- Reduced motion disables nonessential transitions.

## Performance budget

- No animation dependency or new large asset.
- Keep the public CSS/JS additions focused on the existing route.
- Preserve fallback-first system Devanagari rendering and the existing adaptive
  answer profile.
- Validate at 320, 375, 390, 414, 768, 1024, 1280, 1440, and 1920 widths with
  no horizontal overflow or sticky-element obstruction.

## Protected boundaries

No backend search, embedding, OpenAI policy, data lifecycle, RustFS, writer,
scheduler, or protected operational file changes are part of this design.
