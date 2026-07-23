# PdfSearch Agent Instructions

This file is the tracked, repository-local adapter for agents working inside
`PdfSearch`. The parent workspace `../AGENTS.md` remains the production-server
authority when it is available locally. When this repository is checked out by
itself, use this file plus `docs/AGENT_RULE_AUTHORITY.md`.

## Authority Order

1. User instructions in the current conversation.
2. System and developer instructions from the active agent runtime.
3. Parent workspace `../AGENTS.md`, when present.
4. `docs/AGENT_RULE_AUTHORITY.md`.
5. `docs/PRODUCTION_OPERATING_RULES.md`.
6. `docs/CODEX_OPERATIONS_GUIDE.md`.
7. Historical Kilo/OpenCode adapters, only when they do not conflict with the
   sources above.

## Repository Rules

- **MANDATORY:** Follow `~/.agent-workflow-rules.md` for every code-changing
  task. Branch from `dev`, commit locally in cherry-pickable chunks, verify
  before pushing, open a PR into `dev`, merge only when green. No uncommitted
  work at session end. No commits directly to `main` or `dev`.
- Branch from `dev` for normal work.
- Keep commits logical and reviewable: separate app, tests, docs, release
  evidence, and operational-rule changes.
- Open PRs into `dev`; PRs validate, while merges to `dev` publish release
  images.
- Do not claim production deployment from a successful image build. Production
  promotion requires Dokploy, live route, image digest, Compose, data, and
  rollback evidence.
- Never commit secrets, bootstrap credentials, production databases, uploaded
  PDFs, FAISS/Chroma indexes, or `.env` values.
- Do not edit production through SSH or Dokploy server checkouts as a normal
  deployment path. Use the GitHub-to-Dokploy release contract.
- For local verification, prefer Docker commands from this repository when host
  Python dependencies are not guaranteed.
- Public search is intentionally anonymous, but `/register/` is never public:
  only authenticated `admin` and `superadmin` users may create accounts.
  Department-scoped admin roles are a phase-2 authorization design item.
- Bulk indexing, OCR repair, and folder operations must queue durable
  maintenance jobs; never put embedding or FAISS work back into a synchronous
  web request. Preserve cancellation, retry, and per-item failure state.
- **Migration discipline:** Before generating a new Django migration, check
  the base branch (`git fetch origin dev && git show origin/dev:flowdocs/core/migrations/`)
  to determine the next available number. Renumber manually if needed; never
  push two branches with the same migration number. The CI migration guard
  (`scripts/ci/validate_migrations.py`) catches collisions at PR time, but
  avoid them by checking first.
- **Test file isolation:** When multiple PRs add test classes that would
  conflict in a monolithic `tests.py`, split new test classes into separate
  files (`core/tests/test_feature.py`) and import from `__init__.py`. This
  prevents cascading rebase conflicts. The CI test-isolation checker
  (`validate_migrations.py --check-tests`) reports duplicate class names.
- **Zero-commitment merges:** When multiple independent PRs must land in quick
  succession, prefer an integration branch: merge all PRs into it, resolve
  conflicts once, run the full suite, then fast-forward `dev`. This avoids
  the N×N rebase matrix where each merge forces rebasing every remaining PR.

## Local Validation

Use the narrowest checks that cover the change. Common gates:

```bash
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python manage.py test core.tests'
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python /app/scripts/ci/admin_ui_smoke.py'
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
```

If Docker is not running, report that verification gap instead of inventing a
green result.
