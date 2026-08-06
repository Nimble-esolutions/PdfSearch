Status: Active
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical source: docs/OPERATIONS_RUNBOOK.md
Supersedes: None

# FlowDocs Operations Runbook

## Current 2026 stage note

The 2026 recovery rehearsal has a signed active 242-document runtime, indexing
ratio `1.0`, a manual stage backup receipt, and an isolated restore rehearsal.
The stage route is https://2026.ai-sahakar.net; a healthy root response still
does not replace `/readyz` evidence. A future failed candidate or backup must
not be worked around by enabling startup restore or copying quarantine data
over the active volume.

The stage Compose project name is
sahakar-ai-sahakar-frontend-2026-prod-ruhj6z. Operate through that preserved
Dokploy project; do not invent a Compose project-name variable or run the
deployment from a timestamped checkout that could allocate blank volumes. See
[`HANDOFF.md`](HANDOFF.md) for current evidence and
`STATUS-2026-08-02.md` for dated migration evidence.

This runbook is for a Dokploy Compose deployment of FlowDocs. It uses the
repository Compose file, the web service on container port `8000`, the
`flowdocs_data` application data volume, and Redis as a cache/queue dependency.
Run evidence commands before recovery commands. Do not expose environment
values, credentials, document contents, or copied production data in tickets or
logs.

Legacy production remains `https://www.ai-sahakar.net` and is authoritative.
`https://2026.ai-sahakar.net` is the current non-production stage. Future 2026
production traffic changes require separate approval; Dokploy production
evidence must show exact application/infrastructure digests and `pull_policy:
always`.

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
  vault. The application adapter is opt-in and explicit: superadmins can queue
  an immutable sync or checksum-verified pull into staging. Automatic
  cross-environment sync and live promotion remain disabled.
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

## New Evidence Commands (2026-07-24)

### Environment Identity

```bash
docker compose -f docker-compose.yml exec -T web python manage.py config_inspect
```

Reports `APP_ENV`, `DATA_MODE`, `BACKUP_ROLE`, `EXTERNAL_SIDE_EFFECTS_MODE`,
`PRODUCTION_SOURCE_ID`, `AUTHORITATIVE_DATASET_ID`, `DATASET_ID`, and instance
identity. Use this to confirm the running container's environment posture
before any deployment or recovery action.

### Object Store Capabilities

```bash
docker compose -f docker-compose.yml exec -T web python manage.py verify_object_store_capabilities
```

Probes the configured S3/RustFS endpoint for conditional operation support
(If-None-Match, If-Match). Fails if the object store does not support the
required operations for global writer fencing.

### Health Endpoints

```bash
curl -fsS https://<configured-domain>/health/data/
curl -fsS https://<configured-domain>/health/lease/
curl -fsS https://<configured-domain>/health/metrics/
```

- `/health/data/` — SQLite integrity, media count, FAISS index count, Chroma
  collection count.
- `/health/lease/` — current writer lease owner, TTL, renewal status.
- `/health/metrics/` — Prometheus-format metrics including request counts,
  search latency, index sizes, and lease state.

### Management Commands

```bash
docker compose -f docker-compose.yml exec -T web python manage.py inventory_artifacts
docker compose -f docker-compose.yml exec -T web python manage.py validate_data_release
```

- `inventory_artifacts` — lists all generations, their validation status, and
  retention state.
- `validate_data_release` — runs the full compatibility check suite (schema,
  embedding dimensions, FAISS format) against the current data root.

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

## Incident Card: Environment Identity Failure

### Symptoms

Startup fails with an environment identity validation error, the web container
exits before migrations run, or `/readyz` never becomes reachable.

### Read-only evidence

```bash
docker compose -f docker-compose.yml logs --tail=100 web
docker compose -f docker-compose.yml exec -T web python manage.py config_inspect
```

### Expected evidence

`config_inspect` reports valid `APP_ENV`, `DATA_MODE`, `BACKUP_ROLE`, and
`EXTERNAL_SIDE_EFFECTS_MODE`. Production requires `APP_ENV=production`,
`DATA_MODE=local` for the current volume-backed deployment, and a non-empty
`PRODUCTION_SOURCE_ID`.

### Stop conditions

Stop if any required identity variable is missing, invalid, or mismatched for
the deployment environment. Do not bypass the validation by setting
`ALLOW_INSECURE_DEFAULTS=1` in production.

### Recovery

Correct the environment variables in Dokploy and redeploy. The container will
pass startup validation and proceed to migrations.

