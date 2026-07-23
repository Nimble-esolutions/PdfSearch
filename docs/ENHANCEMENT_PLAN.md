# Production Enhancement Plan

> Generated: 2026-07-23 | Based on: full container audit + 48h code review + swarm inspection

## Already Addressed (Merged or PR'd)

| Item | PR | Status |
|------|----|--------|
| Maintenance worker health check fix (Levels 1–3) | #49 | Merged |
| Maintenance worker Redis queue + kind filters + health server (Level 4) | #49 | Merged |
| Configurable service footer (`DISPLAY_SERVICE_FOOTER`) | #50 | PR open |
| Migration numbering guard + test isolation checker | #48 | Merged |
| Agent workflow rule enforcement | #41 | Merged |
| All 6 completion-plan items (generation, audit, filter, drawer, lifecycle, SEO) | #42–#47 | Merged |

## Production Audit Findings & Actions

### P1 — Containers missing health checks (5 containers)

| Container | Risk | Fix |
|-----------|------|-----|
| `dokploy-traefik` | **Critical** — all ingress dies silently if Traefik breaks | Add `healthcheck: test: ["CMD", "traefik", "healthcheck"]` to Dokploy Compose |
| `dokploy-postgres` | High — Dokploy degrades silently if PG dies | Add `pg_isready -U dokploy` health check |
| `dokploy-redis` | Medium | Add `redis-cli ping` |
| `ops-rustfs-rustfs-1` | Medium | Research RustFS health endpoint |
| `ops-dozzle-dozzle-1` | Low | Add `wget --spider http://localhost:8080/health` |

**Action:** These are managed by Dokploy Compose applications, not the PdfSearch repo. File Dokploy tickets for each.

### P2 — Maintenance container still unhealthy post-deploy

PR #49 adds the heartbeat file that the new health check expects. The production image must be rebuilt and redeployed via Dokploy for the fix to take effect. The compose-level health check was deployed but the container code hasn't caught up.

**Action:** Merge PR #49's image build → Dokploy redeploys → container goes healthy within 20s.

### P3 — Kernel pending (196-day uptime)

Kernel `6.8.0-136` pending since the 2026-07-22 apt upgrade. Current kernel is `6.8.0-90`.

**Action:** Schedule maintenance reboot. Pre-reboot: stop all containers, verify volumes, record startup order. Post-reboot: verify all containers, Traefik routes, TLS, `/livez`/`/readyz`.

### P4 — Non-prod service traceability

7 non-prod services (dev, stage, training, togo, togo-live, hs, dev-jlukaf) use locally-built `:latest` images. No way to correlate a running container to a Git SHA or CI build.

**Action:** Add `--label org.opencontainers.image.revision=<sha>` to builds. For CI-built images, this is automatic with the `docker/metadata-action`. For local builds, document the label command.

### P5 — Image variant inconsistency

| Component | Dokploy uses | Authentik uses |
|-----------|-------------|----------------|
| Redis | `redis:7` | `redis:alpine` |
| PostgreSQL | `postgres:16` | `postgres:16-alpine` |

**Action:** Standardize on a single variant per component. Low priority — operational consistency only.

## Code-Level Enhancement Ideas (brainstormed from audit)

### Immediate (next sprint)

1. **`.env.example` file** — No env template exists in the repo. Add one listing all configurable variables with defaults and descriptions. Blocks self-service deployment.

2. **Deploy health gate** — Add a post-deploy verification script that Dokploy (or the operator) runs after each deployment: checks `/livez`, `/readyz`, maintenance heartbeat, PDF count, FAISS count, search smoke.

3. **First-run bootstrap UX** — The production container requires explicit `CREATE_SUPERUSER=1` on first run. Document this, add it to `.env.example`, and add a startup warning if no admin users exist.

### Medium-term (next month)

4. **Job queue observability** — The Level 4C health server on `:9090/healthz` is internal-only. Surface worker metrics to the admin dashboard (queue depth, last job status, uptime) via a dedicated widget.

5. **Embedding worker separation** — The Level 4B `--kinds` filter enables it. Actually split into two Compose services (one for FAISS/embedding with 4G mem, one for validation/sync with 2G) using a YAML anchor.

6. **Automated backup integration** — The project has a backup script pattern but no scheduled automation. Wire a cron container (or Dokploy scheduled task) to run daily volume backups to RustFS.

### Long-term (aspirational)

7. **Multi-arch images** — Current builds are `linux/amd64` only. Add `linux/arm64` for Apple Silicon Dokploy hosts or cloud migration.

8. **Database migration to PostgreSQL** — SQLite handles the current load but shared-volume contention and single-writer limitations will become bottlenecks at scale. Plan the migration path while the dataset is small (253 PDFs).

9. **Read-only standby** — Deploy a second web container in `read-only` mode (no writes, no admin) for load distribution. The maintenance worker is already the sole writer.

---

## Documentation Gaps Found

| Doc | Status |
|-----|--------|
| `.env.example` | **Missing** — needs creation |
| `COMPLETION_PLAN.md` | Updated with all 6 merge SHAs |
| `AGENTS.md` | Updated with migration discipline + test isolation + integration-branch rules |
| `scripts/ci/validate_migrations.py` | New — pre-merge guard |
| `scripts/ci/validate_public_html.py` | New — SEO validation (PR #47) |
| Production release evidence | Needs 2026-07-23 entry with all PRs + audit findings |
| `worker-entrypoint.sh` | Updated with migrate step |
| `run_maintenance_jobs.py` | Fully rewritten with recovery, Redis queue, health server |

---

## Next Immediate Action

Merge PR #50 (configurable footer), then Dokploy redeploy picks up both PR #49 (maintenance hardening) and #50. The maintenance container goes healthy. The footer is hidden by default unless `DISPLAY_SERVICE_FOOTER=1` is set in `.env`.
