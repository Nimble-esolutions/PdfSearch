# Plan 007: Reconcile public references, answer actions, and evidence UX

> **Executor instructions**: The source rail, copy action, and multi-channel
> sharing are implemented. Verify they use the actual selected question,
> rendered answer, and protected source links; close residual gaps without
> fabricating citation metadata.

## Status

- **Priority**: P1
- **Effort**: S/M
- **Risk**: MED
- **Depends on**: Plans 005 and 006 gate
- **Category**: correctness / accessibility
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: RECONCILE

## Drift check

```bash
git diff --stat f742b59..HEAD -- \
  flowdocs/core/templates/search.html \
  flowdocs/core/static/main/js/search.js \
  flowdocs/core/views.py flowdocs/core/tests.py browser_tests
```

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

## Current implementation evidence

- Commits `b2e9f8c` and `24b81ae` introduced answer-aware formatting/sharing
  and localized hardening.
- The current search view returns protected references rather than raw storage
  paths.
- The remaining architectural gap is stable document-version/page/chunk/
  generation provenance, which belongs to Plans 009, 011, and 012. This plan
  must not invent those fields before the backend can prove them.

## Hallmark decisions

- Option A Search Desk is the only public search composition; B/C prototypes are removed.
- The supplied registrar seal remains the primary identity asset.
- The official UXDT National Emblem SVG is used as white artwork on a maroon identity tile; it is not recolored.
- WhatsApp retains its recognizable local asset. Feedback is rendered as an accessible labeled control instead of displaying the legacy `Rate Us` raster.
- Cookie consent remains available but participates in document flow so it cannot cover the chat surface.

## Verification contract

The root route must work at 390px and 1440px without horizontal overflow. The FAQ disclosure remains closed by default. Prompt selection, word counting, loading state, retry state, source links, public-search rate limits, and authentication boundaries require regression coverage.

## Reconciliation steps

1. Test two different answers in one conversation. Copy/share each and confirm
   the selected question, actual answer text, and its own sources are used.
2. Verify WhatsApp and Telegram use chooser/share URLs without a fixed
   recipient; Teams and native share follow documented browser behavior.
3. Test long English/Marathi titles, missing optional metadata, protected link
   access, popup blockers, clipboard denial, offline/network errors, and source
   drawer focus restoration.
4. Verify answer formatting safely handles headings/lists/emphasis without
   rendering untrusted HTML or exposing markdown markers such as `**`.
5. Add source provenance fields only through Plan 012 schemas and Plan 009
   retrieval results; retain current protected URL behavior through an adapter.

## Commands, scope, and done criteria

```bash
python manage.py test core.tests.SearchAndAuthenticationTests \
  core.tests.LanguageAndPublicUiTests
node --check flowdocs/core/static/main/js/search.js
npx playwright test
git diff --check
```

In scope: answer action state, safe formatting, source controls, localized
labels, and tests. Out of scope: fabricated page numbers/excerpts, raw object
keys, direct storage URLs, retrieval ranking, and model prompts.

Done means copied/shared content is derived from the selected answer, source
links remain protected, no fixed WhatsApp recipient is imposed, screen-reader
announcements remain coherent, and mobile/desktop browser flows pass.

## STOP conditions

Stop if source identity cannot be tied to the selected answer; if safe
formatting would require rendering model HTML; if a channel requires credentials
or SDK tracking not approved by the operator; or if backend provenance fields
are unavailable.