### Retained evidence

Keep `config_inspect` output, startup logs, and the corrected Dokploy
environment configuration (keys only, no values).

## Incident Card: Side-Effect Policy Violation

### Symptoms

Email notifications are not sent, OpenAI calls fail, payment webhooks are
not received, or the application logs show side-effect policy blocks.

### Read-only evidence

```bash
docker compose -f docker-compose.yml exec -T web python manage.py config_inspect
docker compose -f docker-compose.yml logs --tail=200 web | grep -i "side_effect"
```

### Expected evidence

Production must show `EXTERNAL_SIDE_EFFECTS_MODE=enabled`. Staging, review,
development, and test may use `sandbox` or `disabled` according to the test.

### Stop conditions

Stop if production is running in `sandbox` or `disabled` mode — external
integrations are silently blocked. Do not change the mode without a reviewed
deployment record.

### Recovery

Set `EXTERNAL_SIDE_EFFECTS_MODE=enabled` in Dokploy and redeploy. Verify email,
OpenAI, and webhook functionality after the change.

### Retained evidence

Keep `config_inspect` output, side-effect policy logs, and the deployment
record for the mode change.

## Incident Card: Global Writer Conflict

### Symptoms

Write operations fail with a conflict error, data changes are rejected, or
the application logs show "writer conflict" or "lease held by another instance."

### Read-only evidence

```bash
curl -fsS https://<configured-domain>/health/lease/
docker compose -f docker-compose.yml logs --tail=200 web | grep -i "writer\|lease\|conflict"
```

### Expected evidence

The health endpoint shows the current lease owner. Only one instance should
hold the writer lease. The lease TTL should be within the configured window.

### Stop conditions

Stop if two instances claim the writer lease, if the lease is held by a
stale/dead instance, or if the lease TTL is expired without renewal. Do not
force-release a lease without confirming the holder is dead.

### Recovery

Identify the conflicting instance. If it is dead, wait for lease expiry or
use the management command to release it. If it is alive, investigate why
two instances are writing to the same dataset.

### Retained evidence

Keep `/health/lease/` output, writer conflict logs, instance identities of
both holders, and the resolution decision.

## Incident Card: Dataset Registration Failure

### Symptoms

The application rejects writes with "dataset not registered," or startup logs
show a registration failure.

### Read-only evidence

```bash
docker compose -f docker-compose.yml exec -T web python manage.py config_inspect
docker compose -f docker-compose.yml logs --tail=100 web | grep -i "registration\|dataset"
```

### Expected evidence

`DATASET_ID` and `AUTHORITATIVE_DATASET_ID` are set and the dataset is
registered in the object store. The instance identity matches the registered
writer.

### Stop conditions

Stop if the dataset is not registered, if `DATASET_ID` conflicts with another
instance, or if the registration was created with a different instance identity.

### Recovery

Register the dataset using the management command or correct the
`AUTHORITATIVE_DATASET_ID` / `DATASET_ID` in Dokploy. If the dataset already
exists with a different instance, resolve the conflict before re-registering.

### Retained evidence

Keep `config_inspect` output, registration logs, and the resolution.

## Incident Card: Restore Pipeline Failure

### Symptoms

A restore job fails at any stage (download, validate, sanitize, rehearse,
activate), or the workspace state machine is stuck in an intermediate state.

### Read-only evidence

```bash
docker compose -f docker-compose.yml logs --tail=300 web | grep -i "restore\|workspace"
docker compose -f docker-compose.yml exec -T web python manage.py inventory_artifacts
ls -la /app/data/restore_workspaces/
```

### Expected evidence

The restore workspace shows a clean state machine progression:
`pending` → `downloading` → `validating` → `sanitizing` → `rehearsing` →
`activating` → `completed`. Each stage produces a checkpoint.

### Stop conditions

Stop if the workspace is stuck in an intermediate state, if validation or
rehearsal failed, or if the target generation is incompatible with the
current schema/embeddings/FAISS format. Do not manually advance the state
machine.

### Recovery

Inspect the failed stage logs. For download failures, verify object store
connectivity and credentials. For validation failures, check checksums. For
rehearsal failures, review compatibility report. Discard the failed workspace
and retry with a corrected configuration.

### Retained evidence

Keep restore logs, workspace state, compatibility report, and the failed
generation reference.

## Incident Card: Activation Failure

### Symptoms

A generation activation fails, the symlink swap is incomplete, or the
application serves stale data after a reported activation.

### Read-only evidence

