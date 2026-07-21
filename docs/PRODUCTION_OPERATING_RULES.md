# Production Operating Rules

These rules apply to every PdfSearch production change.

## Source and Release

- Branch from the latest `dev`.
- Do not push fixes to merged branches.
- Every deployment must identify Git SHA, image digest, Compose hash, and data release.
- Do not deploy `latest` as the only release identifier.
- Keep application code in the image and mutable data in `/app/data`.

## Secrets

- Store secrets in Dokploy protected environment configuration.
- Never commit API keys, passwords, secret keys, or copied production `.env` files.
- Rotate any secret that appears in logs, chat, screenshots, or Git history.
- Production must use `DEBUG=False`, explicit hosts, and no insecure fallbacks.

## Data Safety

- Never delete a volume without a verified backup and restore path.
- Never use a host bind path as an undocumented persistence contract.
- Never mount a named volume to a file path.
- Never mount persistent data over `/app/flowdocs`.
- Treat SQLite, media, FAISS, Chroma, and manifests as one recovery set.

## Startup and Health

- Migrations must fail closed.
- `/livez` proves process liveness only.
- `/readyz` must prove database, cache, migrations, and required data state.
- Do not route Traefik traffic to a container that is only HTTP-200 on `/`.

## Deployment

- Back up before schema, volume, or image changes.
- Verify the exact internal container port in Dokploy.
- Run authenticated login, PDF listing, search, and static asset smoke tests.
- Record rollback image and data release before promotion.

## Incident Handling

- Preserve logs, deployment metadata, and checksums.
- Do not clean or prune the production server during an incident.
- Separate code rollback from database/data rollback.
- Perform a restore drill after recovery.
