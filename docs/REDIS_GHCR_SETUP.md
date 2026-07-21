Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/REDIS_GHCR_SETUP.md
Supersedes: docs/DOCKER_HUB_AUTH.md

# Redis Image Policy

The application Compose file currently uses the official `redis:7-alpine`
image. The repository also contains a scheduled/manual GHCR mirroring workflow.
These are two different supply paths and must not be treated as interchangeable
without an explicit Compose and digest change.

## Production Policy

The current Compose contract is the Docker Hub `redis:7-alpine` image. Record
the resolved Redis image digest from the deployed container with each release;
the current Compose file does not pin that image itself. The GHCR mirror is not
the current production contract and its moving tags must not be substituted in
Dokploy without verification of the exact image, pull access, and rollback
path.

Redis is a cache/queue dependency, not the source of truth. Application data
must remain recoverable from the database and persistent data release.

## Host Requirement

Redis recommends `vm.overcommit_memory=1`. This is a host baseline setting,
not an application Compose setting. Verify it on the server and persist it in
the host configuration management process.

## Verification

```bash
docker compose -f docker-compose.yml exec -T redis redis-cli ping
docker inspect <redis-container> --format '{{.Config.Image}}'
docker inspect <redis-container> --format '{{index .RepoDigests 0}}'
```

Record the image digest with each production release.
