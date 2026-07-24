Status: Active
Audience: Codex agents and maintainers
Source conversion: workspace `.kilo` and `.opencode` rules
Last updated: 2026-07-24

# Codex Operations Guide

This guide converts the workspace Kilo/OpenCode operations bundle into a
Codex-friendly runbook for the tracked PdfSearch repository. Repository-local
`AGENTS.md` and `docs/AGENT_RULE_AUTHORITY.md` define the authority order.
The parent workspace `../AGENTS.md` remains the production-server authority when
the full operations workspace is present.

## Core Rules

1. Read before write.
2. Back up before mutating production data, services, routes, or volumes.
3. Prefer zero-downtime diagnostics and deployments.
4. Never print secrets, `.env` values, RustFS credentials, PDF contents, or
   copied production data.
5. Deploy production through GitHub, immutable GHCR image digests, and the
   Dokploy-connected application.
6. Keep commits logical: separate UI, tests, docs, operations, release metadata,
   and emergency evidence.
7. Treat database, PDF, FAISS, Chroma, and manifest artifacts as one immutable
   generation when syncing or restoring through the superadmin cockpit.
8. Never promote a staged generation without checksum, schema, count, and
   representative-search validation.

## Permission Model

Codex may have more than one GitHub access path:

- the GitHub connector, which can read structured PR metadata and may have
  narrower write permissions;
- local `gh`, which uses the operator's authenticated token;
- normal `git`, which may be able to fetch and push even when `gh` is not
  authenticated.

Always check the actual path being used before assuming a permission failure is
global:

```bash
gh auth status
gh repo view --json nameWithOwner,defaultBranchRef,visibility
git status -sb
```

If the connector returns `403` but `gh auth status` is healthy, use `gh` for PR
creation, merge, check inspection, and workflow monitoring. If `gh auth status`
reports an invalid token, ask the operator to refresh it before attempting
write actions.

## PR and Release Flow

1. Branch from `dev`.
2. Keep the change scoped.
3. Run local checks and tests.
4. Open a PR into `dev`.
5. Resolve conflicts by merging or rebasing latest `origin/dev`, then rerun the
   affected local checks.
6. Merge only after required checks are green and review is complete.
7. Let the `dev` merge event publish the new image.
8. Capture the release evidence from the completed GitHub Actions run.
9. Deploy through Dokploy only after Git SHA, OCI digest, Compose hash, route,
   data generation, and rollback evidence agree.

Recommended local verification:

```bash
python manage.py check
python manage.py test core
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.dev.yml up --build
```

If host Python dependencies are absent, run Django checks inside the Docker
`web` service.

### Conflict Resolution Checklist

For open PR conflicts:

1. Fetch the latest default branch and PR refs.
2. Inspect PR state with `gh pr view <number> --json mergeStateStatus,statusCheckRollup`.
3. Resolve conflicts locally on the PR branch.
4. Preserve both sides when conflicts are caused by duplicated logical commits.
5. Run the smallest checks that cover the conflicted files.
6. Push the resolution commit.
7. Confirm GitHub reports `mergeStateStatus: CLEAN`.

For translation conflicts, run:

```bash
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
```

### Admin UI Validation

When Admin UI, document processing, Marathi translation, or category operations
change, prefer these checks:

```bash
docker compose -f docker-compose.dev.yml build web
docker compose -f docker-compose.dev.yml up -d web
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python manage.py test core.tests'
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python /app/scripts/ci/admin_ui_smoke.py'
curl -fsS http://127.0.0.1:8000/readyz
```

Bulk Admin UI operations require the local maintenance worker:

```bash
docker compose -f docker-compose.yml up -d maintenance
docker compose -f docker-compose.yml exec -T maintenance sh -lc 'cd /app/flowdocs && python manage.py run_maintenance_jobs --once'
```

The public search page is intentionally anonymous and defaults to English. A
visitor's Marathi selection is session-backed and must be sent explicitly with
the search request so the answer language cannot be inferred incorrectly from
the query text. `/register/` is never public: only authenticated `admin` and
`superadmin` users may create accounts. Department-scoped admin roles are a
phase-2 authorization boundary and must be designed server-side before use.

