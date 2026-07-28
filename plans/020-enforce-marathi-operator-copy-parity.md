# Plan 020: Make English and Marathi operator presentation equivalent

> **Executor instructions**: Do not use machine translation without human
> review. Preserve technical codes as English/LTR evidence.
>
> **Drift check (run first)**:
> `git diff --stat f7d0536..HEAD -- flowdocs/core/operator_presentation.py flowdocs/locale/en/LC_MESSAGES/django.po flowdocs/locale/mr/LC_MESSAGES/django.po flowdocs/core/test_operator_presentation.py`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 018
- **Category**: bug
- **Planned at**: commit `f7d0536`, 2026-07-28

## Why this matters

The registry calls `gettext()` at runtime around strings stored in tuples and
dictionaries, so ordinary Django message extraction cannot reliably discover
them. At the planned commit, 138 of 180 reason-copy strings and 47 of 58 label
strings return unchanged English under Marathi. The existing test checks only
one reason and one label, hiding the broad parity failure.

## Current state

- `flowdocs/core/operator_presentation.py:17-446` stores source-language
  literals in runtime data structures.
- `flowdocs/core/operator_presentation.py:449-480` calls `gettext` only when a
  value is selected.
- `flowdocs/core/test_operator_presentation.py:76-82` samples only
  `external_embeddings_disabled` and `retryable_failed`.
- The Marathi catalog contains authored translations for a small subset near
  its operator-evidence appendix, but most registry strings are absent.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Extract messages | `docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd flowdocs && python manage.py makemessages -l mr -l en_IN --no-obsolete'` | exit 0 |
| Compile Marathi | `msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/pdfsearch-mr.mo` | exit 0 |
| Presentation tests | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py test core.test_operator_presentation` | all pass |
| Browser locale gate | `npx playwright test browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts` | all configured projects pass |

## Scope

**In scope**:

- translation-marking strategy in `operator_presentation.py`
- English and Marathi catalogs
- exhaustive parity tests and representative browser assertions

**Out of scope**:

- changing stable codes or identifiers
- translating codes, hashes, UUIDs, timestamps, or filenames
- rewriting unrelated legacy fuzzy translations

## Steps

### Step 1: Make registry literals statically extractable

Use Django's lazy translation markers or another extraction-supported pattern
at definition sites. Keep presentation resolution locale-sensitive at request
time. Prove `makemessages` discovers every title, detail, consequence, action,
and label without a manual shadow list.

**Verify**: a test/inventory compares every registry string with extracted
catalog entries and reports zero missing source messages.

### Step 2: Author Marathi equivalents

Translate every registered field with equivalent meaning, consequence, and
action. Preserve concise operator vocabulary and do not transliterate stable
technical tokens into Marathi.

**Verify**: compile the catalog and assert every required Marathi lookup differs
from English, except an explicit reviewed allowlist for shared proper nouns.

### Step 3: Test complete parity, not examples

Parameterize across the full reason and label registries. Add browser coverage
for healthy, warning, failure, stale, unknown, and disabled-control states in
both locales.

**Verify**: presentation and browser suites pass with no English fallback in
primary Marathi operator guidance.

## Test plan

- Every registry field appears in the source catalog.
- Every required field has a non-empty, non-fuzzy Marathi translation.
- Unknown fallback is fully translated.
- Technical code remains exact English/LTR after locale switching.
- Locale switching does not cache copy from the prior language.

## Done criteria

- [ ] Zero untranslated required reason strings.
- [ ] Zero untranslated required label strings.
- [ ] No fuzzy operator-evidence translations.
- [ ] English and Marathi browser states convey equivalent actions.
- [ ] Technical evidence remains unchanged and LTR.

## STOP conditions

- A translation changes the safety consequence or recommends a different
  operation from English.
- No qualified reviewer is available for operational Marathi wording.
- The extraction strategy evaluates translations at import time and breaks
  per-request locale switching.

## Maintenance notes

The parity inventory must be mandatory CI so future registry additions cannot
silently fall back to English.
