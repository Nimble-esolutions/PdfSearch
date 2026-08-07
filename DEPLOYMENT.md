Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: DEPLOYMENT_GUIDE.md
Supersedes: None

# FlowDocs Deployment

## Canonical Deployment

The 2026 FlowDocs application is deployed through Dokploy as a Docker Compose
application. `docker-compose.yml` is the canonical deployment template for the
active stage and the future production application; it is not evidence that
the future 2026 production project exists. Do not replace it with a host bind
mount or hand-run `docker run` command.

The container image provides immutable application code under `/app/flowdocs`.
The Dokploy-managed `flowdocs_data` volume provides mutable data under
`/app/data`.

Legacy `www.ai-sahakar.net` remains the authoritative production service.
`2026.ai-sahakar.net` is the active non-production 2026 stage/rehearsal host.
The future 2026 production project has not been deployed; its intended
`ai-sahakar.net` / `www.ai-sahakar.net` routing requires a separately approved
cutover. Record the merged source baseline and resolved image digest at that
time. Production must pull an exact image digest with `pull_policy: always`;
a tag or stale local `latest` image is not release evidence. Stage may track
the approved compatibility channel, but its running digest must still be
recorded after each deployment.

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
Never use `down -v` for retained development data; rollback preserves existing
development data/control and RustFS volumes.

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

DataOps v3 is the sole supported operator UI and lifecycle contract. It uses
one environment-owned RustFS connection for complete immutable recovery
points, automatically chooses same-dataset restore or foreign-dataset
import/rebind, and prepares isolated restore candidates. It never performs
automatic cross-environment synchronization. VaultOps remains installed only
as internal compatibility/control/activation infrastructure; its legacy APIs
and profile choreography are default-off removal debt, not an alternate
operator workflow.
Use [`docs/HANDOFF.md`](docs/HANDOFF.md) for the currently verified dataset,
bucket, generation, and receipt evidence.

See [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) for the complete Dokploy,
data-custody, backup, restore, and rollback procedures. See
[`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) for incident response.
