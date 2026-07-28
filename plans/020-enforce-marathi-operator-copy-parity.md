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
one reason and one label, hiding the broad parity failure. The older catalog
also contains fuzzy Dashboard/Workbench translations whose Marathi text refers
to a different action or concept, so filling only the new entries would retain
misleading legacy copy.

## Current state

- `flowdocs/core/operator_presentation.py:17-446` stores source-language
  literals in runtime data structures.
- `flowdocs/core/operator_presentation.py:449-480` calls `gettext` only when a
  value is selected.
- `flowdocs/core/test_operator_presentation.py:76-82` samples only
  `external_embeddings_disabled` and `retryable_failed`.
- The Marathi catalog contains authored translations for a small subset near
  its operator-evidence appendix, but most registry strings are absent.
- `msgattrib --fuzzy flowdocs/locale/mr/LC_MESSAGES/django.po` surfaces legacy
  operator strings such as “Active jobs”, “Reindex Selected”, “Not indexed”,
  and “Deployment ID” whose carried-forward translations do not express the
  current English meaning.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Extract messages | `docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd flowdocs && python manage.py makemessages -l mr -l en_IN --no-obsolete'` | exit 0 |
| Compile Marathi | `msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/pdfsearch-mr.mo` | exit 0 |
| Find fuzzy legacy copy | `msgattrib --fuzzy flowdocs/locale/mr/LC_MESSAGES/django.po` | no Dashboard/Workbench operator entry remains fuzzy |
| Presentation tests | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py test core.test_operator_presentation` | all pass |
| Browser locale gate | `npx playwright test browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts` | all configured projects pass |

## Scope

**In scope**:

- translation-marking strategy in `operator_presentation.py`
- English and Marathi catalogs
- exhaustive parity tests and representative browser assertions
- a reviewed operator terminology glossary in the canonical UI contract and
  developer guide
- matching enforcement in repository `AGENTS.md` and the existing
  `skills/ai-sahakar-ui-contract/SKILL.md`
- legacy Dashboard/Workbench Marathi entries that are fuzzy, untranslated, or
  semantically inconsistent with their current English source

**Out of scope**:

- changing stable codes or identifiers
- translating codes, hashes, UUIDs, timestamps, or filenames
- rewriting public/search/legal translations unrelated to Dashboard,
  Workbench, maintenance, recovery, retention, or operator evidence

## Steps

### Step 1: Make registry literals statically extractable

Use Django's lazy translation markers or another extraction-supported pattern
at definition sites. Keep presentation resolution locale-sensitive at request
time. Prove `makemessages` discovers every title, detail, consequence, action,
and label without a manual shadow list.

**Verify**: a test/inventory compares every registry string with extracted
catalog entries and reports zero missing source messages.

### Step 2: Establish a reviewed operator terminology policy

Inventory recurring user-facing terms such as Workbench, Vault, runtime,
rollback, generation, maintenance, reindex, deployment, garbage collection,
technical details, and evidence. For each term, record one approved Marathi
literal translation or consistent Marathi-script transliteration. Prefer clear
Marathi operator language over leaving obvious interface prose in English.
Keep only machine evidence—stable codes, API field names, hashes, UUIDs,
filenames, and explicitly branded/proper names—verbatim and LTR.

Amend `docs/design/AI_SAHAKAR_UI_CONTRACT.md`,
`docs/AI_SAHAKAR_DEVELOPER_GUIDE.md`, repository `AGENTS.md`, and the existing
`skills/ai-sahakar-ui-contract/SKILL.md` with this distinction and the glossary
review command. Do not create a competing skill.

**Verify**: a glossary test rejects an unapproved English fallback in Marathi
primary copy and rejects translation or transliteration of stable technical
codes.

### Step 3: Author Marathi equivalents

Translate every registered field with equivalent meaning, consequence, and
action using the approved terminology policy. Where no natural technical
translation exists, use the approved Marathi-script transliteration rather
than ad hoc English leakage. Preserve concise operator vocabulary and do not
translate stable technical evidence.

**Verify**: compile the catalog and assert every required Marathi lookup differs
from English, except an explicit reviewed allowlist for shared proper nouns.

### Step 4: Repair affected legacy translations

Review all existing Dashboard and Workbench messages, including obsolete-source
comments and fuzzy entries. Correct translations that refer to the wrong
action, state, or object; remove the fuzzy flag only after semantic review.
Do not broaden this into a whole-site translation rewrite.

**Verify**: `msgattrib --fuzzy` returns no in-scope operator messages and a
catalog test proves each current Dashboard/Workbench `msgid` has an approved
Marathi `msgstr`.

### Step 5: Test complete parity, not examples

Parameterize across the full reason and label registries. Add browser coverage
for healthy, warning, failure, stale, unknown, and disabled-control states in
both locales.

**Verify**: presentation and browser suites pass with no English fallback in
primary Marathi operator guidance.

## Test plan

- Every registry field appears in the source catalog.
- Every required field has a non-empty, non-fuzzy Marathi translation.
- Every in-scope legacy Dashboard/Workbench entry is reviewed against its
  current English source and stale carried-forward meanings are corrected.
- Obvious user-facing operational terms follow the approved Marathi
  translation/transliteration glossary.
- Unknown fallback is fully translated.
- Technical code remains exact English/LTR after locale switching.
- Locale switching does not cache copy from the prior language.

## Done criteria

- [ ] Zero untranslated required reason strings.
- [ ] Zero untranslated required label strings.
- [ ] No fuzzy operator-evidence translations.
- [ ] No fuzzy or semantically stale Dashboard/Workbench operator translations
  remain in scope.
- [ ] The canonical contract, developer guide, repository rule, and existing UI
  skill encode the Marathi terminology policy.
- [ ] English and Marathi browser states convey equivalent actions.
- [ ] Technical evidence remains unchanged and LTR.

## STOP conditions

- A translation changes the safety consequence or recommends a different
  operation from English.
- No qualified reviewer is available for operational Marathi wording.
- The team cannot agree whether a recurring term should be translated or
  transliterated; record candidates and request operator language review.
- The extraction strategy evaluates translations at import time and breaks
  per-request locale switching.

## Maintenance notes

The parity inventory must be mandatory CI so future registry additions cannot
silently fall back to English.
