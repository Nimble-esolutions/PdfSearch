Status: Active
Audience: Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-23
Canonical source: docs/ENVIRONMENT_CONTRACT.md
Supersedes: env.minimal, env.template

# Environment Contract

`.env.example` is the only tracked environment example. Deleted duplicate
templates (`env.minimal`, `env.template`) had stale PostgreSQL and loopback
Redis guidance and are superseded by this contract.

## Required In Production

Set these through Dokploy protected environment configuration:

```text
SECRET_KEY
OPENAI_API_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
CORS_ALLOWED_ORIGINS
PDFSEARCH_IMAGE
```

Production must keep:

```text
DEBUG=False
ALLOW_INSECURE_DEFAULTS=0
CREATE_SUPERUSER=0
```

`PDFSEARCH_IMAGE` must be an immutable GHCR digest tied to the approved Git SHA.
Tags such as `latest` or `dev` are compatibility aliases, not release identity.

## Runtime Data

The production data root is `/app/data`; code remains in the immutable image at
`/app/flowdocs`. Use these defaults unless an approved release record says
otherwise:

```text
DATA_ROOT=/app/data
SQLITE_DB_PATH=/app/data/db.sqlite3
MEDIA_ROOT=/app/data/media
STATIC_ROOT=/app/data/staticfiles
FAISS_INDEX_DIR=/app/data/faiss_indexes
CHROMA_DIR=/app/data/chroma_db
BACKUP_DIR=/app/data/backups
LEGACY_DATA_ROOT=/mnt/legacy
IMPORT_LEGACY_DATA=0
DATA_BOOTSTRAP_MODE=strict
DECLARED_SEED_DB=/app/init/db.sqlite3
DECLARED_SEED_MEDIA=/app/init/media
```

The current app uses SQLite through `SQLITE_DB_PATH`. `DB_ENGINE`, `DB_HOST`,
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_PORT` are not consumed by the
current settings module and must not appear in active examples.

## Search And AI

```text
OPENAI_EMBED_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini
PDF_CHUNK_SIZE=1200
PDF_CHUNK_OVERLAP=200
MAX_CONTEXT_WORDS=2500
TOP_K_CHUNKS=5
EMBEDDING_TTL=604800
SEARCH_CACHE_TTL=600
```

Model, chunking, and cache changes can affect FAISS compatibility, answer
quality, latency, and cost. Record the before/after values with any release that
changes them.

## Cache And Public Search

Production Compose owns Redis and passes `REDIS_URL=redis://redis:6379/1`.
Do not set a loopback Redis URL in Dokploy; `localhost` resolves inside the web
container and is rejected unless local insecure defaults are enabled.

Anonymous search is disabled by default:

```text
PUBLIC_SEARCH_ENABLED=0
PUBLIC_SEARCH_FOLDER_IDS=
PUBLIC_SEARCH_MAX_WORDS=30
PUBLIC_SEARCH_RATE_LIMIT=30
PUBLIC_SEARCH_RATE_WINDOW=60
```

If anonymous search is enabled, `PUBLIC_SEARCH_FOLDER_IDS` must be `all` or a
comma-separated allowlist of folder IDs.

## Bootstrap Credentials

`DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, and
`DJANGO_SUPERUSER_PASSWORD` are startup bootstrap inputs only when
`CREATE_SUPERUSER=1`. After the database user exists, changing these values in
`.env` does not rotate that account password. Use an authenticated Django admin
or management-command password change for existing users.

## Reserved Pass-Throughs

`GOOGLE_API_KEY` and `SENTRY_DSN` are retained as empty Compose pass-throughs for
future integrations or deployment compatibility. They are not active runtime
requirements in the current code.
