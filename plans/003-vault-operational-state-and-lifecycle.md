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

Impact: operators cannot trust the status, cannot predict action consequences, and may repeat or abandon long-running operations because the UI lacks durable job state.

## Target architecture

Define a typed `VaultHealth` result with independent fields: enabled, configuration-valid, endpoint-reachable, bucket-present, credentials-valid, manifest-readable, last-success, last-error, checked-at, and capability flags. Probe with bounded timeout, no secret leakage, and explicit unavailable/unknown states.

Model maintenance actions as idempotent jobs with request id, actor, operation, queued/running/succeeded/failed/cancelled state, timestamps, safe summary, and retry policy. The UI should show current job state and poll or refresh with backoff. Destructive restore/purge actions require explicit confirmation and display the target generation, scope, and rollback path.

## Reconciliation steps

1. Run focused unit/integration tests and record which original requirements
   are already satisfied.
2. Exercise disabled, missing config, unreachable, forbidden, missing bucket,
   stale manifest, and healthy probes with bounded timeouts.
3. Verify duplicate sync/restore/promote/purge requests cannot duplicate work
   and that browser-visible success follows persisted terminal state.
4. Validate confirmation, target generation, rollback explanation, role, CSRF,
   focus, and error states in the current Hallmark operations UI.
5. Patch only confirmed presentation/service-adapter gaps. Route lifecycle
   defects in protected modules to a separate, explicitly approved plan.

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

## Tests and done criteria

- Health badges correspond to real probes and distinguish unknown from false.
- Repeating the same action does not duplicate work; retries are bounded and observable.
- Promotion/restore fencing and writer lease release are race-tested.
- No action reports success before persistence completes.
- Role/CSRF checks, audit events, and existing endpoint compatibility remain green.

## STOP conditions

Stop if a probe requires exposing credentials; if generation identity differs
between database, object pointer, and activation journal; if rollback cannot
name the previous generation; or if the only fix touches a protected backend
file without approval.
