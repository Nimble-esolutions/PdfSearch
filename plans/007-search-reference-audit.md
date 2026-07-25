# Public Search Reference Audit

## Reference use

`/private/var/folders/y1/6v8l47y11hl50zw12yy18z5m0000gn/T/kilo/sahakar-ui-proposal.html` is a behavior reference only. Its colors, fixed width, mock data, emoji controls, and demo-only text are not adopted.

## Adopted interaction patterns

- Locale-aware loading status with three visible progress stages sourced through Django PO translations.
- Locale-aware prompt chips in the empty state that populate the real query field.
- Word-limit feedback integrated into the query field with OK, warning, and over-limit states.
- Dedicated error surface with explanatory text and a retry action.
- Clear source-reference cards that remain protected by the existing PDF URL boundary.

The PDF URL is a public behavior boundary, not a requirement that the source
bytes remain on the local Docker volume. Source cards should resolve through a
custody adapter and carry document-version, page/chunk, and generation
provenance when available; never expose object-store keys directly.
- Localized welcome guidance that explains the service without mixing English and Marathi in one view.

## Hallmark decisions

- Option A Search Desk is the only public search composition; B/C prototypes are removed.
- The supplied registrar seal remains the primary identity asset.
- The official UXDT National Emblem SVG is used as white artwork on a maroon identity tile; it is not recolored.
- WhatsApp retains its recognizable local asset. Feedback is rendered as an accessible labeled control instead of displaying the legacy `Rate Us` raster.
- Cookie consent remains available but participates in document flow so it cannot cover the chat surface.

## Verification contract

The root route must work at 390px and 1440px without horizontal overflow. The FAQ disclosure remains closed by default. Prompt selection, word counting, loading state, retry state, source links, public-search rate limits, and authentication boundaries require regression coverage.
