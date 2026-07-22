Status: Active
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/OPERATIONS_RUNBOOK.md
Supersedes: None

# FlowDocs Operations Runbook

This runbook is for a Dokploy Compose deployment of FlowDocs. It uses the
repository Compose file, the web service on container port `8000`, the
`flowdocs_data` application data volume, and Redis as a cache/queue dependency.
Run evidence commands before recovery commands. Do not expose environment
values, credentials, document contents, or copied production data in tickets or
logs.

Current canonical production domains are `https://ai-sahakar.net` and
`https://www.ai-sahakar.net`; the verified preview was
`https://2026.ai-sahakar.net` and must not be treated as the main production
URL after cutover. Dokploy production evidence must show exact web/Redis
digests and `pull_policy: always`.

## Safety Rules

- Preserve logs, deployment metadata, image identity, and volume identity before cleanup.
- Stop if the target container, image digest, or `/app/data` volume is not the expected one.
- Do not run `docker compose down -v` in production.
- Do not mount data over `/app/flowdocs`.
- Do not restore over the active volume; use a disposable Dokploy application or uniquely named volume.
- Treat application rollback and data rollback as separate decisions.
- Treat legacy and active data as divergent custody domains. Never copy them
  directly; use quarantine, inventory, conflict classification, staged restore,
  FAISS fingerprint validation, and explicit promotion.
- RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` is an isolated recovery
  vault. The application adapter is opt-in and explicit; automatic
  cross-environment sync is not implemented.
- Use Dokploy for production deployment changes; do not hand-run a replacement `docker run` container.
- Use the GitHub-connected Dokploy application as the deployment authority. Do
  not edit `/etc/dokploy/compose/.../code`, its ignored `.env`, or Dokploy
  Postgres directly as a normal fix.
- Require Git SHA, OCI digest/revision, Compose hash, Dokploy deployment ID, and
  data-generation identity to agree before accepting a release.
- Stop on branch/tag trigger mismatch, stale checkout, mutable image identity,
  generated-file drift, or stale network references.

## Initial Evidence Bundle

Run from a host with access to the Dokploy Compose project:

```bash
date -u
git rev-parse HEAD
sha256sum docker-compose.yml
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml config --images
docker compose -f docker-compose.yml config --volumes
docker inspect "$(docker compose -f docker-compose.yml ps -q web)" \
  --format '{{.Created}} {{.Config.Image}}'
docker inspect "$(docker compose -f docker-compose.yml ps -q web)" \
  --format '{{range .Mounts}}{{println .Name .Source .Destination .RW}}{{end}}'
docker inspect "$(docker compose -f docker-compose.yml ps -q redis)" \
  --format '{{.Created}} {{.Config.Image}}'
```

Expected evidence is the intended Compose application, web and Redis images
identified by `repo@sha256:<digest>` for promotion, effective
`pull_policy: always`, `/app/data` on the expected named volume, `/mnt/legacy`
read-only when present, and no mount at `/app/flowdocs`. Retain the command
output with the incident record after redacting hostnames or other sensitive
operational details as required.

## Incident Card: Unhealthy Containers

### Symptoms

Dokploy reports an unhealthy or restarting web/Redis container, or traffic is
not reaching a healthy replica.

### Read-only evidence

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=200 web
docker compose -f docker-compose.yml logs --tail=200 redis
docker inspect "$(docker compose -f docker-compose.yml ps -q web)" \
  --format '{{json .State.Health}}'
docker inspect "$(docker compose -f docker-compose.yml ps -q redis)" \
  --format '{{json .State.Health}}'
curl -fsS https://<configured-domain>/livez
curl -fsS https://<configured-domain>/readyz
```

### Expected evidence

The web container should be running and its Compose healthcheck should reach
`/readyz`. Redis should report `PONG`. Startup logs should show completed Django
migrations before Gunicorn starts.

### Stop conditions

