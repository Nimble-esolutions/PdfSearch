Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/INDEX.md
Supersedes: None

# Documentation Index

This index routes readers by task. The metadata at the top of each maintained
document identifies its status, audience, owner, verification date, canonical
source, and supersession relationship.

## Local Development

- [`README.md`](../README.md) — prerequisites, architecture, and local start
- [`docker-compose.dev.yml`](../docker-compose.dev.yml) — local services and isolated named volumes
- [`env.minimal`](../env.minimal) — non-secret environment reference
- [`env.template`](../env.template) — broader environment reference; never copy production values

## Dokploy Deployment

- [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — Compose deployment, UI checkpoints, migration, backup, restore, and rollback
- [`DEPLOYMENT.md`](../DEPLOYMENT.md) — concise deployment contract
- [`PRODUCTION_OPERATING_RULES.md`](PRODUCTION_OPERATING_RULES.md) — release, data, health, and incident rules
- [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md) — current Redis image boundary and mirror status

## Release Promotion

- [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md) — current workflow guarantees and future targets
- [`SECURITY_SCAN.md`](SECURITY_SCAN.md) — Trivy behavior, root cause, remediation, and verification
- [`PERSISTENT_DATA_RELEASE.md`](PERSISTENT_DATA_RELEASE.md) — current release record and proposed artifact manifest
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; use the roadmap for current targets

## Data Recovery

- [`PERSISTENT_DATA_RELEASE.md`](PERSISTENT_DATA_RELEASE.md) — recovery-set contents and compatibility rules
- [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) — backup evidence, restore isolation, and failed-restore response
- [`ROOT_CAUSE_ANALYSIS.md`](ROOT_CAUSE_ANALYSIS.md) — historical migration incident context

## Incident Response

- [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) — executable incident cards for container, digest, volume, migration, Dokploy, Redis, readiness, and restore failures
- [`PRODUCTION_OPERATING_RULES.md`](PRODUCTION_OPERATING_RULES.md) — non-negotiable operating rules

## Client Usage

- [`CLIENT_USER_MANUAL.md`](CLIENT_USER_MANUAL.md) — sign-in, upload, search, permissions, and support guidance

## Historical Context

Historical documents remain for context and are not deployment instructions:

- [`DOCKER_BUILD_FIX.md`](DOCKER_BUILD_FIX.md) — historical build failure and prevention notes; current deployment source is [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md)
- [`DOCKER_HUB_AUTH.md`](DOCKER_HUB_AUTH.md) — historical Docker Hub authentication notes; current Redis source is [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md)
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; current release source is [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md)
- [`ROOT_CAUSE_ANALYSIS.md`](ROOT_CAUSE_ANALYSIS.md) — historical migration incident; current response source is [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)
- [`../DOKPLOY_STATIC_DEPLOYMENT.md`](../DOKPLOY_STATIC_DEPLOYMENT.md) — unrelated static landing-page deployment notes; no current FlowDocs replacement because it is not part of the application deployment

## Document Lifecycle

- `Active` is the current operational or user-facing source.
- `Proposed` describes a target that is not emitted or enforced by current tooling.
- `Historical` preserves context and must link to its current replacement or say that none exists.
- `Client-facing` is reserved for content intended for end users.

When a document is superseded, retain the old file only when its historical
context is useful, change its status to `Historical`, and add a prominent link
to the replacement. Do not leave stale instructions looking active.
