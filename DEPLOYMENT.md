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

## Development

Use:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Development uses its own named volumes and must not reference `prod_flowdocs`.

## Production Principles

- Deploy by immutable image digest.
- Keep secrets in Dokploy UI, never in Git.
- Use `/readyz` for traffic readiness.
- Never mount persistent data over application code.
- Never run production with `DEBUG=True` or wildcard hosts.
- Treat database, media, and vector indexes as one recovery set.
- Verify restores, not only backup creation.

See [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) for the complete Dokploy,
migration, backup, restore, and rollback procedures. See
[`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) for incident response.
