# AI Sahakar Developer Guide

**Status:** Active
**Audience:** Developers, reviewers, and coding agents
**Last verified:** 2026-07-25
**Canonical design:** [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md)

## Start with the contract

AI Sahakar is a civic knowledge workbench, not a generic search landing page.
Read the UI contract before touching the public or admin interface. A visual
change is an enhancement only when it improves clarity, evidence access,
accessibility, or performance without changing the protected direction.

## File map

| Area | Files |
| --- | --- |
| Public template | `flowdocs/core/templates/search.html` |
| Public shell and tokens | `flowdocs/core/static/main/css/civic-workbench.css` |
| Search cascade | `flowdocs/core/static/main/css/search.css` |
| Public behavior | `flowdocs/core/static/main/js/search.js` |
| Public backend boundary | `flowdocs/core/views.py`, existing search URL and JSON contract |
| Admin shell | `flowdocs/core/templates/base.html`, `dashboard.html` |
| Admin styling | `flowdocs/core/static/main/css/style.css` |
| Locale catalogs | `flowdocs/locale/en/LC_MESSAGES/django.po`, `flowdocs/locale/mr/LC_MESSAGES/django.po` |
| Browser coverage | `tests/browser/civic-workbench.spec.ts`, `tests/browser/search-motion.spec.ts`, `tests/browser/full-suite.spec.ts` |

Use existing Django partials, translation tags, `json_script`, and CSS tokens.
Do not copy production data or secrets into fixtures.

## Operator presentation API

`flowdocs/core/operator_presentation.py` is the sole presentation boundary for
Dashboard and Workbench machine evidence. Keep `reason_code`,
`safe_error_code`, state, operation, and job fields stable. Add the adjacent
`presentation` or `*_label` fields by calling `decorate_operator_state`; JSON
responses keep the code and add a sibling presentation object.

Templates render guidance with:

```django
{% include "components/operator_evidence.html" with
  presentation=item.presentation technical_code=item.reason_code only %}
```

The component presents title, explanation, consequence, and action first. It
places the exact code in collapsed, LTR Technical details. Unknown codes use
neutral review guidance and must never be formatted by replacing underscores.
Register new UI reasons with authored English and Marathi copy before use.

Marathi terminology follows a reviewed glossary:

| English concept | Required Marathi rendering |
| --- | --- |
| Dashboard | डॅशबोर्ड |
| Workbench | कार्यपटल |
| operator | ऑपरेटर |
| Vault | तिजोरी |
| runtime | रनटाइम |
| profile | प्रोफाइल |
| rollback | रोलबॅक |
| embedding | एम्बेडिंग |
| manifest | मॅनिफेस्ट |
| checkpoint | तपासणी बिंदू |
| garbage collection / GC | कचरा संकलन / जीसी |
| retention hold | जतन स्थगिती |
| generation | निर्मिती संच |
| reindex | पुनःअनुक्रमण |

Prefer an established literal Marathi term where it stays precise; otherwise
use the glossary's Marathi-script transliteration and explain the operational
meaning in Marathi. Latin-script English is reserved for exact technical
evidence such as codes, API fields, UUIDs, hashes, filenames, and paths. Do not
translate those identifiers.

## Safe UI change workflow

1. Inspect the current implementation and the relevant browser tests.
2. Identify the component contract, responsive range, states, locale strings,
   and backend data fields involved.
3. Make a small patch. Keep search routes, CSRF, auth, PDF access, feedback,
   WhatsApp, and response fields unchanged.
4. Add or update English/Marathi copy in catalogs; never concatenate translated
   fragments in JavaScript or mutate user-entered questions. Remove fuzzy flags
   only after reviewing the complete Marathi sentence in context; a successful
   locale compilation does not establish translation parity.
5. Test static and dynamic states, including no-result and failure paths.
6. Update the contract or this guide if the rule itself changed.

Never remove an old template, route, asset, or CSS block based only on a
visual assumption. Prove references and record cleanup separately; see
[`DEV_CLEANUP_SCOPE.md`](DEV_CLEANUP_SCOPE.md).

## Verification

Run the smallest applicable checks first:

```bash
git diff --check
python manage.py check
python manage.py makemigrations --check --dry-run
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
msgattrib --only-fuzzy flowdocs/locale/mr/LC_MESSAGES/django.po
python3 -m unittest scripts.ci.test_operator_language
python3 scripts/ci/validate_operator_language.py
node --check flowdocs/core/static/main/js/search.js
```

For UI changes, run the repository browser suite with the disposable local
fixture and test at least 320px mobile, 768px tablet, 1024px laptop, and
1440px desktop. Check English, Marathi, reduced motion, loading, success,
no-result, error, source drawer, keyboard search, and account visibility.

For image/runtime changes, rebuild the web image before browser verification:

```bash
docker build -t pdfsearch-ci:source .
PDFSEARCH_IMAGE=pdfsearch-ci:source REDIS_IMAGE=redis:7-alpine \
  bash scripts/ci/run_compose_smoke.sh
```

The smoke gate proves the actual image entrypoint, Redis dependency, runtime
routes, migrations, static files, search landing page, core tests, data, and
index gates. It is not a substitute for browser interaction checks.

## PR checklist

- [ ] UI contract read and affected component recorded.
- [ ] No unapproved direction change or fabricated civic content.
- [ ] English and Marathi states reviewed.
- [ ] Keyboard/focus/live-region/reduced-motion behavior reviewed.
- [ ] No horizontal overflow or sticky composer obstruction.
- [ ] Relevant tests and `git diff --check` pass.
- [ ] Documentation updated for user-visible behavior.
- [ ] Changes are committed logically and the PR targets `dev`.
