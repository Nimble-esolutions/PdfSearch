Status: Active
Audience: Operators, release managers, and developers changing Dokploy settings
Owner: FlowDocs maintainers
Last verified: 2026-07-26
Canonical source: This document
Supersedes: None

# Dokploy Deployments and Data Persistence

## Short answer

For the current Compose application, pressing **Deploy** or allowing an
autodeploy normally replaces the application container and pulls the selected
image. It does not normally delete the named application volume. The database,
uploaded media, search indexes, static collection, and local backups under
`/app/data` therefore survive a normal image redeploy.

This is a deployment convention, not a safety guarantee. Data survives only if
Dokploy keeps the same Compose project and the same volume mapping. A project
deletion, `docker compose down -v`, changed Compose project name, changed
volume declaration, manual volume deletion, or an accidental bind mount can
start the application with a new or empty data volume.

The release image contains code. The named volume contains mutable data. They
must be versioned, inspected, backed up, and rolled back separately.

The RustFS artifact vault is a third custody layer. It stores only explicit,
immutable publications; it is not a live mirror of the volume and is not pulled
automatically when Dokploy creates an empty volume. Read
[`RUSTFS_RECOVERY_VAULT.md`](RUSTFS_RECOVERY_VAULT.md) before relying on it for
recovery.

## What a normal action does

| Action | Application container | Named `/app/data` volume | Redis data | Expected result |
|---|---|---|---|---|
| Dokploy Deploy with same Compose project | Recreated or replaced | Retained | Usually retained | New code, existing application data |
| Autodeploy after a `dev` release | Recreated or replaced | Retained | Usually retained | New release, existing application data |
| `docker compose up -d` with same project | Reconciled/recreated as needed | Retained | Retained | Safe only after image and mount checks |
| `docker compose down` | Removed | Retained by default | Retained by default | Services stopped; data remains |
| `docker compose down -v` | Removed | Deleted | Deleted | Destructive; never use on production/stage data |
| Delete/recreate Dokploy application | Removed | May become orphaned or be replaced | May be replaced | Treat as destructive until volume ownership is proven |
| Change project name or volume key | Recreated | New volume may be allocated | New volume may be allocated | Application can appear empty |
| Replace `/app/data` with a host bind path | Recreated | Depends on host path | Depends on host path | Unsafe unless explicitly reviewed and backed up |

“Retained” means Docker keeps the volume object. It does not prove that the
application is using the intended volume, that the data is internally valid, or
that a newer image is serving traffic.

## Volume and vault decision matrix

| Observed state after deploy | Correct interpretation | Safe next action |
| --- | --- | --- |
| Same volume identity and expected counts | Existing accumulated data survived | Continue post-deploy checks; do not restore |
| Same volume identity but unexpected counts/integrity | Volume survived but data state is suspect | Stop traffic/promotion, preserve evidence, investigate in isolation |
| New or empty volume, no selected generation | The deploy did not recover application data | Stop; do not accept an empty service as success |
| New or empty volume, validated vault generation exists | Recovery may be possible, but is not automatic | Restore into a disposable target using the full pipeline and acceptance gate |
| Healthy local volume and newer/different vault generation | Two custody states exist | Never auto-merge or overwrite; inventory and reconcile explicitly |
| Volume and RustFS share one failed host | Both recovery layers may be unavailable | Recover from an independent off-host copy |

The existing named volume remains authoritative during a normal redeploy.
Neither `DATA_MODE=s3-restore` nor `RESTORE_POLICY=startup-latest` causes an
entrypoint restore. A `startup-*` policy now prevents both entrypoints from
creating or migrating an absent/zero-byte database and exits with
`startup_restore_required_but_unavailable`; an existing non-empty database is
preserved. Admin generation status also does not prove that the active
database/media/index files changed.

## Current stage contract

The stage preview project is:

```text
sahakar-ai-sahakar-frontend-2026-prod-ruhj6z
```

Its web service must have these mounts:

```text
/app/data     -> the project-owned flowdocs data volume, read-write
/mnt/legacy   -> prod_flowdocs, read-only quarantine/evidence volume
```

The active data volume contains `db.sqlite3`, `media/`, `faiss_indexes/`,
`chroma_db/`, `staticfiles/`, `backups/`, and `.instance_id`. The external
`prod_flowdocs` volume is not the active database and must not be imported by
changing a mount or copying files directly.

During the 2026-07-25 audit, the stage `/app/data` volume was present and
contained the database, media, indexes, and approximately 65 GB of local
backups. This is evidence that redeploys have retained data, but it is also a
disk-capacity risk. Check backup growth and retention before the next release;
do not delete backups during an incident without an approved retention
decision.

## Required pre-deploy evidence

Before pressing Deploy, enabling an autodeploy, changing a Compose file, or
changing environment values:

