# Dokploy Deployment Guide — FlowDocs

This application is deployed as a Dokploy **Compose** application. Dokploy owns
service naming, Traefik labels, deployment history, and environment injection.
The repository Compose file owns service behavior and persistent volume names.

## Deployment Contract

| Item | Value |
|---|---|
| Repository | `https://github.com/Nimble-esolutions/PdfSearch.git` |
| Branch | `dev` after the approved PR is merged |
| Compose file | `docker-compose.yml` |
| Container port | `8000` |
| Application health | `/livez` and `/readyz` |
| Persistent data mount | `/app/data` |
| Legacy import mount | `/mnt/legacy` read-only |
| Public TLS | Configured in Dokploy UI / Traefik |

Do not configure host bind paths such as `/home/prodsahakar/flowdocs`. They are
not part of the application contract.

## Dokploy UI Setup

1. Create or open the Compose application.
2. Select the repository and `dev` branch.
3. Set Compose path to `docker-compose.yml`.
4. Set the domain and container port to `8000`.
5. Configure HTTPS/Let's Encrypt in Dokploy.
6. Add environment variables from `.env.example` through the Dokploy UI.
7. Store secrets as protected environment values where supported.
8. Set `PDFSEARCH_IMAGE` to the exact image digest for the release.
9. Deploy only after the pre-deployment checklist passes.

Every successful `dev` release publishes both `:dev` and `:latest` as
compatibility aliases of the same tested digest. Keep both aliases enabled:
existing Dokploy Compose deployments may use either one without requiring a
Compose-file change. The aliases are convenience references, not the release
identity; record and prefer the immutable digest for production promotion.

## Persistent Data Layout

The named volume `flowdocs_data` is mounted at `/app/data`:

```text
/app/data/
├── db.sqlite3
├── media/pdfs/
├── faiss_indexes/
├── chroma_db/
├── staticfiles/
├── backups/
│   ├── db_backup_*.sqlite3
│   ├── json_backups/
│   └── chroma_backup/
└── .legacy_migration_complete
```

Application code remains in the immutable image under `/app/flowdocs`. Never
mount persistent data over `/app/flowdocs`; that would shadow new image code.

`prod_flowdocs` is mounted read-only at `/mnt/legacy` only during controlled
migration. It is not the ongoing application data store.

## First Migration From `prod_flowdocs`

This is a controlled one-time operation:

1. Take a Dokploy/host backup of `prod_flowdocs`.
2. Quiesce all writers to the legacy volume.
3. Confirm the new application image and Compose file are the intended release.
4. Set `IMPORT_LEGACY_DATA=1` in Dokploy for one deployment.
5. Deploy and inspect logs for `[legacy] Import complete`.
6. Verify database rows, media count, FAISS count, and a representative search.
7. Set `IMPORT_LEGACY_DATA=0` after successful verification.
8. Retain `prod_flowdocs` unchanged until the restore drill passes.

The importer copies mutable data only. It never copies legacy Python code.
It refuses to overwrite a non-empty active database without an explicit data
migration decision.

## Required Environment Values

Required in production:

```text
SECRET_KEY
OPENAI_API_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
CORS_ALLOWED_ORIGINS
REDIS_URL
PDFSEARCH_IMAGE
```

Do not use these production fallbacks:

```text
DEBUG=True
ALLOW_INSECURE_DEFAULTS=1
CREATE_SUPERUSER=1
ALLOWED_HOSTS=*
```

## Backup Policy

The application startup backup is only a local safety net. It is not a
replacement for Dokploy volume backups or off-host backups.

Back up the following together:

- `flowdocs_data` volume
- SQLite consistent snapshot
- media files
- FAISS indexes
- Chroma data
- active image digest
- Git SHA
- Compose configuration
- release manifest/checksums

For SQLite, use SQLite's `.backup` operation rather than copying the live file
while writes are active. Never copy a snapshot back over the live database as
part of a backup script.

## Restore Drill

At least monthly, restore into a disposable Dokploy application or volume:

1. Restore `flowdocs_data`.
2. Deploy the matching image digest.
3. Run `PRAGMA integrity_check`.
4. Verify PDF row count and media file count.
5. Load FAISS/Chroma indexes.
6. Run a representative search.
7. Verify `/readyz` and the public HTTPS route.
8. Record the result and release manifest.

A backup is not considered valid until this drill succeeds.

## Pre-Deployment Checklist

- [ ] PR merged into `dev` and required CI checks green.
- [ ] Image is identified by immutable digest.
- [ ] Previous image digest recorded for rollback.
- [ ] Current `flowdocs_data` backup completed.
- [ ] Legacy import flag is correct (`0` after first migration).
- [ ] Dokploy port is `8000`.
- [ ] Traefik route points to the web service on port `8000`.
- [ ] `DEBUG=False` and no insecure defaults are enabled.
- [ ] Disk and memory headroom verified.

## Post-Deployment Verification

```bash
curl -fsS https://<configured-domain>/livez
curl -fsS https://<configured-domain>/readyz
curl -fsS https://<configured-domain>/
```

Then verify one authenticated login, one PDF listing, one representative
search, and one static asset. Do not treat a root HTTP 200 alone as proof of
readiness.

## Rollback

Rollback is two-dimensional:

1. Application rollback: restore the previous immutable image digest.
2. Data rollback: restore the matching volume snapshot/release.

Do not roll back code across an incompatible database migration without the
matching data procedure. Record the rollback in the release/incident log.
