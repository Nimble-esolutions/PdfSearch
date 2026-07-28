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

## Deployment Architecture (2026-07-24)

### Stage vs Production

| Environment | Domain | Deployment | When |
|-------------|--------|------------|------|
| Stage | `2026.ai-sahakar.net` | Auto-deployed by Dokploy on every GitHub image release | After CI publishes `:latest` tag |
| Production | `ai-sahakar.net`, `www.ai-sahakar.net` | Manual, stable releases only | After stage verification |

### Critical Rules

- **NEVER modify the production Traefik config** (`/etc/dokploy/traefik/dynamic/` static files or labels for production domain). Production routing is managed separately from stage.
- **The `sahakar-ai-sahakar-frontend-2026-prod-ruhj6z` Compose project is STAGE**, not production. Despite "prod" in the name, it deploys to `2026.ai-sahakar.net`.
- **Do not change the Traefik Host labels** on the stage container to include `ai-sahakar.net` — this would route production traffic to stage.
- **Production domain `ai-sahakar.net`** is routed via a separate static Traefik config. The current config routes to a dev service that needs migration to a dedicated production service.
- **Stage container health checks** may show "unhealthy" when `/readyz` returns 503 due to degraded data (ratio-based check). This is expected for stage. Use `/livez` for health checks.

### Traefik Routing Map

```
2026.ai-sahakar.net  →  Compose: sahakar-ai-sahakar-frontend-2026-prod-ruhj6z (Docker labels)
ai-sahakar.net       →  Static: sahakar-dev-frontend-dockerfile-1cubi5.yml (needs migration)
www.ai-sahakar.net   →  Same static config as ai-sahakar.net
```

### Historical / Known Issues
- The production domain was historically served by the `sahakar-dev-frontend-dockerfile-1cubi5` Swarm service during initial development. This was never migrated to a dedicated production service.
- The stage Compose project name includes "prod" for historical reasons (it was created as a preview/verification host before proper staging was set up).

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
- **Operator language boundary:** Dashboard and Workbench UI changes must
  resolve machine reasons, states, job kinds, operations, and audit values
  through `core.operator_presentation` and render codes only through
  `components/operator_evidence.html`. Never interpolate raw diagnostic codes
  or create user copy by replacing underscores. Run
  `python3 scripts/ci/validate_operator_language.py` for every operator UI
  change and preserve equivalent authored English/Marathi guidance. Every
  registry title, detail, consequence, action, and label requires a reviewed,
  non-fuzzy Marathi entry. Prefer precise Marathi literal translations; for
  specialized terms without a safe equivalent, use the repository glossary's
  consistent Marathi-script transliteration. Do not leave ordinary interface
  words in Latin-script English. Preserve exact codes, API fields, hashes,
  UUIDs, filenames, and paths as English/LTR technical evidence.

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

## UI design lock

Before changing public search/admin templates, CSS, JavaScript, translations,
browser tests, or visual documentation, read
`docs/design/AI_SAHAKAR_UI_CONTRACT.md` and use
`skills/ai-sahakar-ui-contract/SKILL.md`. Preserve the Civic Knowledge
Workbench and Operations Cockpit directions unless an explicit human request
changes them. Enhancements must retain evidence-first hierarchy, Marathi
parity, accessibility, responsive behavior, and existing backend contracts.
