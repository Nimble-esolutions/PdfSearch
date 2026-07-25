Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-25
Canonical source: DEPLOYMENT_GUIDE.md
Supersedes: None

# Dokploy Deployment Guide — FlowDocs

This application is deployed as a Dokploy **Compose** application. Dokploy owns
service naming, Traefik labels, deployment history, and environment injection.
The repository Compose file owns service behavior and persistent volume names.

Read [`docs/DOKPLOY_DATA_PERSISTENCE.md`](docs/DOKPLOY_DATA_PERSISTENCE.md)
before using Deploy or enabling autodeploy. A normal redeploy replaces the
container but retains the named `/app/data` volume only while the Dokploy
project and volume mapping remain unchanged. It is not a backup and it is not
safe to delete/recreate the project or run `down -v`.

## Deployment Contract

| Item | Value |
|---|---|
| Repository | `https://github.com/Nimble-esolutions/PdfSearch.git` |
| Branch | `dev` after the approved PR is merged |
| Compose file | `docker-compose.yml` |
| Container port | `8000` |
| Application health | `/livez` and `/readyz` |
| Persistent data mount | `/app/data` |
| Legacy custody mount | `/mnt/legacy` read-only quarantine |
| Public TLS | Configured in Dokploy UI / Traefik |

Do not configure undocumented host bind paths. They are not part of the
application contract.

## Preflight Evidence

Run these read-only checks before changing the Dokploy application:

```bash
git rev-parse HEAD
sha256sum docker-compose.yml
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml config --images
docker compose -f docker-compose.yml config --volumes
docker system df
free -h
```

The Git SHA, intended web and Redis image digests, Compose file hash, current
container images, and current data-volume identity belong in the release record.
`config --images` may show repository tag defaults, but production promotion
must set every production image to an exact `repo@sha256:<digest>` value in
Dokploy and use `pull_policy: always`. Do not accept a stale local tag cache.

Current canonical production domains are `https://ai-sahakar.net` and
`https://www.ai-sahakar.net`. The verified preview baseline was
`https://2026.ai-sahakar.net`; retain it only as historical rollback evidence.
See [`PRODUCTION_BASELINE.md`](docs/PRODUCTION_BASELINE.md) for the current
custody counts and release boundary.

## Dokploy UI Setup

1. Create or open the Compose application.
2. Select the repository and `dev` branch.
3. Set Compose path to `docker-compose.yml`.
4. Set the domain and container port to `8000`.
5. Configure HTTPS/Let's Encrypt in Dokploy.
6. Add environment variables from `.env.example` through the Dokploy UI.
7. Store secrets as protected environment values where supported; never paste them into Git or an incident record.
8. Set `PDFSEARCH_IMAGE` to the exact image digest for the release.
9. Confirm the Compose application resolves the web service to container port `8000` and the external `dokploy-network` is attached.
10. Confirm the configured domain and TLS route point to this Compose application, not a static application.
11. Deploy only after the pre-deployment checklist passes.

The Deploy button is an image/service lifecycle action, not a data reset. It
must not be used as a substitute for a backup, restore, or data cleanup. After
deployment, verify both the running immutable image digest and the actual
`/app/data` volume identity.

## GitHub/Dokploy Source Of Truth

Production deployment is controlled by the GitHub-connected Dokploy application.
The server checkout under `/etc/dokploy/compose/.../code` and its ignored `.env`
are generated deployment state, not a place to make normal fixes.

- Repository: `Nimble-esolutions/PdfSearch`
- Branch: `dev`
- Compose path: `docker-compose.yml`
- Release identity: Git SHA, OCI image digest, Compose hash, Dokploy deployment ID, and data generation
- Image identity: `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:<digest>`

The GitHub workflow publishes on branch pushes. Dokploy must use a compatible
branch/webhook trigger; a tag-only Dokploy trigger with no polling does not
deploy branch-push releases. Before deployment, compare the GitHub branch HEAD,
Dokploy checkout HEAD, OCI revision, and release-manifest digest. Stop on any
mismatch.

Do not use `docker compose up` from the server as the normal deployment path.
Do not edit the Dokploy checkout or `.env` directly. An explicitly authorized
emergency test must have a backup, rollback command, expiry, and a follow-up
repository/Dokploy fix.

