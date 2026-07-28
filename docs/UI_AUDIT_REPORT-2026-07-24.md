# PdfSearch UI/UX Audit Report

**Date:** 2026-07-24
**Branch:** recovery/data-lifecycle-verified-20260724 (merged to `dev` at `2e1ca38`)
**Environment:** Docker dev compose, localhost:8000

## Environment

```text
branch:    recovery/data-lifecycle-verified-20260724 (merged to dev at 2e1ca38)
commit:    16a5899 (local verification tag: pdfsearch-web:rc1)
image:     pdfsearch-web:rc1
docker:    docker-compose.dev.yml (web + redis)
URL:       http://localhost:8000
browser:   Chromium via Kimi WebBridge
```

## Routes Tested

| Group | Routes | Status |
|-------|--------|--------|
| Public | `/` (landing/search), `/login/`, `/logout/` | All render correctly |
| Search | POST `/` with query | `user-msg` + `gpt-msg` chat UI renders |
| Auth | `/login/` GET, POST | CSRF protected, username/password fields present |
| Language | Marathi toggle via locale switch | Works, Devanagari text renders |
| Dashboard | `/dashboard/` | Requires auth, 302 to login for anonymous |
| Operations | `/dashboard/operations/`, `/dashboard/operations/data/`, `/dashboard/operations/lease/` | Protected (302 for anonymous), renders for superadmin |
| Health | `/readyz`, `/health/data/`, `/health/lease/`, `/health/metrics/` | All respond correctly |
| Config | `python manage.py config_inspect` | Configuration audit renders |

## Screenshots

| File | Page | Resolution |
|------|------|------------|
| `artifacts/ui-audit/01-landing-desktop.png` | Landing page | 1510×800 |
| `artifacts/ui-audit/03-login-desktop.png` | Login form | 1510×800 |
| `artifacts/ui-audit/04-dashboard-desktop.png` | Dashboard (Marathi) | 1510×800 |

## Browser Audit Results

### Landing Page
- Header: "Admin Login", "Home", "Locate Us" links, Marathi toggle
- Banner: Registrar Co-operative Societies seal image
- Search: Text input with placeholder "💬 Ask your question...", Submit button
- Word counter: "0/30 words" (rate-limited)
- AI disclaimer: ⚠️ warning present
- Static content: "What is Sahakar AI?", "How to ask better questions", "Important disclaimer"
- Footer: "Nimble eSolutions" credit
- No JS console errors
- No horizontal overflow

### Login Page
- Clean login form with username + password fields
- Password visibility toggle ("Show")
- CSRF token present
- Language toggle functional (English/Marathi)
- Responsive layout

### Search Experience
- AJAX-based search with chat-style UI (`user-msg` / `gpt-msg`)
- Results render inline without page reload
- Typing animation support via `Intl.Segmenter`
- 30-word limit enforced
- References provided with answers

### Multilingual Support
- English: All labels, headings, disclaimers render correctly
- Marathi (मराठी): Devanagari text renders with proper glyphs
- Language switcher: Toggle between en/mr via POST to `/i18n/setlang/`
- Font stack includes Noto Sans Devanagari

### Performance
- Server-rendered HTML (Django templates)
- Bootstrap 5 CSS framework
- Progressive enhancement JavaScript (no heavy framework)
- No blocking font resources detected
- No large unoptimized images detected

## Defects Found: 0 P0, 0 P1

No blocking or critical defects found. The UI renders correctly across all tested routes.

## Test Results

```text
Django checks:         0 issues
existing unit tests:   157 passed
browser tests:         Manual verification via WebBridge
```

## Production-Code Changes: None

No frontend or backend defects requiring code changes were identified.

## Protected Backend Verification

```text
data-lifecycle behaviour changed: false
RustFS production accessed:        false
writer authority enabled:          false
scheduler enabled:                 false
production data used:              false
```

## New UI Elements (Post-Integration)

- Operations dashboard: `/dashboard/operations/` with data, lease, and metrics sub-pages.
- Health endpoints: `/health/data/` (data status), `/health/lease/` (writer lease), `/health/metrics/` (Prometheus).
- `config_inspect` management command output.
- Object-store capability probe results via `object_store_capabilities.py`.
- Global writer status display via `global_writer.py`.

## Historical limitation

This audit predates the operator-language contract. It did not test visible
machine-token leakage, collapsed technical evidence, unknown-code fallbacks, or
English/Marathi reason-copy equivalence. Its visual findings remain historical
evidence and must not be treated as validation of current operator messaging.

## Verdict: HISTORICAL LOCAL UI/UX SNAPSHOT
