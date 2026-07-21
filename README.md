Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-22
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
- Release promotion: [`docs/BUILD_AND_RELEASE_ROADMAP.md`](docs/BUILD_AND_RELEASE_ROADMAP.md)
- Backup and data recovery: [`docs/PERSISTENT_DATA_RELEASE.md`](docs/PERSISTENT_DATA_RELEASE.md) and [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- Incident response: [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- Client usage: [`docs/CLIENT_USER_MANUAL.md`](docs/CLIENT_USER_MANUAL.md)
- Documentation map and historical context: [`docs/INDEX.md`](docs/INDEX.md)

## Prerequisites

- Docker Engine with the Compose plugin
- A local `.env` created from [`.env.example`](.env.example)
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

## Architecture

```text
web container
  /app/flowdocs       immutable Django application code from the image
  /app/data           mutable application data
    db.sqlite3
    media/
    faiss_indexes/
    chroma_db/
    staticfiles/
    backups/

redis container
  /data                cache/queue persistence in redis_data
```

Production Compose mounts `flowdocs_data` at `/app/data` and the external
`prod_flowdocs` volume at `/mnt/legacy:ro` for an explicitly enabled, controlled
legacy data import only. It must never mount persistent data over
`/app/flowdocs`.

## Production Contract

Production is deployed through Dokploy as the Compose application defined by
[`docker-compose.yml`](docker-compose.yml).

- Container port: `8000`
- Liveness: `/livez` proves process liveness
- Readiness: `/readyz` checks database, configured cache, and migrations
- Persistent state: Compose volume `flowdocs_data` at `/app/data`
- Legacy import: external `prod_flowdocs` at `/mnt/legacy:ro`, one-time only
- Secrets: Dokploy protected environment values
- Release identity: `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:<digest>`

The release workflow also maintains `:dev` and `:latest` as compatibility
aliases for the same tested image digest. They are convenience references, not
immutable release identity. Record the digest before promotion.

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
- Historical documents and supersession map: [`docs/INDEX.md`](docs/INDEX.md#historical-context)

Update the client manual in the same change as user-visible behavior changes;
keep deployment and operator procedures out of it.