Stop if logs show a migration error, a non-writable `/app/data`, an unexpected
volume, or a missing required environment value. Do not repeatedly restart a
container that is failing closed.

### Recovery

Preserve logs, confirm the data volume, and use Dokploy to redeploy the intended
digest after correcting the identified configuration or dependency. Restart
only the affected service when the evidence shows a transient dependency issue.

### Rollback

Set `PDFSEARCH_IMAGE` in Dokploy to the recorded previous immutable digest and
redeploy. If the failure follows a schema or index change, use the matching data
release procedure rather than an image-only rollback.

### Retained evidence

Keep the initial evidence bundle, health JSON, relevant startup logs, Dokploy
deployment ID, tested digest, previous digest, and backup reference.

## Incident Card: Wrong Image Digest

### Symptoms

The running web or Redis image does not match the digest recorded for the
deployment, or the effective Compose configuration does not pull exact digests
with `pull_policy: always`.

### Read-only evidence

```bash
WEB="$(docker compose -f docker-compose.yml ps -q web)"
docker inspect "$WEB" --format '{{.Config.Image}}'
docker inspect "$WEB" --format '{{index .RepoDigests 0}}'
docker compose -f docker-compose.yml config --images
```

From a release workstation, resolve any compatibility tags only for comparison;
never use them as release identity:

```bash
docker buildx imagetools inspect ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:dev
docker buildx imagetools inspect ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest
```

### Expected evidence

The deployed web and Redis containers and the release record identify the same
immutable digests. Compatibility tags may be aliases, but they are not evidence
of the deployed release.

### Stop conditions

Stop promotion if either service is tag-only, if `pull_policy` is not always, or
if a digest cannot be matched to CI smoke-test evidence. Do not repoint an alias
or reuse a local cache to make a mismatched deployment appear valid.

### Recovery

Record the mismatched images and use Dokploy to set the web and Redis images to
the tested `repo@sha256:<digest>` values. Redeploy, then repeat `/livez`,
`/readyz`, login, PDF count, FAISS count, search, and static-asset checks.

### Rollback

Use the previous recorded immutable web and Redis digests through Dokploy. Tags
and aliases do not replace rollback identity.

### Retained evidence

Keep the deployed image reference, both alias resolutions, CI summary URL or
identifier, Git SHA, and the post-recovery smoke-test results.

## Incident Card: Wrong Volume

### Symptoms

Documents, users, indexes, or backups are missing, or the web container mounts
an unexpected named volume.

### Read-only evidence

```bash
WEB="$(docker compose -f docker-compose.yml ps -q web)"
docker inspect "$WEB" --format '{{range .Mounts}}{{println .Name .Source .Destination .RW}}{{end}}'
docker volume ls --filter label=com.dokploy.backup=true
docker volume inspect <candidate-volume>
docker compose -f docker-compose.yml config --volumes
```

### Expected evidence

The active data volume is mounted at `/app/data`, the legacy volume is mounted
at `/mnt/legacy` read-only only for controlled quarantine evidence, and no volume shadows
`/app/flowdocs`.

### Stop conditions

Stop immediately if the active volume is unknown, if `/app/data` is empty when
the service should contain data, or if application code is shadowed. Do not
delete, rename, or reformat a candidate volume.

### Recovery

Keep the container stopped through Dokploy, identify the intended volume from
the last release record and backup, and correct the Dokploy Compose application
association. Restore only into a disposable target first and verify its data
before reconnecting the intended volume.

### Rollback

Return to the previous Dokploy application/volume association only after
confirming that its image and data release are compatible. Never use a volume
rollback to conceal an unverified data loss.

### Retained evidence

Keep mount output, volume inspection output, backup reference, last known-good
volume identity, container ID, and all recovery decisions.

## Incident Card: Migration Failure

### Symptoms

Startup logs show a failed Django or explicit JSON migration, the web container
exits, or `/readyz` returns `503` with migrations not `ok`.

### Read-only evidence

