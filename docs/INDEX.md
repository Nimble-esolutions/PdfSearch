Status: Active
Audience: Developer
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: docs/INDEX.md
Supersedes: None

# Documentation Index

## Current operational incidents and decisions

- [Activated-runtime additive migration rollout](incidents/2026-08-04-activated-runtime-migration-gap.md)
- [Chrome for Testing macOS application-registration crash](incidents/2026-08-06-chrome-for-testing-macos-registration-crash.md) — resolved local tooling incident; use the installed headless shell for diagram/document rendering

## Current operational status

- [HANDOFF.md](HANDOFF.md) — canonical living record for repository, stage,
  legacy production, signed runtime, backup/recovery, current decisions, and
  exact next actions.
- [STATUS-2026-08-03.md](STATUS-2026-08-03.md) — historical snapshot of signed
  stage activation, search-policy repair, and recovery evidence.
- [STATUS-2026-08-02.md](STATUS-2026-08-02.md) — historical source snapshot,
  RustFS v2 publication/clone, quarantine, OCR, and migration evidence.
- [OPERATIONS_CHANGELOG-2026-08-02.md](OPERATIONS_CHANGELOG-2026-08-02.md) —
  dated timeline, backup-size explanation, current-versus-historical matrix,
  readiness guide, and documentation coverage checklist.
- [LEGACY_VS_CURRENT_STATE.md](LEGACY_VS_CURRENT_STATE.md) — living old-versus-
  current comparison and documentation maintenance contract.
- [ENVIRONMENT_REFERENCE.md](ENVIRONMENT_REFERENCE.md) — categorized reference
  for every reviewed environment variable, its dev/stage/production posture,
  impact, examples, and safe-change recipes.
- [dataops/V3_ARCHITECTURE.md](dataops/V3_ARCHITECTURE.md) — canonical
  operator-facing backup/import/restore architecture and the explicit internal
  compatibility boundary.
- [dataops/ROLLOUT.md](dataops/ROLLOUT.md) — reusable DataOps v3 rollout and
  recovery-test procedure without environment-specific profiles or moving
  deployment evidence.
- [dataops/V3_IMPACT_ANALYSIS.md](dataops/V3_IMPACT_ANALYSIS.md) — completed
  cutover impact plus the remaining VaultOps dependency-removal backlog.
- [PRODUCT_ANALYTICS_RECOMMENDATION.md](PRODUCT_ANALYTICS_RECOMMENDATION.md) —
  privacy-first PostHog/OSS comparison, event allowlist, forbidden data, and
  stage-pilot release gate; proposed, not enabled.

This index routes readers by task. The metadata at the top of each maintained
document identifies its status, audience, owner, verification date, canonical
source, and supersession relationship.

Deployment names are explicit throughout current documentation:
`www.ai-sahakar.net` is legacy authoritative production,
`2026.ai-sahakar.net` is the active 2026 stage/rehearsal host, and the future
2026 production project is not deployed. Exact live evidence stays in
[HANDOFF.md](HANDOFF.md).

The complete environment variable tables and environment-specific examples are
maintained in [ENVIRONMENT_REFERENCE.md](ENVIRONMENT_REFERENCE.md).

## Local Development

- [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) — current runtime,
  repository structure, module boundaries, and data-flow map

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
- [`activate`](../flowdocs/core/activate.py) — legacy core activation
  compatibility primitive; not the DataOps v3 operator entry point
- [`activation_journal`](../flowdocs/core/activation_journal.py) — legacy core
  activation crash-recovery journal retained for compatibility
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
- [`releases/2026-08-07-search-answer-latency.md`](releases/2026-08-07-search-answer-latency.md) — measured search-latency RCA, signed-runtime/access-scoped cache design, UI continuity, impact, and stage rollout gate
- [`releases/2026-08-03-operator-workbench-truth-and-scale.md`](releases/2026-08-03-operator-workbench-truth-and-scale.md) — single readiness authority, maintenance/PDF scale UX, browser evidence, and stage `:latest` policy
- [`releases/2026-07-22-admin-operations-cockpit.md`](releases/2026-07-22-admin-operations-cockpit.md) — Admin Operations Cockpit merge, image digest, and dev-release evidence
- Data release pipeline merge evidence is maintained in [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md) and the current data-custody/release records.
- [`SECURITY_SCAN.md`](SECURITY_SCAN.md) — Trivy behavior, root cause, remediation, and verification
- [`PERSISTENT_DATA_RELEASE.md`](PERSISTENT_DATA_RELEASE.md) — current release record and proposed artifact manifest
- [`UI_DATA_INTEGRATION_PLAN.md`](UI_DATA_INTEGRATION_PLAN.md) — historical UI, init-data, FAISS, bootstrap, and blue-green integration plan
- [`FAISS_COMPATIBILITY.md`](FAISS_COMPATIBILITY.md) — current index evidence and manual compatibility gates
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; use the roadmap for current targets

