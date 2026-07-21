## Change Type

- [ ] Application code
- [ ] Docker/Compose
- [ ] Persistent data or migration
- [ ] Dokploy/Traefik
- [ ] Documentation/rules
- [ ] Security

## Required Checks

- [ ] Based on latest `dev` or explicitly documented stacked branch
- [ ] No secrets or production `.env` values added
- [ ] `docker compose config` passes
- [ ] `docker build --check` passes
- [ ] Django checks and migration checks pass
- [ ] Health/readiness behavior tested
- [ ] Persistent data impact documented
- [ ] Backup and rollback plan documented
- [ ] Dokploy port/network/volume behavior verified

## Data Safety

- [ ] No named volume is mounted to a file path
- [ ] Application code is not shadowed by a data volume
- [ ] Existing data is preserved
- [ ] Restore path has been tested or explicitly marked pending

## Release Evidence

- Git SHA:
- Image digest:
- Data release/backup:
- Validation commands:
- Rollback procedure:
