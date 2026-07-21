Status: Historical
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/REDIS_GHCR_SETUP.md
Supersedes: None

> Historical Docker Hub notes. They are not the current deployment contract and
> contain no current authentication or image-promotion procedure. See
> [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md) for current Redis policy.

# Docker Hub Authentication Notes

## Preserved Context

An earlier deployment discussion considered Docker Hub pull limits and optional
authentication for public Redis images. Authentication details, account names,
tokens, and server login commands do not belong in this repository.

## Current Replacement

The current Compose file uses the official `redis:7-alpine` image. Operators
record the resolved deployed image digest and verify it with the commands in
[`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md). The repository's GHCR Redis mirror
is a separate workflow and is not the current production image contract.
