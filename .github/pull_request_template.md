## Change Type

- [ ] Application code
- [ ] Docker/Compose
- [ ] Persistent data or migration
- [ ] Dokploy/Traefik
- [ ] Documentation/rules
- [ ] Agent rules/skills/adapters
- [ ] Security

## PR Validation

- [ ] Based on latest `dev` or explicitly documented stacked branch
- [ ] `PR contract` passed (bounded feedback only; not full certification)
- [ ] Approved PR entered the `dev` merge queue; no direct-merge bypass used
- [ ] Exact merge-group SHA passed `Pre-merge certification`
- [ ] No secrets or production `.env` values added
- [ ] `docker compose config` passes
- [ ] `docker build --check` passes
- [ ] Django checks and migration checks pass
- [ ] Health/readiness behavior tested
- [ ] Admin UI smoke tested when UI/admin workflows changed
- [ ] Human title, meaning, consequence, and next action are authored for new UI reasons
- [ ] Machine codes appear only in collapsed, redacted technical evidence
- [ ] English and Marathi copy have equivalent meaning and action
- [ ] Keyboard, screen-reader, 200% zoom, and visible-token checks pass
- [ ] English/Marathi desktop and mobile visual evidence is attached for UI changes
- [ ] Persistent data impact documented
- [ ] Backup and rollback plan documented
- [ ] Dokploy port/network/volume behavior verified

## Agent / Rules Drift

- [ ] Not applicable
- [ ] `AGENTS.md`, `docs/AGENT_RULE_AUTHORITY.md`, and `docs/CODEX_OPERATIONS_GUIDE.md` checked for impact
- [ ] Kilo/OpenCode/Codex adapter drift checked or follow-up documented
- [ ] No stale production host, image, Docker, OS, data-boundary, or deployment-authority facts introduced

## Data Safety

- [ ] No named volume is mounted to a file path
- [ ] Application code is not shadowed by a data volume
- [ ] Existing data is preserved
- [ ] Restore path has been tested or explicitly marked pending
- [ ] Production SQLite, PDF files, FAISS/Chroma data, and bootstrap credentials are not committed

## Dev Merge Release Evidence

Required after this PR merges to `dev` when the workflow publishes an image.

- Git SHA:
- GitHub Actions run:
- Image digest:
- Tags promoted:
- Published-image smoke:
- Scan / image-size result:

## Production Promotion Evidence

Do not fill this from PR validation alone. Complete only after Dokploy/live
production verification.

- Data release/backup:
- Dokploy deployment ID:
- Dokploy checkout SHA:
- Rendered Compose image:
- Running container digest:
- Route and TLS evidence:
- `/livez` and `/readyz`:
- Representative PDF listing/search:
- Validation commands:
- Rollback procedure:
