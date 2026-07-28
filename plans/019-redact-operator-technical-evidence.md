# Plan 019: Replace raw exception summaries with bounded technical evidence

> **Executor instructions**: Follow the steps exactly and preserve audit and
> retry semantics. Stop if redaction would destroy evidence required by an
> existing automation consumer.
>
> **Drift check (run first)**:
> `git diff --stat f7d0536..HEAD -- flowdocs/core/maintenance.py flowdocs/core/maintenance_plans.py flowdocs/core/operator_presentation.py flowdocs/core/templates/components/operator_evidence.html flowdocs/core/templates/vaultops/workbench.html`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: 018
- **Category**: security
- **Planned at**: commit `f7d0536`, 2026-07-28
- **Implementation**: DONE — browser projections expose bounded safe codes and
  identifiers while raw exception and audit messages remain server-side.

## Why this matters

Local maintenance jobs persist `str(exc)[:2000]` as `error_summary`. The
presentation decorator treats that arbitrary text as a technical code, and the
Workbench passes it verbatim to collapsed Technical details. An exception may
contain paths, provider text, filenames, or other unbounded diagnostics, so
the UI is not actually redacted even though the component is collapsed and
superadmin-only.

## Current state

- `flowdocs/core/maintenance.py:213-293,330-414` stores raw exception strings
  in `MaintenanceJob.error_summary`.
- `flowdocs/core/maintenance_plans.py:513-535` serializes the raw field into the
  Workbench read model.
- `flowdocs/core/operator_presentation.py:513-515` falls back from
  `safe_error_code` to `error_summary` and treats either as a code.
- `flowdocs/core/templates/vaultops/workbench.html:292,328` supplies the raw
  summary as `technical_code`.
- `flowdocs/core/templates/components/operator_evidence.html:9-16` correctly
  provides the presentation container but performs no redaction itself.

The database field and durable audit history may remain for compatibility; the
browser boundary must receive only a stable safe code and bounded identifiers.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py test core.tests_maintenance_contract core.test_operator_presentation vaultops.tests.test_workbench` | all pass |
| Template validator | `python3 scripts/ci/validate_operator_language.py` | exit 0 |
| Secret-pattern check | `git diff --check` | exit 0 |
| Browser gate | `npx playwright test browser_tests/vault-workbench.spec.ts` | all configured projects pass |

## Scope

**In scope**:

- maintenance error classification and read-model projection
- operator presentation/component call sites
- focused unit, template, and browser tests

**Out of scope**:

- deleting historical `error_summary` database data
- weakening superadmin authorization
- exposing stack traces, paths, credentials, object keys, or provider messages
- changing retry decisions or job state transitions

## Steps

### Step 1: Establish a stable safe-code projection

Project or persist a bounded `safe_error_code` derived from exception class and
existing stable error attributes. Keep raw summaries server-side where current
support/audit compatibility requires them. Unknown exceptions must map to a
neutral stable code such as the existing `maintenance_job_failed`, never to
their message text.

**Verify**: tests inject exceptions containing path-like and secret-like marker
text; the marker remains available only where explicitly required server-side
and is absent from the serialized Workbench state.

### Step 2: Remove raw summary from presentation

Delete the `or mapping.get("error_summary")` presentation fallback. Render the
safe code through the shared component and pass only bounded evidence IDs.
Keep human title/detail/consequence visible.

**Verify**: rendered HTML and response JSON do not contain injected marker
text, including after opening Technical details.

### Step 3: Enforce the redaction boundary

Extend the CI validator/test fixtures to reject `error_summary` as a
`technical_code` argument and reject its inclusion in additive presentation
objects. Add a positive fixture for `safe_error_code`.

**Verify**: negative fixtures fail with the intended diagnostic; repository
templates pass.

## Test plan

- Known typed error becomes its exact safe code.
- Unknown exception becomes neutral `maintenance_job_failed`.
- Arbitrary exception text never appears in HTML, JSON presentation, accessible
  text, or technical details.
- Retry and status transitions remain unchanged.

## Done criteria

- [ ] No Workbench template renders `error_summary`.
- [ ] No arbitrary exception message reaches browser-visible or expandable
  technical evidence.
- [ ] Stable safe codes and bounded IDs remain available to superadmins.
- [ ] Existing audit/retry behavior and focused gates pass.
- [ ] No migration is generated unless the executor stops for explicit schema
  approval.

## STOP conditions

- A documented external consumer depends on raw `error_summary` in the
  Workbench API.
- Safe classification requires collapsing two errors that lead to different
  retry safety decisions.
- A schema migration appears necessary; request approval before proceeding.

## Maintenance notes

Collapsed is not redacted. Reviewers must inspect the actual serialized values
and expanded details with adversarial marker text.
