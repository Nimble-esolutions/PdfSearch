# Public Search Theme Architecture

**Status:** Active
**Owner:** FlowDocs maintainers
**Last verified:** 2026-08-06
**Scope:** Public search presentation only

## Outcome

Classic search is the safe primary presentation. Knowledge Workbench remains a
complete secondary presentation. They coexist without frontend inheritance or
asset coupling and use the same secured Django backend contract.

## Request decision flow

```text
GET /?view=classic|workbench
        │
        ├─ valid query value ──> render requested view for this response only
        │
        └─ absent/invalid ─────> read PUBLIC_SEARCH_PRIMARY_VIEW SiteSetting
                                  │
                                  ├─ classic/workbench ──> render saved view
                                  └─ missing/invalid ─────> render Classic
```

The query override writes nothing. The superadmin settings form is the only UI
that persists the allowlisted primary value.

## Isolation map

| Boundary | Classic | Knowledge Workbench | Shared |
| --- | --- | --- | --- |
| Django template | `search_classic.html` | `search.html` | View context contract |
| Presentation CSS | `search-classic.css` | `civic-workbench.css`, `search.css` | None |
| Application JS | `search-classic.js` | `search.js` | None |
| Layout | Legacy service composition | Three-region evidence workspace | None |
| Search POST | Existing form/JSON request | Existing form/JSON request | `search_query` backend |
| Sources/PDFs | Safe DOM records | Safe DOM records/drawer | Protected backend URLs |
| Language | Django locale session | Django locale session | English/Marathi catalog |
| Security | CSRF, escaping, URL allowlist | CSRF, escaping, URL allowlist | Django policy |

Selectors in one theme must not target the other theme. Static files from one
theme must not be loaded by the other. New behavior may be theme-specific; it
is shared only when it belongs to the backend contract.

## Admin and URL behavior

| Scenario | Result |
| --- | --- |
| Fresh database | Classic |
| Saved `classic` | Classic |
| Saved `workbench` | Workbench |
| Corrupt/unknown saved value | Classic |
| `/?view=workbench` | Workbench for that request; no persistence |
| `/?view=classic` | Classic for that request; no persistence |
| Unknown query value | Saved primary, otherwise Classic |
| English/Marathi switch | Locale changes and valid explicit `view` remains |

## Impact and rollback

This architecture adds one ordinary `SiteSetting`; it adds no migration, ENV,
Compose, data-custody, search, embedding, OCR, backup, restore, or activation
change. It does not alter the admin shell beyond the superadmin selector.

Operational rollback is immediate: select the other primary view in Settings.
Code rollback removes the selector and Classic files; document and index data
are unaffected. A failed theme render must never change search data or the
saved primary selection.

## Review gates

- Django tests prove default, persistence, permission, invalid-value, link, and
  frontend-isolation behavior.
- Playwright proves both themes, safe answer/source rendering, 30-word limits,
  Marathi query preservation, 320px overflow, accessibility, and Workbench
  motion behavior.
- Visual review covers 1920×1080 Classic parity and responsive Classic and
  Workbench layouts.
- Translation catalogs compile with no fuzzy entries in the affected copy.