```bash
docker compose -f docker-compose.yml logs --tail=200 web | grep -i "activate\|activation"
ls -la /app/data/current /app/data/generations/
docker compose -f docker-compose.yml exec -T web python manage.py inventory_artifacts
```

### Expected evidence

`/app/data/current` is a symlink to the active generation directory. The
activation journal shows a completed heartbeat sequence. The active generation
matches the expected one from `inventory_artifacts`.

### Stop conditions

Stop if the symlink points to a non-existent target, if the activation journal
shows an incomplete heartbeat sequence, or if the active generation does not
match the expected one. Do not manually fix the symlink.

### Recovery

If the activation journal shows a crash during activation, the crash recovery
will roll back to the last known-good state on next startup. If activation
failed cleanly (validation or rehearsal), fix the underlying issue and retry.
If the symlink is broken, run the activation recovery management command.

### Retained evidence

Keep activation logs, journal state, symlink target, `inventory_artifacts`
output, and the recovery action.

## Incident Card: Activation Crash Recovery

### Symptoms

After an unexpected restart, the application fails to start or serves data
from an unexpected generation. The activation journal shows a partial
heartbeat sequence.

### Read-only evidence

```bash
docker compose -f docker-compose.yml logs --tail=200 web | grep -i "crash\|recovery\|journal"
cat /app/data/.activation_journal
ls -la /app/data/current /app/data/generations/
```

### Expected evidence

The activation journal shows either a completed heartbeat sequence (clean
activation) or a rollback marker (crash recovered). The symlink points to a
valid generation directory.

### Stop conditions

Stop if the journal is corrupted, if the rollback target is also invalid, or
if manual intervention has modified the symlink or journal. Do not delete the
journal file.

### Recovery

The crash recovery runs automatically on startup. If it fails, inspect the
journal for the last completed heartbeat and manually verify that generation.
If both the attempted and rollback generations are corrupt, restore from the
last known-good backup.

### Retained evidence

Keep the activation journal, startup logs, symlink state, and any manual
recovery steps.

## Incident Card: Object Store Capability Failure

### Symptoms

Object store operations fail with "operation not supported," conditional
writes are rejected, or `verify_object_store_capabilities` reports missing
features.

### Read-only evidence

```bash
docker compose -f docker-compose.yml exec -T web python manage.py verify_object_store_capabilities
docker compose -f docker-compose.yml logs --tail=100 web | grep -i "object_store\|s3\|rustfs"
```

### Expected evidence

The capabilities probe reports support for `PutIfNoneMatch`, `PutIfMatch`,
`GetIfNoneMatch`, and `GetIfMatch`. All conditional operations are available.

### Stop conditions

Stop if any conditional operation is not supported — global writer fencing
and CAS-based operations will not work correctly. Do not bypass the capability
check.

### Recovery

Verify the S3/RustFS endpoint supports conditional operations. If using
RustFS, ensure the version supports If-None-Match and If-Match headers. If
the endpoint cannot be upgraded, disable artifact vault features until the
endpoint is compatible.

### Retained evidence

Keep `verify_object_store_capabilities` output, object store logs, and the
endpoint version/configuration.

### Verified baseline

Post-reconciliation (2026-07-24): 253 PDF rows, 242 recovered PDF files, 53
folders, 8 users, 51 FAISS indexes, 8,753 vectors. Eleven target-only PDF rows
remain preserved but unrecovered. RustFS bucket
`ai-sahakar-prod-flowdocs-data-volume` holds timestamped active/legacy
snapshots and checksums but is isolated from the application network.

### Recovery

1. Quarantine both sources in separate read-only targets.
2. Inventory rows, paths, index files, and checksums without copying contents.
3. Classify conflicts and missing references.
4. Stage an explicit restore in a uniquely named disposable target.
5. Validate SQLite, PDF count, FAISS fingerprints, index loading, and search.
6. Promote only after an operator records the selected source and decision.

Direct legacy-to-active copying and silent merging are prohibited. The application
now supports namespace-scoped S3 keys, conditional operations, immutable
generation manifests, CAS-based writer fencing, staged restore with rehearsal,
and signed JSON runtime-pointer activation with rollback evidence in staging.

The active Workbench path now publishes a dataset-scoped generation, verifies
authoritative inventory, prepares an isolated restore workspace, and schedules
runtime activation only through a separately confirmed signed intent. Both
entrypoints still fail closed before database mutation instead of performing an
automatic startup restore.

