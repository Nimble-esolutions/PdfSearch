Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: DEPLOYMENT_GUIDE.md
Supersedes: None

# FlowDocs Deployment

## Canonical Deployment

FlowDocs is deployed through Dokploy as a Docker Compose application. The
canonical production entrypoint is `docker-compose.yml`, not a host bind mount
and not a hand-run `docker run` command.

The container image provides immutable application code under `/app/flowdocs`.
The Dokploy-managed `flowdocs_data` volume provides mutable data under
`/app/data`.

The canonical production domains are `ai-sahakar.net` and `www.ai-sahakar.net`.
`2026.ai-sahakar.net` was the preview/verification host and remains a historical
rollback reference. The merged source baseline and current release must be
recorded from Dokploy at cutover. Dokploy must pull exact image digests with
`pull_policy: always`; a tag or stale local `latest` image is not valid release
evidence.

## Development

Use:

```bash
LOCAL_BUILD_REVISION="$(git rev-parse --short HEAD)" \
  docker compose -f docker-compose.dev.yml up -d --build --wait
```

Development uses its own named volumes and must not reference `prod_flowdocs`.
It builds one shared local application image and runs an isolated pinned RustFS
service with a bounded boto3 bucket initializer. Stage/production have the same
web, maintenance, Redis, data/control mount, and critical-environment shape,
but pull one immutable `repository@sha256:digest` and use external RustFS.
Never use `down -v` for retained development data; rollback preserves both the
legacy MinIO volume and the new RustFS volumes.

## Production Principles

- Deploy by immutable image digest.
- Keep secrets in Dokploy UI, never in Git.
- Use `/readyz` for traffic readiness.
- Never mount persistent data over application code.
- Never run production with `DEBUG=True` or wildcard hosts.
- Treat database, media, and vector indexes as one recovery set.
- Treat legacy and active data as divergent custody domains; never copy them
  directly. Use quarantine, inventory, conflict classification, staged restore,
  FAISS fingerprint validation, and explicit promotion.
- Verify restores, not only backup creation.

RustFS bucket `ai-sahakar-prod-flowdocs-data-volume` is an isolated operator
recovery vault containing timestamped active/legacy snapshots and checksums.
Application-level S3 integration is opt-in and explicit; no automatic
cross-environment synchronization exists.

See [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) for the complete Dokploy,
data-custody, backup, restore, and rollback procedures. See
[`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) for incident response.