## Data Recovery

- [`DATA_CUSTODY_AND_PROMOTION.md`](DATA_CUSTODY_AND_PROMOTION.md) — quarantine, reconciliation, staging, and explicit promotion
- [`RUSTFS_RECOVERY_VAULT.md`](RUSTFS_RECOVERY_VAULT.md) — historical v2 rehearsal evidence and recovery-safety lessons; use DataOps v3 for current operations
- [`RECOVERY_CERTIFICATION.md`](RECOVERY_CERTIFICATION.md) — isolated fresh-volume and accumulated-volume certification with paired data/control custody
- [`INTERNAL_VAULT_MIGRATION.md`](INTERNAL_VAULT_MIGRATION.md) — dry-run-first legacy volume migration and candidate-generation publish tool
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
- [`DOCUMENT_INTAKE_WORKBENCH.md`](DOCUMENT_INTAKE_WORKBENCH.md) — reviewed multi-file intake, receipt states, reason-based lifecycle controls, impact, and verification
- [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md) — protected public/admin design and interaction contract
- [`design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md`](design/PUBLIC_SEARCH_THEME_ARCHITECTURE.md) — isolated Classic/Workbench selection and rollback contract
- [`handoffs/PUBLIC_SEARCH_THEME_ENGINE.md`](handoffs/PUBLIC_SEARCH_THEME_ENGINE.md) — historical public-theme delivery handoff; current rollout state is in `HANDOFF.md`
- [`AI_SAHAKAR_DEVELOPER_GUIDE.md`](AI_SAHAKAR_DEVELOPER_GUIDE.md) — safe UI extension and verification workflow
- [`DEV_CLEANUP_SCOPE.md`](DEV_CLEANUP_SCOPE.md) — read-only cleanup audit for `dev`
- `/dashboard/operations/` — operations dashboard
- `/dashboard/users/` — user management dashboard
- PDF lifecycle — reviewed intake, receipt, processing, indexing, reversible search removal, and custody-aware recovery
- Generation lifecycle — data generation creation, validation, and promotion lifecycle

## Representative management commands

This is a supported navigation list, not the complete internal worker-command
inventory. Use `python manage.py help` from the exact running image when an
incident requires exhaustive discovery.

- `config_inspect` — inspect runtime configuration
- `verify_object_store_capabilities` — verify object store capabilities
- `inventory_artifacts` — inventory data artifacts with checksums
- `validate_data_release` — validate a data release manifest
- `reconcile_data_operations` — reconcile resumable DataOps operation state
- `stage_dataops_restore` — prepare a DataOps restore in isolated staging storage
- `prepare_dataops_candidate` — validate/repair a v3 restore candidate
- `verify_recovery_certification` — verify disposable recovery evidence
- `verify_activation_runtime` — verify signed runtime activation evidence
- `maintenance_preflight` — report maintenance capability blockers
- `emergency_db` — explicit emergency database evidence workflow

## Historical Context

Historical documents remain for context and are not deployment instructions:

