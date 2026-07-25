Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-07-25
Canonical source: docs/INDEX.md
Supersedes: None

# Documentation Index

This index routes readers by task. The metadata at the top of each maintained
document identifies its status, audience, owner, verification date, canonical
source, and supersession relationship.

## Local Development

- [`README.md`](../README.md) — prerequisites, architecture, and local start
- [`../AGENTS.md`](../AGENTS.md) — repository-local agent instructions and validation gates
- [`AGENT_RULE_AUTHORITY.md`](AGENT_RULE_AUTHORITY.md) — canonical order for Codex, Kilo, OpenCode, and operations-rule adapters
- [`docker-compose.dev.yml`](../docker-compose.dev.yml) — local services and isolated named volumes
- [`.env.example`](../.env.example) — canonical non-secret environment example
- [`ENVIRONMENT_CONTRACT.md`](ENVIRONMENT_CONTRACT.md) — runtime env variable contract and stale-template supersession
- [`ENVIRONMENT_CONFIGURATION_GUIDE.md`](ENVIRONMENT_CONFIGURATION_GUIDE.md) — variable impact matrix, MB upload migration, and edge cases
- [`environments/development.env.example`](environments/development.env.example) — reviewed local development example
- [`environments/stage.env.example`](environments/stage.env.example) — reviewed stage example
- [`environments/production.env.example`](environments/production.env.example) — reviewed production reference
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contribution, PR, and dev-to-release workflow
- [`environment`](../flowdocs/core/environment.py) — APP_ENV, PRODUCTION_SOURCE_ID, AUTHORITATIVE_DATASET_ID, DATASET_ID, BACKUP_ROLE, EXTERNAL_SIDE_EFFECTS_MODE, DATA_MODE
- [`side_effects`](../flowdocs/core/side_effects.py) — external side-effect safety gating
- [`ai_guard`](../flowdocs/core/ai_guard.py) — AI operation authorization
- [`activate`](../flowdocs/core/activate.py) — activation entry point
- [`activation_journal`](../flowdocs/core/activation_journal.py) — activation audit trail
- [`global_writer`](../flowdocs/core/global_writer.py) — global writer fencing
- [`registration`](../flowdocs/core/registration.py) — dataset registration
- [`backup_policy`](../flowdocs/core/backup_policy.py) — backup policy enforcement
- [`sanitize`](../flowdocs/core/sanitize.py) — data sanitization pipeline
- [`rehearsal`](../flowdocs/core/rehearsal.py) — migration rehearsal
- [`lease`](../flowdocs/core/lease.py) — writer lease management
- [`compatibility`](../flowdocs/core/compatibility.py) — compatibility checks
- [`metrics`](../flowdocs/core/metrics.py) — metrics collection
- [`namespace`](../flowdocs/core/namespace.py) — namespace management
- [`object_store_capabilities`](../flowdocs/core/object_store_capabilities.py) — object store capability detection

## Dokploy Deployment

