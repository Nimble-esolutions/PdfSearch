Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/REDIS_GHCR_SETUP.md
Supersedes: docs/DOCKER_HUB_AUTH.md

# Redis Image Policy

The application Compose file currently names the official `redis:7-alpine`
image. The current production release is Redis-enabled and uses the immutable
image revision from PR #24. The repository also contains a scheduled/manual GHCR
mirroring workflow. These are different supply paths and must not be treated as
interchangeable without an explicit Compose and digest change.

## Production Policy

The current repository Compose contract names Docker Hub `redis:7-alpine` and
does not pin that Redis image itself. For production, Dokploy must resolve and
record an exact Redis `repo@sha256:<digest>`, use `pull_policy: always`, and
verify the running container. The GHCR mirror is not the current production
contract and its moving tags must not be substituted without verification of the
exact image, pull access, and rollback path.

The web service already declares `pull_policy: always` in repository Compose.
The Redis tag and its effective pull policy remain explicit release evidence
until the Compose contract is amended; this documentation-only change does not
amend it.

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