If a test intentionally exercises a mocked failure path, tracebacks can appear
in output while the suite still exits successfully. Report the exit result and
the known mocked-failure context rather than hiding the tracebacks.

### Release Evidence Capture

After a merge to `dev`, monitor the workflow until it completes:

```bash
gh run list --branch dev --limit 5 --json databaseId,displayTitle,headSha,status,conclusion,url
gh run view <run-id> --json status,conclusion,jobs,url
```

Record:

- merge commit SHA;
- GitHub Actions run URL;
- image digest;
- promoted compatibility tags;
- smoke, scan, image-size, and release-evidence job outcomes;
- production promotion status.

A successful `dev` image release means a tested image exists. It does not prove
Dokploy has promoted that digest to production.

## Production Safety

Server identity:

- SSH: `root@80.65.208.138`
- Hostname: `mum-01.ai-sahakar.net`
- Runtime: Docker Swarm, Traefik, Dokploy
- Normal deployment authority: Dokploy GitHub-connected app

SSH is for read-only diagnostics, approved backups, explicit emergency testing,
and rollback evidence. Use:

```bash
ssh root@80.65.208.138 "<command>"
```

For complex remote variables, use a reviewed script through stdin rather than
local-shell interpolation.

## Health Audit Order

1. Disk, memory, CPU, uptime.
2. Docker daemon and Swarm state.
3. Containers and health status.
4. Swarm services and replicas.
5. Docker disk usage.
6. Traefik routes and Docker labels.
7. Recent container death events.
8. PdfSearch `/livez`, `/readyz`, `/health/data/`, `/health/lease/`,
   `/health/metrics/`, Redis `PONG`, static asset, PDF listing, and
   representative search.
9. GitHub branch HEAD, Dokploy checkout HEAD, OCI revision/digest, rendered
   Compose image, and Dokploy deployment ID.
10. Active `/app/data` volume identity, PDF count, FAISS count, and data
    generation reference.

Score each category as OK, WARNING, or CRITICAL. Do not restart blindly to hide
evidence.

## Backup and Rollback

Backups use read-only volume mounts and must include manifests, checksums,
service specs, rendered Compose evidence, and route evidence.

For PdfSearch, record SQLite size, PDF count, FAISS count, Chroma presence,
image digest, Compose hash, RustFS snapshot/checksum references, writer lease
status, activation journal entries, and backup policy compliance.

Rollback is two-dimensional:

1. Application rollback: redeploy the previous immutable image digest through
   Dokploy.
2. Data rollback: restore the matching data generation into an isolated volume,
   validate it, then explicitly promote.

Never use `docker compose down -v`, never overwrite active data during first
restore, and never copy legacy SQLite/PDF/FAISS files directly into active
production.

## Source Mapping

- `.kilo/skill/sahakar-server/SKILL.md`: server identity, deployment contract,
  data boundary, backup/deployment constraints.
- `.kilo/agent/ops.md`: diagnostic order and read-before-write principles.
- `.kilo/agent/deploy.md`: deploy/rollback workflow and Dokploy constraints.
- `.kilo/command/health.md`: health audit checklist.
- `.kilo/command/logs.md`: log collection and incident evidence.
- `.kilo/command/ssh.md`: SSH guardrails.
- `.kilo/command/backup.md`: backup runbook.
- `.kilo/command/rollback.md`: rollback runbook.
- `.opencode/skill/sahakar-server/SKILL.md`: older duplicate skill, superseded
  when dates or facts conflict with `AGENTS.md`.
- `flowdocs/core/environment.py`: environment identity (APP_ENV,
  PRODUCTION_SOURCE_ID, AUTHORITATIVE_DATASET_ID, DATASET_ID, BACKUP_ROLE,
  EXTERNAL_SIDE_EFFECTS_MODE, DATA_MODE).
