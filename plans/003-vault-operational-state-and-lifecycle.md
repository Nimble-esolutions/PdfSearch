# Plan 003: Reconcile truthful vault state and safe operator actions

> **Executor instructions**: The repository already contains health, job,
> generation, activation, restore, writer, and audit machinery. Verify and
> close gaps around the existing services; do not recreate lifecycle logic in
> views or templates.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: Plan 006 gate
- **Category**: operations / data lifecycle
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: RECONCILE

## Drift check

```bash
git diff --stat f742b59..HEAD -- \
  flowdocs/core/views.py flowdocs/core/maintenance.py \
  flowdocs/core/models.py flowdocs/core/templates/dashboard_s3ops.html \
  flowdocs/core/templates/settings.html flowdocs/core/tests.py integration_tests
```

Any required change to protected lifecycle files is a STOP condition requiring
separate operator approval.

## Historical defect and current evidence

- `flowdocs/core/views.py:1795-1870` now exposes independent enabled,
  configured, reachable, bucket-present, healthy, and safe error-code fields.
- `flowdocs/core/maintenance.py:58-133,162-374,377-673` provides generation
  promotion/rollback/purge, durable jobs, and synchronization.
- `flowdocs/core/views.py:1131-1197,1251-1290` provides job actions, audit
  trails, validations, and durable status JSON.
- Integration tests cover restore, publication, writer fencing, activation
  crash recovery, and long-running ownership.
- `dashboard_s3ops.html` mixes status, sync, restore, generation, and lease actions in a long page with generic card styling, making it unclear what is safe, pending, failed, or complete.
- Existing `flowdocs/core/artifact_vault.py` already contains the service boundary, config, manifest listing, validation, and capability helpers. Reuse it instead of creating template-level clients.

### 2026-07-26 recovery audit

Real MinIO verification passed for S3 CAS/concurrency (16 tests), application
publication (5 tests), and the direct full restore pipeline (7 tests). Focused
vault/health/job tests also passed (27 tests). The primitives work.

The deployed/operator path is not end-to-end:

- `sync_active_generation()` writes a dataset-scoped manifest, while
  `stage_generation()` reads the legacy flat manifest namespace;
- `run_job(restore_generation)` calls `stage_generation()`, not
  `run_restore_pipeline()`;
- admin promote/rollback changes `ArtifactGeneration` records without calling
  `activate_generation()`;
- `RESTORE_POLICY` is parsed but has no startup consumer;
- `mark_data_dirty()` has no mutation callers, so scheduled backup does not
  discover accumulated changes;
- production maintenance Compose omits explicit vault, restore, scheduler,
  sync-mode, and release-identity mappings;
- the integration Compose runner command is ignored by the image entrypoint,
  its test label conflicts with `core/tests.py`, and the runner is separated
  from MinIO/Redis by network wiring.

Impact: the vault can be used by controlled direct tooling, but the UI can
report staged/active lifecycle state without proving that the selected runtime
bytes were restored. A fresh deployment cannot self-restore.

Impact: operators cannot trust the status, cannot predict action consequences, and may repeat or abandon long-running operations because the UI lacks durable job state.

## Target architecture

Define a typed `VaultHealth` result with independent fields: enabled, configuration-valid, endpoint-reachable, bucket-present, credentials-valid, manifest-readable, last-success, last-error, checked-at, and capability flags. Probe with bounded timeout, no secret leakage, and explicit unavailable/unknown states.

Model maintenance actions as idempotent jobs with request id, actor, operation, queued/running/succeeded/failed/cancelled state, timestamps, safe summary, and retry policy. The UI should show current job state and poll or refresh with backoff. Destructive restore/purge actions require explicit confirmation and display the target generation, scope, and rollback path.

## Reconciliation steps

1. Preserve the passing primitive tests and add one regression that publishes
   through the production sync function and restores the same generation
   through the operator-facing job.
2. Select one dataset-scoped manifest contract. Remove the legacy flat
   namespace from the operator path without deleting historical objects.
3. Route restore jobs through the full download/validate/compatibility/
   sanitize/rehearse/activate state machine. Make `active` mean byte-level
   activation, not only a database status update.
4. Define startup behavior explicitly: either implement fail-closed
   empty-volume restore for `startup-*` policies or remove those policy names
   from active configuration. Never silently seed/empty when restore was
   requested.
5. Pass the complete vault/restore/scheduler/release environment to the
   maintenance service and add a startup posture check that reports missing
   keys without values.
6. Wire durable mutation change detection or replace the cache-only dirty flag
   with a reliable fingerprint/outbox boundary. Mark backup complete only
   after authoritative pointer publication.
7. Repair `docker-compose.integration.yml`: service networks, bucket
   initialization, test-runner entrypoint/command, importable test label,
   immutable dependency image, and automatic cleanup.
8. Exercise disabled, missing config, unreachable, forbidden, missing bucket,
   stale manifest, and healthy probes with bounded timeouts.
9. Verify duplicate sync/restore/promote/purge requests cannot duplicate work
   and that browser-visible success follows persisted terminal state.
10. Validate confirmation, target generation, rollback explanation, role, CSRF,
   focus, and error states in the current Hallmark operations UI.
11. Run two deployment drills: preserve an accumulated named volume across
    redeploy, then restore a selected generation into a genuinely fresh
    disposable volume with no host-only state.

## Commands and scope

```bash
python manage.py test \
  core.tests.AuditAndValidationTests core.tests.JobDrawerTests \
  core.tests.DashboardTests
python -m unittest discover -s integration_tests -p 'test_*.py'
python manage.py check
git diff --check
```

In scope: operations/settings views, templates, adapters, and tests. Out of
scope: production vault mutation, production credentials, destructive restore
or purge, and protected custody/activation implementations.

Changes to protected custody/activation implementations remain a separate
execution approval even though this plan identifies the required call-path
outcome.

## Tests and done criteria

- Health badges correspond to real probes and distinguish unknown from false.
- Repeating the same action does not duplicate work; retries are bounded and observable.
- Promotion/restore fencing and writer lease release are race-tested.
- A sync-created generation is restorable through the same operator path.
- `active` is emitted only after the active database/media/index pointer has
  switched and post-activation checks pass.
- `startup-*` either restores a fresh volume or fails closed with an actionable
  reason; it never silently starts empty.
- Scheduled mode detects a real PDF/category/index mutation and publishes one
  new generation; unchanged data does not republish.
- The integration Compose command runs the intended test suite without manual
  entrypoint or network workarounds.
- An intact accumulated volume and a fresh restore volume both pass their
  separate drills.
- No action reports success before persistence completes.
- Role/CSRF checks, audit events, and existing endpoint compatibility remain green.

## STOP conditions

Stop if a probe requires exposing credentials; if generation identity differs
between database, object pointer, and activation journal; if rollback cannot
name the previous generation; or if the only fix touches a protected backend
file without approval.