```bash
docker compose -f docker-compose.yml logs --tail=300 web
curl -i https://<configured-domain>/readyz
docker compose -f docker-compose.yml ps
```

### Expected evidence

The startup script creates a local SQLite safety snapshot when a database exists,
runs optional JSON migrations only when explicitly enabled, then runs Django
migrations. A migration error stops startup; traffic must not be served by that
container.

### Stop conditions

Stop if the error is not understood, the backup is missing, the database is
non-writable, or the proposed image changes schema/index compatibility. Do not
delete migration rows or apply ad hoc SQL in the production database.

### Recovery

Preserve the failed container logs and database backup. Reproduce against a
disposable copy, correct the migration in a reviewed release, and redeploy via
Dokploy. Re-run integrity, migration, readiness, and representative search
checks before restoring traffic.

### Rollback

Use the previous image only when its schema is compatible. Otherwise restore the
matching database/data release in isolation and promote it with the matching
image.

### Retained evidence

Keep migration logs, migration state output, database integrity result, failed
and previous image digests, backup reference, and the reviewed recovery change.

## Incident Card: Stale Dokploy Deployment

### Symptoms

The Dokploy UI shows a new deployment but the public route serves old behavior,
old image metadata, or an old Compose configuration.

### Read-only evidence

```bash
docker compose -f docker-compose.yml config --images
docker compose -f docker-compose.yml ps
WEB="$(docker compose -f docker-compose.yml ps -q web)"
docker inspect "$WEB" --format '{{.Created}} {{.Config.Image}}'
curl -fsS https://<configured-domain>/livez
curl -fsS https://<configured-domain>/readyz
```

Also compare the Dokploy deployment record, Git revision, Compose path, domain,
and container port `8000` in the UI. Do not export environment values for this
comparison.

### Expected evidence

The running container creation time and immutable image match the successful
Dokploy deployment, and the route reaches the web service on port `8000`.

### Stop conditions

Stop if the route maps to a different application, the port is not `8000`, or
the UI and container evidence disagree. Do not manually edit a running
container or deploy the static landing-page configuration.

### Recovery

Use Dokploy to redeploy the intended Git revision and exact image digest. Confirm
the deployment reaches healthy state and run the full post-deployment smoke set.

### Rollback

Use the previous Dokploy deployment or previous immutable digest, with its
matching data release when required.

### Retained evidence

Keep Dokploy deployment IDs, Git revisions, container creation time, image
references, route/port confirmation, and smoke-test output.

## Incident Card: Redis Failure

### Symptoms

Redis healthchecks fail, `/readyz` reports a cache error, or application cache
operations fail while the database remains available.

### Read-only evidence

```bash
docker compose -f docker-compose.yml ps redis
docker compose -f docker-compose.yml logs --tail=200 redis
docker compose -f docker-compose.yml exec -T redis redis-cli ping
docker inspect "$(docker compose -f docker-compose.yml ps -q redis)" \
  --format '{{.Config.Image}} {{index .RepoDigests 0}}'
curl -i https://<configured-domain>/readyz
```

### Expected evidence

Redis responds `PONG`, the deployed image reference is recorded, and `/readyz`
reports `cache: ok` when `REDIS_URL` is configured. Redis is not the source of
truth for the database or document/index recovery set.

### Stop conditions

Stop if Redis data appears corrupted, the image is unexpected, or a proposed
action would flush data. Never run `FLUSHALL` as a diagnostic.

### Recovery

Use Dokploy to restart or redeploy the Redis service after preserving logs and
volume identity. Confirm Redis health, then confirm web readiness. Investigate
the configured `REDIS_URL` without printing its value.

### Rollback

Return to the previously recorded Redis image reference or service deployment.
Restore Redis data only when the incident evidence shows it is necessary; do
not substitute Redis restoration for database/media/index restoration.

### Retained evidence

Keep Redis health output, image digest, logs, volume identity, readiness output,
and any restart/redeploy timestamps.

