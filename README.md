Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-25
Canonical source: README.md
Supersedes: None

# FlowDocs PDF Search

FlowDocs is a Django application for authorized users to upload PDF documents,
organize them into folders, and search them with natural-language questions. It
supports English and Marathi workflows and uses OpenAI-backed retrieval.

## Start Here

Use the route that matches the work:

- Local development: [`docker-compose.dev.yml`](docker-compose.dev.yml) and [`docs/INDEX.md`](docs/INDEX.md#local-development)
- Dokploy deployment: [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md)
- Dokploy data persistence and safe redeploys: [`docs/DOKPLOY_DATA_PERSISTENCE.md`](docs/DOKPLOY_DATA_PERSISTENCE.md)
- Release promotion: [`docs/BUILD_AND_RELEASE_ROADMAP.md`](docs/BUILD_AND_RELEASE_ROADMAP.md)
- Production baseline: [`docs/PRODUCTION_BASELINE.md`](docs/PRODUCTION_BASELINE.md)
- Data custody and recovery: [`docs/DATA_CUSTODY_AND_PROMOTION.md`](docs/DATA_CUSTODY_AND_PROMOTION.md), [`docs/RUSTFS_RECOVERY_VAULT.md`](docs/RUSTFS_RECOVERY_VAULT.md), and [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- FAISS compatibility: [`docs/FAISS_COMPATIBILITY.md`](docs/FAISS_COMPATIBILITY.md)
- Incident response: [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- Client usage: [`docs/CLIENT_USER_MANUAL.md`](docs/CLIENT_USER_MANUAL.md)
- Public/admin design lock: [`docs/design/AI_SAHAKAR_UI_CONTRACT.md`](docs/design/AI_SAHAKAR_UI_CONTRACT.md)
- Developer UI guide: [`docs/AI_SAHAKAR_DEVELOPER_GUIDE.md`](docs/AI_SAHAKAR_DEVELOPER_GUIDE.md)
- Admin user guide: [`docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md`](docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md)
- `dev` cleanup scope: [`docs/DEV_CLEANUP_SCOPE.md`](docs/DEV_CLEANUP_SCOPE.md)
- Environment impact guide and reviewed examples: [`docs/ENVIRONMENT_CONFIGURATION_GUIDE.md`](docs/ENVIRONMENT_CONFIGURATION_GUIDE.md), [`docs/environments/`](docs/environments/)
- Contribution and dev-to-release workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Documentation map and historical context: [`docs/INDEX.md`](docs/INDEX.md)

## Prerequisites

- Docker Engine with the Compose plugin
- A local `.env` created from [`.env.example`](.env.example)
- Environment details from [`docs/ENVIRONMENT_CONTRACT.md`](docs/ENVIRONMENT_CONTRACT.md)
- Local-only credentials when search or external integrations are exercised

Never copy production secrets or production data into a local environment.

## Local Development

```bash
cp .env.example .env
# Set local values; never use production secrets.
docker compose -f docker-compose.dev.yml up --build
```

The development Compose file uses `flowdocs_data_dev` and `redis_data_dev`.
It does not mount the production legacy volume. Stop local services with
`docker compose -f docker-compose.dev.yml stop`; do not use `down -v` when data
needs to be retained.

## Product surfaces and design lock

The public `/` route is the AI Sahakar **Civic Knowledge Workbench**: a
three-region evidence-led workspace on widescreens that collapses into a
single-column conversation with accessible source drawers on smaller screens.
The authenticated surface is the **Operations Cockpit** admin UI. These are
protected product directions. Enhancements are welcome when they improve
clarity, evidence access, accessibility, or performance while preserving the
contract; a change of visual direction requires an explicit human request.

Read the [active UI contract](docs/design/AI_SAHAKAR_UI_CONTRACT.md) before
changing templates, CSS, JavaScript, translations, browser tests, or visual
documentation. The tracked agent skill is
[`skills/ai-sahakar-ui-contract/SKILL.md`](skills/ai-sahakar-ui-contract/SKILL.md).

## Architecture

```text
web container
  /app/flowdocs              immutable Django application code from the image
  /app/data                  mutable application data
    db.sqlite3
    media/
    faiss_indexes/
    chroma_db/
    staticfiles/
    backups/
    .instance_id             stable instance identity
  /app/data-control          activation control plane
    active-generation        atomic symlink to active workspace
    previous-generation      rollback target
    activation-journals/     crash recovery journals

redis container
  /data                      cache/queue persistence in redis_data
```

Production Compose mounts `flowdocs_data` at `/app/data`. The external
`prod_flowdocs` volume at `/mnt/legacy:ro` is legacy evidence/quarantine only; it
is not an automatic import source. Legacy and active data must not be copied
directly or treated as one database. Promotion requires inventory, conflict
classification, staged restore, FAISS fingerprint validation, and an explicit
operator decision. It must never mount persistent data over `/app/flowdocs`.

Key new modules (2026-07-24): `core/environment.py` (startup identity validation),
`core/side_effects.py` (external call gating), `core/ai_guard.py` (OpenAI containment),
`core/activate.py` (atomic generation activation), `core/restore_pipeline.py`
(download→validate→sanitize→rehearse→activate), `core/global_writer.py` (CAS writer
fencing), `core/registration.py` (dataset registration), `core/backup_policy.py`
(dirty-state tracking), `core/sanitize.py` (PII sanitization), `core/rehearsal.py`
(migration rehearsal), `core/activation_journal.py` (crash recovery),
`core/lease.py` (writer lease), `core/compatibility.py` (pre-activation checks),
`core/metrics.py` (Prometheus), `core/namespace.py` (S3 key builder),
`core/object_store_capabilities.py` (S3 capability probing).
See [`docs/ARCHITECTURE_OVERVIEW.md`](docs/ARCHITECTURE_OVERVIEW.md) for the full map.

## Production Contract

Canonical production is `https://ai-sahakar.net` with
`https://www.ai-sahakar.net` as the canonical alias. It is deployed through
Dokploy as the Compose application defined by [`docker-compose.yml`](docker-compose.yml).
`https://2026.ai-sahakar.net` was the preview/verification host and is retained
as historical rollback evidence. The exact image digest is the production
release identity and must be recorded from Dokploy.

- Container port: `8000`
- Liveness: `/livez` proves process liveness
- Readiness: `/readyz` checks database, configured cache, migrations, data state, and backup status
- Health: `/health/data/` (generation status), `/health/lease/` (writer lease), `/health/metrics/` (Prometheus)
- Redis: production Compose pins the web service to `redis://redis:6379/1`;
  do not override it with `localhost`, which points back to the web container
- Persistent state: Compose volume `flowdocs_data` at `/app/data`
- Legacy data: external `prod_flowdocs` at `/mnt/legacy:ro`, read-only quarantine only
- Secrets: Dokploy protected environment values
- Release identity: `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:<digest>`
- Pull policy: the effective Dokploy Compose configuration must use `pull_policy: always`

The repository keeps tag defaults for compatibility, but production must set
`PDFSEARCH_IMAGE` to the exact digest and verify the running container's digest.
Tags such as `:latest` are never release identity and must not be reused from a
stale local cache.

Deploying a new image normally recreates the container while retaining the
Compose-managed `/app/data` named volume. This is conditional on preserving the
Dokploy project and volume mapping; deleting the project, changing the project
or volume name, or using `down -v` can create an empty volume or delete data.
Read [`DOKPLOY_DATA_PERSISTENCE.md`](docs/DOKPLOY_DATA_PERSISTENCE.md) before
enabling autodeploy or pressing Deploy.

## Current Data-Custody Boundary

Post-reconciliation (2026-07-22): 253 PDF rows, 242 recovered PDF files, 53 folders,
8 users, and 51 rebuilt FAISS indexes with 8,753 vectors at dimension 1536. Eleven
target-only PDF rows remain preserved but unrecovered.

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped active
and legacy snapshots and checksums. Application-level S3 integration is implemented
through the artifact vault adapter, dataset registration, global writer fencing,
namespace-scoped keys, object store capability probing, and a full restore pipeline
(download→validate→sanitize→rehearse→activate). The bucket remains an operator
recovery vault; automatic cross-environment sync is planned but not yet automated.

## Verification Gates

Every documentation or release change must pass the applicable link/path scan,
Mermaid validation, `docker compose -f docker-compose.yml config`, `/livez`,
`/readyz`, PDF count, FAISS count, and representative search gates. Record the
source SHA, exact image digests, Compose evidence, data snapshot/checksum
references, and explicit promotion decision.

## Security and Recovery Warnings

Production requires an explicit `SECRET_KEY`, `DEBUG=False`, explicit
`ALLOWED_HOSTS`, secure cookies, and protected API credentials. Do not commit
`.env` files, API keys, passwords, or copied production data.

Do not run `docker compose down -v` against production. It can remove named
volumes and destroy the recovery set. Back up and restore into an isolated
volume or disposable Dokploy application instead.

Treat generated answers as assistance. Review source references before making an
official decision.

## Contribution and Release Links

- Deployment contract: [`DEPLOYMENT.md`](DEPLOYMENT.md)
- Operating rules: [`docs/PRODUCTION_OPERATING_RULES.md`](docs/PRODUCTION_OPERATING_RULES.md)
- Release evidence: [`docs/BUILD_AND_RELEASE_ROADMAP.md`](docs/BUILD_AND_RELEASE_ROADMAP.md)
- Persistent data contract: [`docs/PERSISTENT_DATA_RELEASE.md`](docs/PERSISTENT_DATA_RELEASE.md)
- Client manual: [`docs/CLIENT_USER_MANUAL.md`](docs/CLIENT_USER_MANUAL.md)
- UI guides: [`docs/AI_SAHAKAR_DEVELOPER_GUIDE.md`](docs/AI_SAHAKAR_DEVELOPER_GUIDE.md) and [`docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md`](docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md)
- Historical documents and supersession map: [`docs/INDEX.md`](docs/INDEX.md#historical-context)

Update the client manual in the same change as user-visible behavior changes;
keep deployment and operator procedures out of it.
