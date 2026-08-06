Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-08-05
Canonical source: README.md
Supersedes: None

# FlowDocs PDF Search

[![Docker CI and Release](https://github.com/Nimble-esolutions/PdfSearch/actions/workflows/docker-build.yml/badge.svg?branch=dev)](https://github.com/Nimble-esolutions/PdfSearch/actions/workflows/docker-build.yml)
[![Documentation contract](https://github.com/Nimble-esolutions/PdfSearch/actions/workflows/docs-contract.yml/badge.svg?branch=dev)](https://github.com/Nimble-esolutions/PdfSearch/actions/workflows/docs-contract.yml)

FlowDocs is a Django application for authorized users to upload PDF documents,
organize them into folders, and search them with natural-language questions. It
supports English, Marathi, and Hindi document processing: native PDF extraction
is preferred, with bounded local Tesseract OCR for scanned pages, followed by
the existing embedding and retrieval workflow.

> **Current operational state:** stage serves the signed 242-document runtime,
> all readiness checks pass, real English/Marathi search and manual
> backup/recovery rehearsal are proven, and the legacy production service is
> unchanged. Read the living [project handoff](docs/HANDOFF.md) before operating
> recovery, deployment, or persistent-data workflows.

## Start Here

Use the route that matches the work:

- Local development: [`docker-compose.dev.yml`](docker-compose.dev.yml) and [`docs/INDEX.md`](docs/INDEX.md#local-development)
- Dokploy deployment: [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md)
- Dokploy data persistence and safe redeploys: [`docs/DOKPLOY_DATA_PERSISTENCE.md`](docs/DOKPLOY_DATA_PERSISTENCE.md)
- Release promotion: [`docs/BUILD_AND_RELEASE_ROADMAP.md`](docs/BUILD_AND_RELEASE_ROADMAP.md)
- Production baseline: [`docs/PRODUCTION_BASELINE.md`](docs/PRODUCTION_BASELINE.md)
- Data custody and recovery: [`docs/DATA_CUSTODY_AND_PROMOTION.md`](docs/DATA_CUSTODY_AND_PROMOTION.md), [`docs/RUSTFS_RECOVERY_VAULT.md`](docs/RUSTFS_RECOVERY_VAULT.md), [`docs/INTERNAL_VAULT_MIGRATION.md`](docs/INTERNAL_VAULT_MIGRATION.md), and [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- FAISS compatibility: [`docs/FAISS_COMPATIBILITY.md`](docs/FAISS_COMPATIBILITY.md)
- Incident response: [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- Client usage: [`docs/CLIENT_USER_MANUAL.md`](docs/CLIENT_USER_MANUAL.md)
- Public/admin design lock: [`docs/design/AI_SAHAKAR_UI_CONTRACT.md`](docs/design/AI_SAHAKAR_UI_CONTRACT.md)
- Developer UI guide: [`docs/AI_SAHAKAR_DEVELOPER_GUIDE.md`](docs/AI_SAHAKAR_DEVELOPER_GUIDE.md)
- Admin user guide: [`docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md`](docs/AI_SAHAKAR_ADMIN_USER_GUIDE.md)
- Multi-file intake and document lifecycle: [`docs/DOCUMENT_INTAKE_WORKBENCH.md`](docs/DOCUMENT_INTAKE_WORKBENCH.md)
- `dev` cleanup scope: [`docs/DEV_CLEANUP_SCOPE.md`](docs/DEV_CLEANUP_SCOPE.md)
- Environment impact guide and reviewed examples: [`docs/ENVIRONMENT_CONFIGURATION_GUIDE.md`](docs/ENVIRONMENT_CONFIGURATION_GUIDE.md), [`docs/environments/`](docs/environments/)
- Contribution and dev-to-release workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Documentation map and historical context: [`docs/INDEX.md`](docs/INDEX.md)
- Current project handoff: [`docs/HANDOFF.md`](docs/HANDOFF.md)

## Prerequisites

For the complete environment-variable reference, see
[docs/ENVIRONMENT_REFERENCE.md](docs/ENVIRONMENT_REFERENCE.md).

Current operational references:

- [living current handoff](docs/HANDOFF.md)
- [2026-08-03 status snapshot](docs/STATUS-2026-08-03.md)
- [2026-08-02 migration snapshot](docs/STATUS-2026-08-02.md)
- [dated operations changelog](docs/OPERATIONS_CHANGELOG-2026-08-02.md)
- [legacy-versus-current state](docs/LEGACY_VS_CURRENT_STATE.md)

- Docker Engine with the Compose plugin
- A local `.env` created from [`.env.example`](.env.example)
- Environment details from [`docs/ENVIRONMENT_CONTRACT.md`](docs/ENVIRONMENT_CONTRACT.md)
- Local-only credentials when search or external integrations are exercised

Never copy production secrets or production data into a local environment.

## Local Development

```bash
cp .env.example .env
# Set local values; never use production secrets.
LOCAL_BUILD_REVISION="$(git rev-parse --short HEAD)" \
  docker compose -f docker-compose.dev.yml up -d --build --wait
```

The development Compose file uses `flowdocs_data_dev` and `redis_data_dev`.
It does not mount the production legacy volume. Stop local services with
`docker compose -f docker-compose.dev.yml stop`; do not use `down -v` when data
needs to be retained.

## Product surfaces and design lock

The public `/` route defaults to **Classic search**, the approved
`training.ai-sahakar.net` service composition rebuilt without CDN Bootstrap,
inline application JavaScript, unsafe HTML insertion, or heavy bitmap assets.
The **Knowledge Workbench** remains an isolated secondary frontend and can be
previewed with `/?view=workbench`. A superadmin can choose the primary view in
Settings; URL previews never persist. Both views share the same secured Django
search/PDF backend and complete English/Marathi session behavior, but not
templates, presentation CSS, or frontend JavaScript.

The public information pages at `/privacy/`, `/terms/`, `/data-policy/`,
`/cookies/`, and `/disclaimer/` follow the same saved or explicitly previewed
theme. They use a standalone public document shell—never the authenticated
admin layout—and preserve only allowlisted `?view=classic|workbench` previews
across policy navigation and language switching.

The authenticated surface remains the **Operations Cockpit** admin UI.
Public-theme selection is the only admin change in this feature.

Read the [active UI contract](docs/design/AI_SAHAKAR_UI_CONTRACT.md) before
changing templates, CSS, JavaScript, translations, browser tests, or visual
documentation. The tracked agent skill is
[`skills/ai-sahakar-ui-contract/SKILL.md`](skills/ai-sahakar-ui-contract/SKILL.md).
The isolation and selection boundary is documented in
[`docs/design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md`](docs/design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md).

## Architecture

The deployed system has four cooperating boundaries:

1. **Application plane** — Django/Gunicorn web, maintenance worker, Redis
   cache/queue, SQLite, media, FAISS, Chroma, and PDF cache.
2. **Control plane** — durable control SQLite, signed activation intent,
   runtime-generation pointers, evidence, leases, and recovery journals.
3. **Recovery plane** — RustFS immutable, dataset-scoped manifests and
   content-addressed objects accessed through explicit Data Operations profiles.
4. **External AI plane** — local OCR first; extracted text may use the existing
   embedding provider under the configured side-effect policy. Original PDFs
   never leave custody because OCR is local.

Visual sources:
[legacy-to-stage-2026.mmd](docs/diagrams/legacy-to-stage-2026.mmd),
[stage-recovery-state.mmd](docs/diagrams/stage-recovery-state.mmd), and
[ocr-index-lifecycle.mmd](docs/diagrams/ocr-index-lifecycle.mmd).

### Repository map: where to start

| Area | Owns | Start with |
| --- | --- | --- |
| Django shell | settings, URLs, WSGI/ASGI, runtime paths | flowdocs/flowdocs/ |
| Safety and lifecycle | environment identity, upload intake, document lifecycle, restore, activation, leases | flowdocs/core/ |
| Document intelligence | PDF extraction, OCR fallback, chunking, embeddings, FAISS/Chroma | flowdocs/data/ |
| Data Operations | profile selection, backup/restore routes, operation contracts, readiness | flowdocs/dataops/ |
| Vault Operations | control database, workbench, receipts, signed activation, supervisors | flowdocs/vaultops/ |
| Delivery and operations | migration, recovery certification, CI contracts, parity checks | scripts/ |
| Verification | browser behavior, RustFS/MinIO lifecycle, restore and process-death gates | browser_tests/ and integration_tests/ |
| Documentation | contracts, runbooks, evidence, architecture and lifecycle diagrams | docs/ |

For a change, identify the owning row first, then trace callers and tests
before editing. The graph-backed architecture query is the source for this
grouping; the tree below is a navigation aid, not a claim that every file has
the same runtime role.

```mermaid
flowchart LR
  U["Users / operators"] --> W["Django web + workbench"]
  W --> D["flowdocs/data<br/>PDF, OCR, embeddings, indexes"]
  W --> O["flowdocs/dataops<br/>profiles + operations"]
  O --> V["flowdocs/vaultops<br/>receipts + signed activation"]
  V --> R["RustFS<br/>immutable datasets"]
  D --> Q["SQLite + media + FAISS + Chroma"]
  W --> C["Redis<br/>cache + queue"]
  T["scripts + browser_tests + integration_tests"] --> W
```

### Project structure

    flowdocs/
      flowdocs/       Django settings, URLs, WSGI/ASGI, runtime configuration
      core/           environment, safety, data lifecycle, activation primitives
      data/           PDF models, extraction, OCR, embeddings, indexing
      dataops/        profile resolution, backup/restore operation contracts
      vaultops/       control plane, workbench, activation, receipts, read models
      locale/         application translation assets
    scripts/
      ci/             contract, parity, lifecycle, and release checks
      ops/            migration, recovery certification, and operator utilities
      runtime/        runtime and maintenance helper scripts
    browser_tests/    Playwright public, admin, search, and workbench gates
    integration_tests/ RustFS/MinIO, restore, activation, process-death tests
    docs/
      diagrams/       Mermaid architecture and lifecycle sources
      dataops/        Data Operations contracts and rollout procedures
      environments/   reviewed non-secret environment examples
      HANDOFF.md      living current operational authority
      STATUS-*.md     dated historical operational evidence
    init/             declared seed database and image-provided index assets
    Dockerfile        immutable application image definition
    docker-compose*.yml  local, CI, integration, recovery, and Dokploy contracts
    requirements-web.txt / requirements-web.lock  input and hashed dependencies

Keep mutable data in named volumes, application code in the image, and
operational evidence in the control boundary. Do not treat init assets,
browser reports, local graph output, or generated indexes as interchangeable
with the production data volume.

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
The current non-production rehearsal host is
https://2026.ai-sahakar.net. It serves a signed 242-document runtime and remains
outside production traffic. Runtime/search readiness and the separate backup
rehearsal are reported independently: a disposable stage backup receipt is
valuable recovery evidence, but it is not an availability prerequisite.

- Container port: `8000`
- Liveness: `/livez` proves process liveness
- Readiness: `/readyz` checks database, configured cache, migrations, data state, and backup status
- Health: `/health/data/` (generation status), `/health/lease/` (writer lease), `/health/metrics/` (Prometheus)
- Redis: production Compose pins the web service to `redis://redis:6379/1`;
  do not override it with `localhost`, which points back to the web container
- Persistent state: Compose volume `flowdocs_data` at `/app/data`
- Legacy data: external `prod_flowdocs` at `/mnt/legacy:ro`, read-only quarantine only
- Secrets: Dokploy protected environment values
- Stage image channel: `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest`
- Stage evidence: record the resolved running digest after every pull/deploy
- Production release identity: `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:<digest>`
- Pull policy: the effective Dokploy Compose configuration must use `pull_policy: always`

The 2026 stage deliberately tracks `:latest` for both `PDFSEARCH_IMAGE` and
`APP_IMAGE_DIGEST`; `pull_policy: always` plus the resolved container digest
provides its deployment evidence. Production must instead set
`PDFSEARCH_IMAGE` to an approved immutable digest. Never infer the running
artifact from a tag alone.

Deploying a new image normally recreates the container while retaining the
Compose-managed `/app/data` named volume. This is conditional on preserving the
Dokploy project and volume mapping; deleting the project, changing the project
or volume name, or using `down -v` can create an empty volume or delete data.
Read [`DOKPLOY_DATA_PERSISTENCE.md`](docs/DOKPLOY_DATA_PERSISTENCE.md) before
enabling autodeploy or pressing Deploy.

## Current Data-Custody Boundary

As of 2026-08-03, the legacy source boundary is unchanged: prod_flowdocs
contains 242 PDFs, 46 folders, 7 users, and 29 migrations. The verified
RustFS v2 source generation is legacy-20260802T085639Z-86288855; the stage
clone is clone-legacy-20260802T085639Z-86288855 with 416 objects totaling
1,093,501,777 bytes. The stage-owned import is now bound to a
signature-verified runtime pointer and serves 242/242 indexed documents.

Stage is reachable at https://2026.ai-sahakar.net; healthy containers and a
root response do not replace `/readyz`. The deployed readiness code projects
the valid v3 pointer, exact generation, manifest, and indexing ratio. Manual
stage backup and isolated rehearsal have also succeeded. Use
[docs/HANDOFF.md](docs/HANDOFF.md) for current evidence.

The old July reconciliation numbers below are retained as historical baseline
evidence, not as the current 2026 migration inventory. See
docs/HANDOFF.md and docs/LEGACY_VS_CURRENT_STATE.md.

## Historical reconciliation baseline

Post-reconciliation (2026-07-22): 253 PDF rows, 242 recovered PDF files, 53 folders,
8 users, and 51 rebuilt FAISS indexes with 8,753 vectors at dimension 1536. Eleven
target-only PDF rows remain preserved but unrecovered.

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` contains timestamped active
and legacy snapshots and checksums. Application-level S3 integration is implemented
through the artifact vault adapter, dataset registration, global writer fencing,
namespace-scoped keys, object store capability probing, and a full restore pipeline
(download→validate→sanitize→rehearse→activate).

The 2026-07-26 audit verified those primitives against disposable MinIO, but
also confirmed that the normal admin/worker path and startup entrypoints are
not yet connected to one end-to-end restore orchestrator. Scheduled backup is
not currently reliable, and a fresh volume does not auto-pull from RustFS.
Treat the bucket as an explicit operator recovery component and read
[`RUSTFS_RECOVERY_VAULT.md`](docs/RUSTFS_RECOVERY_VAULT.md) before depending on
it for a deploy, restore, or disaster-recovery decision.

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

The canonical local stack builds one `pdfsearch-dev:local` application image
for web, maintenance, and bucket initialization, and starts pinned RustFS on
localhost-only ports. Stage and production instead pull one immutable image
digest and use externally operated RustFS. MinIO is retained only as a
separately named S3 compatibility test; it is not RustFS certification. Native
local builds set `APP_RELEASE_VERSION` from `LOCAL_BUILD_REVISION` and leave
`APP_IMAGE_DIGEST` empty. Existing MinIO and RustFS volumes are never reused or
removed automatically.