- [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — Compose deployment, UI checkpoints, migration, backup, restore, and rollback
- [`DOKPLOY_DATA_PERSISTENCE.md`](DOKPLOY_DATA_PERSISTENCE.md) — Deploy/autodeploy behavior, named-volume survival, destructive actions, and recovery options
- [`DEPLOYMENT.md`](../DEPLOYMENT.md) — concise deployment contract
- [`ENVIRONMENT_CONTRACT.md`](ENVIRONMENT_CONTRACT.md) — required production env values and bootstrap caveats
- [`PRODUCTION_OPERATING_RULES.md`](PRODUCTION_OPERATING_RULES.md) — release, data, health, and incident rules
- [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md) — Redis image boundary, digest, and pull policy
- [`PRODUCTION_BASELINE.md`](PRODUCTION_BASELINE.md) — verified production, source, image, and data baseline

## Release Promotion

- [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md) — current workflow guarantees and future targets
- [`releases/2026-07-22-admin-operations-cockpit.md`](releases/2026-07-22-admin-operations-cockpit.md) — Admin Operations Cockpit merge, image digest, and dev-release evidence
- Data release pipeline merge evidence is maintained in [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md) and the current data-custody/release records.
- [`SECURITY_SCAN.md`](SECURITY_SCAN.md) — Trivy behavior, root cause, remediation, and verification
- [`PERSISTENT_DATA_RELEASE.md`](PERSISTENT_DATA_RELEASE.md) — current release record and proposed artifact manifest
- [`UI_DATA_INTEGRATION_PLAN.md`](UI_DATA_INTEGRATION_PLAN.md) — historical UI, init-data, FAISS, bootstrap, and blue-green integration plan
- [`FAISS_COMPATIBILITY.md`](FAISS_COMPATIBILITY.md) — current index evidence and manual compatibility gates
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; use the roadmap for current targets

## Data Recovery

- [`DATA_CUSTODY_AND_PROMOTION.md`](DATA_CUSTODY_AND_PROMOTION.md) — quarantine, reconciliation, staging, and explicit promotion
- [`RUSTFS_RECOVERY_VAULT.md`](RUSTFS_RECOVERY_VAULT.md) — snapshot/checksum custody boundary
- [`PERSISTENT_DATA_RELEASE.md`](PERSISTENT_DATA_RELEASE.md) — recovery-set contents and compatibility rules
- [`FAISS_COMPATIBILITY.md`](FAISS_COMPATIBILITY.md) — index fingerprint validation
- [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) — backup evidence, restore isolation, and failed-restore response
- [`ROOT_CAUSE_ANALYSIS.md`](ROOT_CAUSE_ANALYSIS.md) — historical migration incident context
- [`restore_pipeline`](../flowdocs/core/restore_pipeline.py) — restore pipeline orchestration
- [`restore_workspace`](../flowdocs/core/restore_workspace.py) — isolated restore workspace management

## Incident Response

- [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) — executable incident cards for container, digest, volume, migration, Dokploy, Redis, readiness, and restore failures
- [`PRODUCTION_OPERATING_RULES.md`](PRODUCTION_OPERATING_RULES.md) — non-negotiable operating rules
- [`CODEX_OPERATIONS_GUIDE.md`](CODEX_OPERATIONS_GUIDE.md) — Codex-friendly adapter for local PR, release, and operations workflows
- `/health/data/` — data health endpoint
- `/health/lease/` — writer lease health endpoint
- `/health/metrics/` — metrics health endpoint

## Client Usage

- [`CLIENT_USER_MANUAL.md`](CLIENT_USER_MANUAL.md) — sign-in, upload, search, permissions, and support guidance
- [`AI_SAHAKAR_ADMIN_USER_GUIDE.md`](AI_SAHAKAR_ADMIN_USER_GUIDE.md) — Operations Cockpit and document-management workflow
- [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md) — protected public/admin design and interaction contract
- [`AI_SAHAKAR_DEVELOPER_GUIDE.md`](AI_SAHAKAR_DEVELOPER_GUIDE.md) — safe UI extension and verification workflow
- [`DEV_CLEANUP_SCOPE.md`](DEV_CLEANUP_SCOPE.md) — read-only cleanup audit for `dev`
- `/dashboard/operations/` — operations dashboard
- `/dashboard/users/` — user management dashboard
- PDF lifecycle — PDF upload, processing, indexing, and archival lifecycle
- Generation lifecycle — data generation creation, validation, and promotion lifecycle

## Management Commands

- `config_inspect` — inspect runtime configuration
- `verify_object_store_capabilities` — verify object store capabilities
- `inventory_artifacts` — inventory data artifacts with checksums
- `validate_data_release` — validate a data release manifest

## Historical Context

Historical documents remain for context and are not deployment instructions:

- [`DOCKER_BUILD_FIX.md`](DOCKER_BUILD_FIX.md) — historical build failure and prevention notes; current deployment source is [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md)
- [`DOCKER_HUB_AUTH.md`](DOCKER_HUB_AUTH.md) — historical Docker Hub authentication notes; current Redis source is [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md)
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; current release source is [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md)
- [`ROOT_CAUSE_ANALYSIS.md`](ROOT_CAUSE_ANALYSIS.md) — historical migration incident; current response source is [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)
- [`../DOKPLOY_STATIC_DEPLOYMENT.md`](../DOKPLOY_STATIC_DEPLOYMENT.md) — unrelated static landing-page deployment notes; no current FlowDocs replacement because it is not part of the application deployment

## Diagrams

- [`deployment-flow.mmd`](diagrams/deployment-flow.mmd) — source-to-Dokploy release flow
- [`runtime-topology.mmd`](diagrams/runtime-topology.mmd) — current runtime and network boundary
- [`data-custody-promotion.mmd`](diagrams/data-custody-promotion.mmd) — snapshot-to-promotion controls
- [`incident-recovery.mmd`](diagrams/incident-recovery.mmd) — incident evidence and isolated recovery
- [`legacy-reconciliation.mmd`](diagrams/legacy-reconciliation.mmd) — active versus legacy data classification
- [`ui-shell-and-evidence.mmd`](diagrams/ui-shell-and-evidence.mmd) — public workbench regions and mobile source drawer
- [`ui-change-control.mmd`](diagrams/ui-change-control.mmd) — enhancement, verification, and human-approval flow
- [`admin-operator-workflow.mmd`](diagrams/admin-operator-workflow.mmd) — admin document-to-search readiness flow

## Document Lifecycle

- `Active` is the current operational or user-facing source.
- `Proposed` describes a target that is not emitted or enforced by current tooling.
- `Historical` preserves context and must link to its current replacement or say that none exists.
- `Client-facing` is reserved for content intended for end users.

Current-versus-planned labels are mandatory for recovery, storage, artifact, and
automation claims. RustFS is current as an operator recovery vault with explicit
superadmin generation sync, staged pull, and quarantine staging. Application
S3 integration, automatic cross-environment sync, generated artifact manifests,
and FAISS recovery automation are implemented through the object store
capabilities, restore pipeline, and activation journal modules.

When a document is superseded, retain the old file only when its historical
context is useful, change its status to `Historical`, and add a prominent link
to the replacement. Do not leave stale instructions looking active.