The workflow may publish compatibility aliases such as `:dev` and `:latest`, but
they are not production release identity. Dokploy must pull the exact recorded
digests with `pull_policy: always`; never rely on a local alias or cached
`latest` image.

## Production Domain Cutover

The canonical production host is `ai-sahakar.net`; `www.ai-sahakar.net` is its
canonical alias. `2026.ai-sahakar.net` was the preview/verification host and
must be retained only for rollback validation until the cutover is accepted.

1. Point DNS for both `ai-sahakar.net` and `www.ai-sahakar.net` to the Traefik ingress address.
2. Configure both hostnames in the Dokploy application domain settings.
3. Confirm Traefik has one intended router/backend per hostname and valid TLS certificates.
4. Verify `/livez`, `/readyz`, root, static assets, authenticated PDF view, and representative search on both canonical hosts.
5. Keep the preview route unchanged during the observation window; remove or restrict it only after rollback evidence is no longer required.
6. Record the DNS/TLS cutover time, live image digest, data-volume identity, and smoke results in the release record.

## Named Volume Discovery

Dokploy may prefix logical Compose volume names with the application or project
name. Discover the actual volume from the running web container instead of
guessing its host name:

```bash
docker compose -f docker-compose.yml ps
docker inspect "$(docker compose -f docker-compose.yml ps -q web)" \
  --format '{{range .Mounts}}{{println .Name .Source .Destination .RW}}{{end}}'
docker volume ls --filter label=com.dokploy.backup=true
docker volume inspect <discovered-flowdocs-data-volume>
```

The entry whose destination is `/app/data` is the active application data
volume. The entry whose destination is `/mnt/legacy` must be read-only and is a
quarantine/evidence source only. Stop immediately if `/app/data` is missing,
points to an unexpected volume, or any volume is mounted over `/app/flowdocs`.

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

`prod_flowdocs` is mounted read-only at `/mnt/legacy` only for controlled
evidence and recovery work. It is not the ongoing application data store.

## Legacy and Active Data Promotion

Legacy data must not be copied directly into active data. The verified baseline
is divergent: legacy has 242 PDFs and 45 FAISS files; active has 17 PDF rows, 0
PDF files, and 11 FAISS files; only 6 PDF paths overlap. Use the procedure in
[`DATA_CUSTODY_AND_PROMOTION.md`](docs/DATA_CUSTODY_AND_PROMOTION.md):

1. Preserve both sources and take or verify timestamped snapshots/checksums.
2. Restore each source into separate read-only quarantine targets.
3. Inventory database rows, PDF paths, and FAISS files without copying content.
4. Classify conflicts and missing references; do not infer that an overlap is
   equivalent content.
5. Build a staged restore from an explicit selection, then validate FAISS
   fingerprints and representative search.
6. Promote only after an operator records the exact scope and decision.

`IMPORT_LEGACY_DATA` is not a substitute for reconciliation or promotion.
Retain `prod_flowdocs` unchanged until the staged restore and restore drill
pass.

## Required Environment Values

Required in production:

```text
SECRET_KEY
OPENAI_API_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
CORS_ALLOWED_ORIGINS
PDFSEARCH_IMAGE
```

See [`docs/ENVIRONMENT_CONTRACT.md`](docs/ENVIRONMENT_CONTRACT.md) for the full
runtime variable contract, defaults, stale-template removals, bootstrap caveats,
and search/model tuning knobs.

The production Compose stack owns Redis and pins the web service to
`redis://redis:6379/1`. Do not set `REDIS_URL` to `localhost` or
`127.0.0.1` in Dokploy: those addresses resolve inside the web container, not
to the Compose Redis service. The application rejects loopback Redis URLs when
local insecure defaults are disabled.

Anonymous search is opt-in and fail-closed. Set `PUBLIC_SEARCH_ENABLED=1`
only when the approved shared corpus is ready, and set
`PUBLIC_SEARCH_FOLDER_IDS` to a comma-separated allowlist or `all` when the
current and future admin-created corpus is intentionally public. Private
folders remain authentication-bound unless `all` is explicitly selected.

Do not use these production fallbacks:

```text
DEBUG=True
ALLOW_INSECURE_DEFAULTS=1
CREATE_SUPERUSER=1
ALLOWED_HOSTS=*
```

`DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, and
`DJANGO_SUPERUSER_PASSWORD` are bootstrap inputs only when
`CREATE_SUPERUSER=1`. Changing them in `.env` does not update an existing
database user. Rotate existing admin credentials through Django admin or a
reviewed management-command password change.

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
- current release record and any available checksums

For SQLite, use SQLite's `.backup` operation rather than copying the live file
while writes are active. A simple operator-controlled snapshot is:

```bash
docker compose -f docker-compose.yml exec -T web \
  sqlite3 /app/data/db.sqlite3 \
  ".backup '/app/data/backups/db_backup_manual.sqlite3'"
```

Then copy or archive the volume through the approved Dokploy/host backup
process. If a consistent whole-volume archive requires quiescing writers, stop
the web service first and record the maintenance window. Never copy a snapshot
back over the live database as part of a backup script.

Retention must protect the active release, the previous known-good release, and
all backups referenced by an open incident. Apply the organization retention
period after those holds are satisfied. Record the backup location and expiry;
do not place backup contents or credentials in Git.

Never run `docker compose down -v` in production. It can remove the named data
volumes. Use `stop`, an approved Dokploy backup, and an isolated restore target.

## Restore Drill

At least monthly, restore into a disposable Dokploy application or volume:

1. Restore into a disposable Dokploy application or uniquely named volume, never the active `flowdocs_data` volume.
2. Deploy the matching image digest and set the disposable application to use only the restored data volume.
3. Run `PRAGMA integrity_check`.
4. Verify PDF row count and media file count.
5. Load FAISS/Chroma indexes.
6. Run a representative search.
7. Verify `/readyz` and the public HTTPS route.
8. Record the result, image digests, Git SHA, volume identity, custody
   snapshot/checksum references, and current release record. There is no
   generated artifact manifest in the current workflow.

A backup is not considered valid until this drill succeeds.

## Pre-Deployment Checklist

- [ ] PR merged into `dev` and required CI checks green.
- [ ] Image is identified by immutable digest.
- [ ] Previous image digest recorded for rollback.
- [ ] Current `flowdocs_data` backup completed.
- [ ] Legacy mount/import controls are disabled unless a reviewed quarantine
      operation explicitly requires them.
- [ ] Dokploy port is `8000`.
- [ ] Traefik route points to the web service on port `8000`.
- [ ] `DEBUG=False` and no insecure defaults are enabled.
- [ ] Disk and memory headroom verified.
- [ ] Actual `/app/data` volume discovered from the web container and matches the intended backup.
- [ ] Restore target is isolated from the active application.
- [ ] Link/path scan and Mermaid validation passed.
- [ ] PDF count, FAISS count, and representative search passed in the staged
      restore before promotion.

## Post-Deployment Verification

```bash
curl -fsS https://ai-sahakar.net/livez
curl -fsS https://ai-sahakar.net/readyz
curl -fsS https://www.ai-sahakar.net/livez
curl -fsS https://www.ai-sahakar.net/readyz
curl -fsS https://<configured-domain>/
```

Then verify one authenticated login, one PDF listing with the recorded PDF
count, one FAISS count, one representative search, and one static asset. Do not
treat a root HTTP 200 alone as proof of readiness.

## Dokploy Checkpoints After Deployment

In the Dokploy application, verify the deployment has:

- the intended Git revision and Compose file path;
- `PDFSEARCH_IMAGE` set to the immutable digest;
- container port `8000` and the intended domain/TLS route;
- a healthy Redis dependency and web container;
- the intended `/app/data` volume and read-only `/mnt/legacy` quarantine mount;
- the previous image digest and data backup retained for rollback.
- effective `pull_policy: always` and exact web/Redis image digests;

## Rollback

Rollback is two-dimensional:

1. Application rollback: restore the previous immutable image digest.
2. Data rollback: restore the matching volume snapshot/release.

Do not roll back code across an incompatible database migration without the
matching data procedure. Record the rollback in the release/incident log. Tags
and aliases are not a substitute for recording the immutable rollback digest.