- `flowdocs/core/side_effects.py`: external side-effect safety gating.
- `flowdocs/core/ai_guard.py`: AI operation authorization.
- `flowdocs/core/activate.py`: atomic active-release pointer switch.
- `flowdocs/core/activation_journal.py`: activation audit trail.
- `flowdocs/core/restore_pipeline.py`: restore pipeline orchestration.
- `flowdocs/core/restore_workspace.py`: isolated restore workspace.
- `flowdocs/core/global_writer.py`: global writer fencing.
- `flowdocs/core/registration.py`: dataset registration.
- `flowdocs/core/backup_policy.py`: backup policy enforcement.
- `flowdocs/core/sanitize.py`: data sanitization pipeline.
- `flowdocs/core/rehearsal.py`: migration rehearsal.
- `flowdocs/core/lease.py`: writer lease management.
- `flowdocs/core/compatibility.py`: compatibility checks.
- `flowdocs/core/metrics.py`: metrics collection.
- `flowdocs/core/namespace.py`: namespace management.
- `flowdocs/core/object_store_capabilities.py`: object store capability detection.

## Environment Identity

The `environment` module defines the runtime contract. Key variables:

- `APP_ENV` — deployment environment (development, staging, production)
- `PRODUCTION_SOURCE_ID` — canonical production source identifier
- `AUTHORITATIVE_DATASET_ID` — authoritative dataset reference
- `DATASET_ID` — current dataset identifier
- `BACKUP_ROLE` — backup role (primary, secondary, none)
- `EXTERNAL_SIDE_EFFECTS_MODE` — external side-effect safety mode
- `DATA_MODE` — data access mode (read_only, read_write)

## Side-Effect Safety

The `side_effects` module gates external side effects. Set
`EXTERNAL_SIDE_EFFECTS_MODE` to control whether external calls (email, webhooks,
API notifications) are permitted. The `ai_guard` module authorizes AI-driven
operations.

## Restore Pipeline

The `restore_pipeline` and `restore_workspace` modules orchestrate isolated
restore operations. A restore workspace is a disposable staging directory.
The pipeline runs compatibility checks, sanitization, migration rehearsal,
and activation in sequence. The `activate` module performs an atomic
active-release pointer switch. The `activation_journal` records every step
with an immutable audit trail.

## Generation Lifecycle

Data generations are created through explicit superadmin sync, validated
through `validate_data_release`, and promoted through the restore pipeline.
Each generation is immutable and content-addressed. The `global_writer`
module fences concurrent writers; the `lease` module manages TTL-based
writer leases.

## PDF Lifecycle

PDFs follow a lifecycle: upload → processing → indexing → archival. Bulk
operations queue durable maintenance jobs through the Admin UI. The
`compatibility` module verifies embedding model and index format
compatibility before indexing.

## User Management

User management is available at `/dashboard/users/`. Only authenticated
`admin` and `superadmin` users may access it. `/register/` is never public.

## Management Commands

```bash
python manage.py config_inspect
python manage.py verify_object_store_capabilities
python manage.py inventory_artifacts --data-root /app/data --output /app/data/backups/inventory.json
python manage.py validate_data_release --manifest /app/data/backups/inventory.json --data-root /app/data
```

## Drift Handling

When Kilo/OpenCode/Codex instructions disagree:

1. Prefer `AGENTS.md` and `docs/AGENT_RULE_AUTHORITY.md`.
2. Identify the stale adapter and the exact stale fact.
3. Fix the tracked repository copy through a PR.
4. If the stale file lives outside the Git repository, record that follow-up in
   the PR body instead of pretending it was committed.

Known examples to guard against:

- Ubuntu `24.04.3` after the host is known to be `24.04.4`.
- Docker `29.1.3` after the host is known to be `29.6.2`.
- Treating `:dev` or `:latest` as release identity.
- Treating `2026.ai-sahakar.net` as the production host.
- Suggesting normal production deploys by editing Dokploy server checkout files.
- Copying legacy SQLite/PDF/FAISS artifacts directly into active production.
- Treating the atomic active-release pointer as a future capability — it is
  implemented through the `activate` module.
- Assuming RustFS is read-only and not application-accessible — the
  `object_store_capabilities`, `restore_pipeline`, and `restore_workspace`
  modules provide explicit, gated access.
- Skipping compatibility checks before staging — the `compatibility` module
  must pass before promotion.
- Running migrations without rehearsal — the `rehearsal` module provides
  dry-run migration against a disposable copy.
- Ignoring writer lease expiry — the `lease` module enforces TTL-based
  fencing; stale writers are rejected.
- Omitting the activation journal — every activation step must be recorded
  for auditability.