- [`DOCKER_BUILD_FIX.md`](DOCKER_BUILD_FIX.md) — historical build failure and prevention notes; current deployment source is [`DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md)
- [`DOCKER_HUB_AUTH.md`](DOCKER_HUB_AUTH.md) — historical Docker Hub authentication notes; current Redis source is [`REDIS_GHCR_SETUP.md`](REDIS_GHCR_SETUP.md)
- [`DOCKER_IMAGE_OPTIMIZATION.md`](DOCKER_IMAGE_OPTIMIZATION.md) — historical optimization notes; current release source is [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md)
- [`ROOT_CAUSE_ANALYSIS.md`](ROOT_CAUSE_ANALYSIS.md) — historical migration incident; current response source is [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md)
- [`VAULT_ACTIVE_SYNC.md`](VAULT_ACTIVE_SYNC.md) — historical publication design; implementation remains disabled compatibility code, while DataOps v3 is the supported contract
- [`VAULT_CONTROL_PLANE.md`](VAULT_CONTROL_PLANE.md) — historical schema foundation and current internal compatibility reference; not an operator workbench
- [`VAULT_MULTI_SOURCE_RESTORE.md`](VAULT_MULTI_SOURCE_RESTORE.md) — historical profile-driven restore and 2026 clone evidence; current operator replacement is DataOps v3 import/restore
- [`VAULT_RETENTION_GC_RUNBOOK.md`](VAULT_RETENTION_GC_RUNBOOK.md) — historical internal API runbook; no supported DataOps v3 retention/GC UI
- [`incidents/2026-08-06-vaultops-documentation-drift.md`](incidents/2026-08-06-vaultops-documentation-drift.md) — root cause, impact, corrected boundary, and prevention rules for the VaultOps/DataOps documentation incident
- [`../DOKPLOY_STATIC_DEPLOYMENT.md`](../DOKPLOY_STATIC_DEPLOYMENT.md) — unrelated static landing-page deployment notes; no current FlowDocs replacement because it is not part of the application deployment

## Diagrams

- [`deployment-flow.mmd`](diagrams/deployment-flow.mmd) — source-to-Dokploy release flow
- [`runtime-topology.mmd`](diagrams/runtime-topology.mmd) — current runtime and network boundary
- [`data-custody-promotion.mmd`](diagrams/data-custody-promotion.mmd) — current DataOps v3 ownership, candidate, signed activation, and rollback controls
- [`incident-recovery.mmd`](diagrams/incident-recovery.mmd) — current signed incident recovery and isolated paired-volume proof
- [`legacy-reconciliation.mmd`](diagrams/legacy-reconciliation.mmd) — dated migration/reconciliation method without mutable counts
- [`ui-shell-and-evidence.mmd`](diagrams/ui-shell-and-evidence.mmd) / [`SVG`](diagrams/ui-shell-and-evidence.svg) — Classic, Workbench, public-information shell, and question-language backend boundaries
- [`ui-change-control.mmd`](diagrams/ui-change-control.mmd) / [`SVG`](diagrams/ui-change-control.svg) — impact analysis, verification, PR, release, stage-canary, and audit flow
- [`admin-operator-workflow.mmd`](diagrams/admin-operator-workflow.mmd) — admin document-to-search readiness flow
- [`ocr-index-lifecycle.mmd`](diagrams/ocr-index-lifecycle.mmd) — core-owned native extraction, bounded local OCR, embedding, index, candidate, and activation lifecycle
- [`search-answer-hot-path.mmd`](diagrams/search-answer-hot-path.mmd) / [`SVG`](diagrams/search-answer-hot-path.svg) — signed-runtime search corpus, provider/result caches, authorization, and isolated frontend rendering paths

## Document Lifecycle

Representative lifecycle diagram:

- [ocr-index-lifecycle.mmd](diagrams/ocr-index-lifecycle.mmd)

Dated 2026 legacy-to-stage migration evidence (current state remains in
[`HANDOFF.md`](HANDOFF.md)):

- [legacy-to-stage-2026.mmd](diagrams/legacy-to-stage-2026.mmd)
- [stage-recovery-state.mmd](diagrams/stage-recovery-state.mmd)

- `Active` is a current operational or user-facing source. `HANDOFF.md` is the
  sole current-state authority when dated status evidence differs.
- `Proposed` describes a target that is not emitted or enforced by current tooling.
- `Historical` preserves context and must link to its current replacement or say that none exists.
- `Client-facing` is reserved for content intended for end users.

Current-versus-planned labels are mandatory for recovery, storage, artifact, and
automation claims. RustFS is current as DataOps v3 recovery storage. DataOps
publishes complete immutable recovery points and prepares isolated candidates;
signed activation remains separate. Automatic cross-environment sync is not a
normal operator contract. The `vaultops` package is still installed for
selected authenticated maintenance endpoints, durable control records, and
activation/runtime compatibility. Legacy profile/sync/mutation APIs are
default-off removal debt; the former workbench and profile choreography are not
supported operator surfaces, and the package has not been fully removed.

When a document is superseded, retain the old file only when its historical
context is useful, change its status to `Historical`, and add a prominent link
to the replacement. Do not leave stale instructions looking active.
