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
3. Inventory every reason code, safe error, state, operation, job kind, audit
   action, and result that the changed surface can expose. Resolve each through
   `core.operator_presentation`; do not infer copy from the token.
4. State the affected component, responsive breakpoints, states, locale copy,
   accessibility behavior, and performance impact in the PR or design note.
5. Implement the smallest contextual enhancement using existing tokens and
   Django patterns. Do not introduce a frontend framework or animation library.
6. Review equivalent authored English and Marathi meaning, consequence, and
   next-action copy. Scan visible text and accessibility output for machine
   tokens, then expand Technical details and verify the exact bounded code is
   copyable, LTR, and redacted.
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

## Minimum review commands

```bash
git diff --check
python manage.py check
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
python3 scripts/ci/validate_operator_language.py
node --check flowdocs/core/static/main/js/search.js
```

Use the repository's Docker/Playwright/Compose gates when those surfaces are
affected. End with a clean, committed worktree and a reviewable PR; do not
merge your own PR.
