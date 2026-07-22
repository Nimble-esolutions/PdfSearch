Status: Active
Audience: Codex agents and maintainers
Source conversion: workspace `.kilo` and `.opencode` rules
Last updated: 2026-07-22

# Codex Operations Guide

This guide converts the workspace Kilo/OpenCode operations bundle into a
Codex-friendly runbook for the tracked PdfSearch repository. `AGENTS.md` remains
the top-level authority for production safety.

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

## PR and Release Flow

1. Branch from `dev`.
2. Keep the change scoped.
3. Run local checks and tests.
4. Open a PR into `dev`.
5. Merge only after required checks are green and review is complete.
6. Let the `dev` merge event publish the new image.
7. Deploy through Dokploy only after Git SHA, OCI digest, Compose hash, route,
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
8. PdfSearch `/livez`, `/readyz`, Redis `PONG`, static asset, PDF listing, and
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
image digest, Compose hash, and RustFS snapshot/checksum references.

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