1. Record the Git SHA, immutable web image digest, previous image digest,
   Compose hash, Dokploy deployment ID, and data-generation identity.
2. Confirm the intended `PDFSEARCH_IMAGE` is a
   `repo@sha256:<digest>`, not only `:latest`, `:dev`, or another mutable tag.
3. Discover the actual volume from the running web container; do not infer its
   host name from the logical Compose key.
4. Confirm `/app/data` is read-write, `/mnt/legacy` is read-only, and no volume
   is mounted over `/app/flowdocs`.
5. Complete a verified application-data backup and record its location,
   checksum, and retention expiry.
6. Record the latest successfully published vault generation and its age. If no
   generation exists, record that the vault recovery point is unavailable.
7. Check disk headroom, memory headroom, and backup growth.
8. Confirm the previous image and matching data release are available for
   rollback.

Read-only inspection:

```bash
docker compose -f docker-compose.yml ps
docker inspect "$(docker compose -f docker-compose.yml ps -q web)" \
  --format '{{range .Mounts}}{{println .Name .Source .Destination .RW}}{{end}}'
docker volume ls --filter label=com.dokploy.backup=true
docker volume inspect <discovered-flowdocs-data-volume>
docker system df
df -h /
```

## Required post-deploy evidence

Do not accept a green Dokploy deployment or a root-page `200` as sufficient.
Verify all of the following:

```bash
docker inspect <web-container> \
  --format 'image={{.Config.Image}} id={{.Image}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
curl -fsS https://<configured-domain>/livez
curl -fsS https://<configured-domain>/readyz
curl -fsS https://<configured-domain>/health/data/
```

Then verify one authenticated login, one PDF listing, one representative
search, one source-document link, and one static asset. Recheck the `/app/data`
volume identity and the database/media/index counts against the pre-deploy
record.

If the image revision changed but the public page did not, inspect Traefik host
ownership and browser/static caching. If the container revision did not change,
inspect the release publication, Dokploy image setting, and pull policy before
restarting again.

## Recovery rules

- Never run `docker compose down -v` against a live stage or production project.
- Never delete a volume to “clear” an application without a verified backup,
  isolated restore, and explicit operator approval.
- Never restore over the active volume. Restore into a uniquely named volume or
  disposable Dokploy application first.
- Treat application rollback and data rollback as separate decisions. A newer
  image may require migrations or index compatibility with a particular data
  release.
- Keep the previous immutable image digest and the matching data snapshot until
  post-deploy acceptance is complete.
- A local SQLite backup inside the same Docker volume is not an off-host backup.
  Maintain an independent recovery copy and perform periodic restore drills.
- A successful vault health probe is not a successful backup. Require an
  immutable generation, manifest/checksum evidence, and a tested restore.
- Do not use the current admin “promote” or “rollback” status as byte-level
  activation evidence until Plan 003 connects those actions to the full restore
  pipeline.

## Options and recommendation

| Option | Benefit | Cost/risk | Recommendation |
|---|---|---|---|
| A. Same named volume + immutable image + pre/post checks | Small change, preserves current architecture, quick rollback | Still depends on one host and operator discipline | Adopt immediately |
| B. Automated off-host snapshots + restore drills | Recovers from host, volume, and operator mistakes | Storage, retention, and restore testing required | Adopt immediately after A |
| C. PostgreSQL for database, object storage for media/index artifacts | Separates critical data from container lifecycle and scales better | Migration, compatibility, credentials, and operational complexity | Plan as the durable production architecture |
| D. Rebuild the Dokploy project on every release | Clean-looking deployments | High risk of orphaned/new volumes and data loss | Reject |
| E. Continue deploying mutable `latest` only | Simple UI workflow | Cannot prove release identity; stale image risk | Reject |

Recommended sequence: keep the current named volume, pin every deployment to
an immutable digest, add a pre-deploy volume/data check, maintain off-host
snapshots, and schedule a restore drill. Move SQLite/media/index custody to
managed PostgreSQL plus object storage only through a separately tested data
release and migration plan.

## Autodeploy policy

Autodeploy may be enabled only when all of these are true:

- the webhook is tied to the approved release branch;
- the workflow has published the exact image digest;
- Dokploy consumes that digest rather than only a mutable alias;
- the Compose project and volume names are locked;
- pre/post deployment checks are recorded;
- rollback image and data snapshot are available.

Autodeploy is a trigger, not a backup system and not proof that the latest
image was published. A failed release workflow can leave `latest` unchanged;
Dokploy may then successfully redeploy the old image.

## Operator decision rule

If a deployment screen says “successful” but any of Git SHA, OCI revision,
image digest, Compose hash, volume identity, or data counts do not match the
release record, stop. Do not repeatedly redeploy. Preserve the evidence,
identify the mismatched layer, and either correct that layer or roll back to the
last known-good image/data pair.
