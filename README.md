# FlowDocs PDF Search

FlowDocs is a Django application for authorized users to upload PDF documents,
organize them into folders, and search them with natural-language questions.
It supports English and Marathi workflows and uses OpenAI-backed retrieval.

## Canonical Architecture

```text
/app/flowdocs       immutable Django application code from the image
/app/data           Dokploy-managed persistent runtime data
  db.sqlite3
  media/
  faiss_indexes/
  chroma_db/
  staticfiles/
  backups/
```

The production Compose file must not mount persistent data over
`/app/flowdocs`. The legacy `prod_flowdocs` volume is read-only and is used only
for a controlled data import.

## Local Development

```bash
cp .env.example .env
# Set local values; never use production secrets.
docker compose -f docker-compose.dev.yml up --build
```

The local Compose file uses separate named volumes and never references the
production legacy volume.

## Production Deployment

Production is deployed through Dokploy using `docker-compose.yml`.

- Container port: `8000`
- Liveness: `/livez`
- Readiness: `/readyz`
- Persistent state: `flowdocs_data` mounted at `/app/data`
- Secrets: Dokploy protected environment values
- Release: immutable image digest plus matching data release

See:

- [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) — Dokploy, migration, backup, restore, rollback
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — concise deployment contract
- [`docs/PRODUCTION_OPERATING_RULES.md`](docs/PRODUCTION_OPERATING_RULES.md) — safety rules
- [`docs/PERSISTENT_DATA_RELEASE.md`](docs/PERSISTENT_DATA_RELEASE.md) — data release contract
- [`docs/CLIENT_USER_MANUAL.md`](docs/CLIENT_USER_MANUAL.md) — client-facing usage guide
- [`docs/BUILD_AND_RELEASE_ROADMAP.md`](docs/BUILD_AND_RELEASE_ROADMAP.md) — image/runtime split roadmap

## Security Requirements

Production requires an explicit `SECRET_KEY`, `DEBUG=False`, explicit
`ALLOWED_HOSTS`, secure cookies, and protected API credentials. Do not commit
`.env` files, API keys, passwords, or copied production data.

Treat generated answers as assistance. Review the source references before
making an official decision.

## User Documentation

The client manual is a living document. Update it in the same change as any
user-visible behavior change and keep deployment/operator procedures out of it.
