Status: Active
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/PRODUCTION_OPERATING_RULES.md
Supersedes: None

# Production Operating Rules

These rules apply to every PdfSearch production change.

## Source and Release

- Branch from the latest `dev`.
- Do not push fixes to merged branches.
- Every deployment must identify Git SHA, image digest, Compose hash, and data release.
- Dokploy must pull exact web and Redis image digests with `pull_policy: always`.
- Do not deploy a tag, cached `latest`, or alias as the release identifier.
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
- Treat SQLite, media, FAISS, Chroma, snapshots, and checksums as one recovery set.
- Legacy and active data are divergent custody domains. Never copy them directly;
  require quarantine, inventory, conflict classification, staged restore, FAISS
  fingerprint validation, and explicit promotion.
- RustFS is currently an isolated operator recovery vault. Application-level S3
  integration and automatic cross-environment sync are not implemented.

## Startup and Health

- Migrations must fail closed.
- `/livez` proves process liveness only.
- `/readyz` currently proves database connectivity, the configured cache (or reports `not_configured` when no cache URL is set), and that no migrations are pending. Required data, media, FAISS, and Chroma validation remains an operator checklist; it is not implemented in the endpoint.
- Do not route Traefik traffic to a container that is only HTTP-200 on `/`.

## Deployment

- Back up before schema, volume, or image changes.
- Verify the exact internal container port in Dokploy.
- Run authenticated login, PDF listing, search, and static asset smoke tests.
- Verify link/path scan, Mermaid validation, Compose config, `/livez`, `/readyz`,
  PDF count, FAISS count, and representative search.
- Record rollback image and data release before promotion.

## Incident Handling

- Preserve logs, deployment metadata, and checksums.
- Do not clean or prune the production server during an incident.
- Separate code rollback from database/data rollback.
- Perform a restore drill after recovery.
