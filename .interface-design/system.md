# PdfSearch Interface Design System

## Maharashtra Service public-search theme

- **Intent:** Help Maharashtra society members, office-bearers, auditors, and
  public officials ask one question and verify the answer on budget phones or
  desktops without learning a research tool.
- **Hierarchy:** The wide question composer is the primary action. Answers are
  readable documents; source evidence is the next level; service/help links are
  deliberately subordinate.
- **Palette:** Use the Commissionerate's blue civic identity: Maharashtra blue
  `#0262b6`, supporting blue `#0c67ad`, pale blue `#d4e8fc`, ice `#f7fcff`,
  charcoal `#24282b`, white, and restrained seal gold `#c68536`. Gold is an
  identity accent, never body text on white. Red is reserved for errors.
- **Depth:** Quiet layered shadows for the composer and answer documents;
  low-contrast dividers for navigation and source rows. Avoid dramatic shadows,
  glass effects, and heavy borders.
- **Surfaces:** Page ice background -> white answer/composer surface -> slightly
  raised interactive/source surface. Keep the single-hue blue family across
  levels.
- **Typography:** System sans for Latin and a Devanagari-capable system stack,
  with a self-hosted Noto Sans Devanagari fallback only if device testing proves
  necessary. Body text starts at 16px, uses 1.6 line height, and keeps answer
  copy near 72 characters per line.
- **Spacing:** 4px base grid. Typical controls use 12/16px internal spacing,
  content groups 24px, and major regions 32/48px. Mobile targets are at least
  44x44px with at least 8px between adjacent controls.

## Signature

The theme's signature is a broad official-blue masthead paired with a single
wide civic search desk. Each answer ends with an inline, expandable **Sources
used** folio instead of a permanent side rail.

## Stable interaction contracts

- Use one document scroll. Never lock `body` overflow or create a nested answer
  scroller.
- Use `min-height: 100dvh`; reserve mobile safe-area padding; keep the composer
  and footer reachable after every answer.
- Use semantic links, buttons, forms, headings, lists, tables, and `details`
  before custom ARIA behavior.
- Every control needs default, hover, active, focus-visible, disabled, loading,
  and error behavior where applicable.
- Respect reduced motion. Animate only opacity and transform, under 300ms.
- Keep Classic, Workbench, Maharashtra, admin, legal, and safe-error selectors
  isolated. A theme must load only its own search CSS and JavaScript.
- Read operational limits, including the maximum question length, from the
  rendered server contract; do not duplicate environment-backed values in CSS
  or JavaScript.
- Treat generated answer text as untrusted. Build structured output with DOM
  APIs, cap renderable payloads, and make only same-origin protected PDF routes
  clickable. Unknown model-generated URLs remain visible plain text.

## Deliberate exclusions

- No Suggested Questions.
- No Civic Knowledge Desk label.
- No “Ask about Maharashtra cooperative law” heading.
- No standalone WhatsApp or Feedback logo assets.
- No permanent evidence rail, generic chatbot dashboard, AI purple gradient,
  Bootstrap/CDN dependency, or new frontend framework.
