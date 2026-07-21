# Redis Image Policy

The application Compose file currently uses the official `redis:7-alpine`
image. The repository also contains a mirroring workflow for GHCR. These are
two different supply paths and must not be treated as interchangeable without
verification.

## Production Policy

Choose one of the following and document it in the Dokploy environment:

1. Official Docker Hub image with the approved digest; or
2. GHCR mirror with a verified digest and pull credentials.

Do not use a moving `latest` Redis tag for a release record.

Redis is a cache/queue dependency, not the source of truth. Application data
must remain recoverable from the database and persistent data release.

## Host Requirement

Redis recommends `vm.overcommit_memory=1`. This is a host baseline setting,
not an application Compose setting. Verify it on the server and persist it in
the host configuration management process.

## Verification

```bash
redis-cli ping
docker inspect <redis-container> --format '{{.Config.Image}}'
```

Record the image digest with each production release.
