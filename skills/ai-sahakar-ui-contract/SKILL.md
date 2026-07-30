---
name: ai-sahakar-ui-contract
description: Preserve and extend the locked AI Sahakar Civic Knowledge Workbench public search and admin UI.
---

# AI Sahakar UI Contract Skill

Use this skill whenever a task touches the public search page, admin console,
templates, UI CSS/JS, translations, browser tests, screenshots, or visual
documentation.

## Required sequence

1. Read [`docs/design/AI_SAHAKAR_UI_CONTRACT.md`](../../docs/design/AI_SAHAKAR_UI_CONTRACT.md).
2. Inspect the existing template, CSS, JavaScript, locale, and browser tests
   before editing. Preserve user changes and the existing search contract.
   Treat `flowdocs/locale/` as the only canonical catalog root; never recreate
   the retired repository-root `locale/` catalog.
3. Inventory every reason code, safe error, state, operation, job kind, audit
   action, and result that the changed surface can expose. Resolve each through
   `core.operator_presentation`; do not infer copy from the token.
   For Vault profile work, also inventory profile source (`environment`,
   `stored`, or migration-only `legacy`), completeness, credential posture,
   and the eligibility of every probe/inventory control. Do not let an empty
   historical projection masquerade as a configurable remote profile.
4. State the affected component, responsive breakpoints, states, locale copy,
   accessibility behavior, and performance impact in the PR or design note.
5. Implement the smallest contextual enhancement using existing tokens and
   Django patterns. Do not introduce a frontend framework or animation library.
6. Review equivalent authored English and Marathi meaning, consequence, and
   next-action copy. Inventory existing translations for the affected
   Dashboard/Workbench area and correct stale fuzzy or misleading entries.
   Translate ordinary Marathi precisely. Prefer established contextual Marathi
   for operational concepts: `संचिका` (file/media), `परिचालक` (operator),
   `कार्यरत प्रणाली` (runtime), `जोडणी रूपरेषा` (connection profile),
   `प्रवेश-प्रमाण` or `प्रमाणीकरण माहिती` (credentials), and
   `मागील आवृत्ती पुनर्स्थापित करा` (rollback action). Retain a reviewed
   Marathi-script term when a named product or specialist identity such as
   Vault, embedding, manifest, or database would become inaccurate as a literal
   word. Ordinary interface copy must not
   fall back to Latin-script English. Obvious operator terms use established
   Marathi: `उपलब्ध नाही` (unavailable), `इशारा` (warning), `अयशस्वी`
   (failed), `सक्रिय` (active), `मागील` (previous), `कारण` (reason),
   `कृती` (action), `परिणाम` (result), `शोध तयारी` (search readiness),
   and `तांत्रिक तपशील` (technical details). Translate and review the complete
   sentence; do not implement this as blind token substitution. Run the
   registry-wide catalog parity gate.
   Scan visible text and accessibility output for machine tokens, then expand
   Technical details and verify the exact bounded code is copyable, LTR, and
   redacted.
   Review existing affected translations as well as new strings. Translate
   ordinary words literally where Marathi remains precise; retain identifiers
   such as S3, HTTP, HTTPS, DNS, codes, aliases, and environment keys as
   technical evidence rather than inventing misleading translations.
   Treat ordinary document maintenance and advanced Vault recovery as separate
   journeys: confirm Dashboard links enter Documents & Search directly, local
   maintenance does not imply profile setup, and advanced authority evidence is
   collapsed on the ordinary path.
7. Verify the affected English, Marathi, mobile, desktop, keyboard, reduced
   motion, source, loading, error, and admin states.
8. Update the developer/user guide or contract when behavior or rules change.

## Non-negotiable guardrails

- Workbench macrostructure and Civic Knowledge Workbench theme remain the
  default.
- Official identity, readable hierarchy, source evidence, Marathi parity,
  accessible labels, and immediate composer access are protected.
- Never add robot branding, purple gradients, decorative tricolour styling,
  fake metrics, fabricated citations, excessive pills, or generic AI hero copy.
- Never change backend search, authentication, CSRF, PDF authorization,
  OpenAI policy, data lifecycle, RustFS, writer, or scheduler behavior for a UI
  task.
- Do not claim visual or browser validation without executing it.
- Raw `reason_code`, `safe_error_code`, error summaries, protection/blocking
  reasons, and state-machine values never become primary copy.
- Every registry title, detail, consequence, action, and label has a reviewed,
  non-fuzzy Marathi entry. The registry-wide validator must report no missing,
  empty, fuzzy, or unchanged-English entries. A compiling catalog or automatic
  fuzzy match is not translation approval.
- Exact codes, API fields, hashes, UUIDs, filenames, and paths remain
  English/LTR technical evidence. Literal contextual forms include `संचिका`,
  `परिचालक`, `कार्यरत प्रणाली`, `जोडणी रूपरेषा`, and
  `मागील आवृत्ती पुनर्स्थापना`. Reviewed specialist forms include `व्हॉल्ट`,
  `रायटर लीज`, `एम्बेडिंग`, `मॅनिफेस्ट`, `चेकपॉइंट`, and `जीसी`.
- Do not transliterate obvious ordinary labels merely because their source
  string is English. Use the reviewed literal Marathi vocabulary from the
  contract, while preserving specialist terms and immutable evidence as above.

## Minimum review commands

```bash
git diff --check
python manage.py check
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
msgattrib --only-fuzzy flowdocs/locale/mr/LC_MESSAGES/django.po
python3 scripts/ci/validate_operator_language.py
node --check flowdocs/core/static/main/js/search.js
```

Use the repository's Docker/Playwright/Compose gates when those surfaces are
affected. End with a clean, committed worktree and a reviewable PR; do not
merge your own PR.