Do not use an admin success message, Vault promotion, or a generation database
row as proof of restored runtime bytes. Require the signed activation result,
matching runtime pointer, fresh-process readiness evidence, database/media/index
checks, and representative English and Marathi searches.

### Required gates

Pass link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
PDF count, FAISS count, and representative search before promotion. Retain
failed isolated targets and all evidence until the incident is closed.

## Backup Policy Operations

Backups are governed by `core/backup_policy.py` with dirty-state tracking,
fingerprinting, and debouncing.

Scheduled publication uses durable mutation epochs and coalescing, but it is
not a disaster-recovery guarantee. Confirm that the production maintenance
service receives the complete scheduler, sync, Vault, restore, and immutable
release-identity environment, and verify every published generation.

### Check backup state

```bash
docker compose -f docker-compose.yml exec -T web python manage.py config_inspect
curl -fsS https://<configured-domain>/health/data/
```

The data health endpoint reports the current dirty-state/fingerprint view. A
clean result is not proof of a recent backup while mutation-to-dirty wiring is
incomplete.

### Trigger a manual backup

```bash
docker compose -f docker-compose.yml exec -T web python manage.py inventory_artifacts
```

Use the artifact inventory to identify the current data set, then queue an
explicit vault generation sync from the approved operator path. Confirm the
maintenance process has the complete vault and writer environment. The
inventory command alone does not upload a generation.

### Verify backup integrity

After a backup completes, verify:
- SQLite integrity (`PRAGMA integrity_check` returns `ok`)
- Media file count matches the source
- FAISS index count matches the source
- Backup fingerprint matches the source fingerprint

## Writer Lease Operations

Writer leases are managed by `core/lease.py` with Redis primary and SQLite
fallback.

### Check lease state

```bash
curl -fsS https://<configured-domain>/health/lease/
```

Reports the current lease owner, TTL, renewal status, and backend (Redis or
SQLite).

### Release a stale lease

If a lease is held by a dead instance, wait for TTL expiry (automatic release)
or use the management command to force-release. Never force-release a lease
without confirming the holder is dead — check the instance identity and
container state first.

### Lease renewal

Leases auto-renew as long as the holder is alive. If renewal fails (Redis
unavailable), the lease falls back to SQLite. Monitor `/health/lease/` for
renewal failures — they indicate Redis connectivity issues.

## Restore Pipeline Operations

The restore pipeline (`core/restore_pipeline.py`) runs a strict sequence:
download → validate → sanitize → rehearse → activate.

### Current operator boundary

The Workbench restore job now verifies the dataset-scoped generation, downloads
and validates it, applies required sanitization, rehearses migrations, and
prepares an activation-ready runtime. Vault promotion still changes only remote
authority. Runtime bytes change only after a separate signed activation and
fresh-process readiness proof.

For production certification, never exercise this path inside the live data
volume. Use [`RECOVERY_CERTIFICATION.md`](RECOVERY_CERTIFICATION.md) and the
paired disposable data/control volumes created by the checked-in wrapper.

Current DataOps restore candidates are created under:

```text
/app/data/restore-quarantine/
```

Monitor the process that actually invoked the pipeline and inspect workspace
metadata read-only. Preserve failed workspaces through incident review. Never
delete a workspace referenced by an incident, activation journal, or pending
job.

## Activation Operations

Generation activation uses signed intent/result records and compare-and-swap
JSON runtime pointers under `/app/data-control`; the runtime supervisor verifies
the exact generation and manifest before accepting readiness or rolling back.

### Verify active generation

```bash
docker compose -f docker-compose.yml exec -T web \
  python manage.py shell -c \
  'from core.activation_journal import activation_status; print(activation_status())'
```

Also inventory the active database/media/FAISS paths and run a representative
search. A local `ArtifactGeneration` row or dashboard badge is not activation
proof.

### Check activation journal

Use `activation_status()` rather than assuming a journal path. A complete
sequence and matching active pointer are required. An incomplete activation is
reconciled by startup; preserve its logs and metadata.

### Manual activation recovery

If automatic crash recovery fails, stop and inspect the journal/pointer through
the activation service helpers. Never manually modify the symlink or journal.

## Management Commands Reference

| Command | Purpose |
|---------|---------|
| `config_inspect` | Report environment identity, data mode, side-effect policy |
| `verify_object_store_capabilities` | Probe S3/RustFS for conditional operation support |
| `inventory_artifacts` | Build a content-free inventory of the current data root |
| `validate_data_release` | Run full compatibility check suite |