## Incident Card: Readiness Failure

### Symptoms

`/livez` responds but `/readyz` returns `503`, or the container healthcheck is
unhealthy while the process is running.

### Read-only evidence

```bash
curl -i https://<configured-domain>/livez
curl -i https://<configured-domain>/readyz
docker compose -f docker-compose.yml logs --tail=200 web
docker compose -f docker-compose.yml ps
```

### Expected evidence

`/livez` returns `{"status":"ok"}`. `/readyz` returns `{"status":"ready"}`
only when database and migrations are healthy and cache is either healthy or
not configured. The endpoint does not validate media, FAISS, or Chroma content.

### Stop conditions

Stop if readiness is being bypassed by routing traffic to `/` or `/livez`, or if
required data/index checks have not been completed after a restore or migration.

### Recovery

Resolve the failing database, cache, or migration condition using the matching
incident card. After `/readyz` succeeds, perform the separate operator data and
search verification before declaring the release ready.

### Rollback

Rollback the image and, when compatibility requires it, the matching data
release. Do not mark a release ready solely because the root page returns `200`.

### Retained evidence

Keep both endpoint responses, container health state, relevant logs, data/index
verification results, and the final release decision.

## Incident Card: Failed Restore

### Symptoms

A backup cannot be unpacked, SQLite integrity fails, indexes cannot load, or a
representative search fails in the restore target.

### Read-only evidence

```bash
docker volume inspect <isolated-restore-volume>
docker run --rm -v <isolated-restore-volume>:/data:ro \
  <matching-flowdocs-image@sha256:digest> \
  python -c 'import sqlite3; c=sqlite3.connect("/data/db.sqlite3"); print(c.execute("PRAGMA integrity_check").fetchone()[0])'
docker compose -p <restore-project> -f docker-compose.yml ps
docker compose -p <restore-project> -f docker-compose.yml logs --tail=300 web
```

The last two commands apply only when a disposable Compose/Dokploy restore
project was created. Do not point them at the active project.

### Expected evidence

The restored volume is uniquely named and isolated, SQLite reports `ok`, media
and PDF counts are plausible, indexes load, search works, and the matching
image reaches `/readyz`.

### Stop conditions

Stop if the target volume is active, the image digest is unknown, integrity
fails, or the restore process requests credentials or document contents in a
shared record. Do not retry extraction over the active volume.

### Recovery

Retain the failed isolated target for analysis, obtain the next known-good
backup, and repeat in another isolated volume. Verify the backup archive and
matching release record before any promotion.

### Rollback

Discard only the disposable failed target after evidence retention. The active
application remains on its known-good image and volume until a restore drill
passes and a separately approved promotion is completed.

### Retained evidence

Keep backup reference, isolated volume name, image digest, integrity output,
restore logs, media/index/search checks, and the decision not to promote.

## Incident Card: Legacy/Active Data Reconciliation

### Verified baseline

Legacy has 242 PDFs and 45 FAISS files. Active has 17 PDF rows, 0 PDFs, and 11
FAISS files. Only 6 PDF paths overlap, and the SQLite databases diverge. RustFS
bucket `ai-sahakar-prod-flowdocs-data-volume` holds timestamped active/legacy
snapshots and checksums but is isolated from the application network.

### Recovery

1. Quarantine both sources in separate read-only targets.
2. Inventory rows, paths, index files, and checksums without copying contents.
3. Classify conflicts and missing references.
4. Stage an explicit restore in a uniquely named disposable target.
5. Validate SQLite, PDF count, FAISS fingerprints, index loading, and search.
6. Promote only after an operator records the selected source and decision.

Direct legacy-to-active copying and silent merging are prohibited. Application
S3 integration, automatic cross-environment synchronization, generated artifact
manifests, and FAISS recovery automation are not current capabilities.

### Required gates

Pass link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
PDF count, FAISS count, and representative search before promotion. Retain
failed isolated targets and all evidence until the incident is closed.
