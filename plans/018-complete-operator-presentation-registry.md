# Plan 018: Make every UI-emitted operator reason resolve to authored guidance

> **Executor instructions**: Follow this plan in order. Run each verification
> command before continuing. If a STOP condition occurs, report it instead of
> inventing copy or changing a stable backend code.
>
> **Drift check (run first)**:
> `git diff --stat f7d0536..HEAD -- flowdocs/core/operator_presentation.py flowdocs/core/services/dashboard_read_model.py flowdocs/core/maintenance_plans.py flowdocs/vaultops/services/read_model.py flowdocs/vaultops/views.py`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `f7d0536`, 2026-07-28
- **Implementation**: DONE — every inventoried UI producer resolves to authored
  guidance, with a neutral unknown-code fallback.

## Why this matters

The Workbench API adds `presentation` for every mutation reason, but the
registry contains only 45 reasons. At least 24 reasons emitted directly from
the affected UI paths are absent, so successful operations such as queueing,
confirmation, inventory verification, retention, activation, and rollback
receive the generic unknown-evidence warning. That is truthful but not the
authored meaning/consequence/action contract promised by Plan 017.

## Current state

- `flowdocs/core/operator_presentation.py:17-391` defines `REASONS`.
- `flowdocs/core/operator_presentation.py:456-482` deliberately uses a neutral
  fallback, which must remain for genuinely unknown future codes.
- `flowdocs/vaultops/views.py:212-236` attaches `present_reason(reason_code)` to
  every JSON mutation response.
- Confirmed missing UI-path codes at the planned commit include
  `maintenance_plan_created`, `maintenance_job_queued`,
  `maintenance_candidate_prepared`, `restore_queued`,
  `job_cancellation_requested`, `job_retry_queued`, `profile_configured`,
  `profile_probe_completed`, `profile_inventory_verified`, `sync_queued`,
  `confirmation_issued`, `promotion_queued`, `generation_retired`,
  `generation_unretired`, `retention_hold_created`,
  `retention_hold_released`, `gc_plan_created`, `activation_scheduled`,
  `runtime_rollback_scheduled`, `no_source_changes`, and
  `control_database_unavailable`.
- `flowdocs/core/test_operator_presentation.py:55-74` checks only a hand-picked
  subset and cannot detect a new unregistered reason at a producer.

The UI contract requires stable codes to remain unchanged and requires unknown
codes to retain the neutral fallback. Do not rename backend evidence.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Registry tests | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py test core.test_operator_presentation` | all tests pass |
| Workbench tests | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py test vaultops.tests.test_workbench core.tests_maintenance_contract` | all tests pass |
| Boundary validator | `python3 scripts/ci/validate_operator_language.py` | prints `Operator-language boundary is valid.` |
| Migration drift | `docker compose -f docker-compose.dev.yml exec -T web python flowdocs/manage.py makemigrations --check --dry-run` | no changes |

## Scope

**In scope**:

- `flowdocs/core/operator_presentation.py`
- `flowdocs/core/test_operator_presentation.py`
- a new read-only inventory helper/test under `scripts/ci/`
- focused API tests in `flowdocs/vaultops/tests/test_workbench.py`

**Out of scope**:

- renaming any stable reason/state/action value
- changing mutation authorization, state machines, or HTTP status codes
- translating copy (Plan 020 owns catalog parity)
- exposing internal-only service errors that never cross a UI/API boundary

## Steps

### Step 1: Define the UI reason inventory from producers

Create a deterministic allowlisted inventory covering reasons emitted by
Dashboard, maintenance planning, Workbench read models, and Workbench mutation
responses. Distinguish internal-only errors from values crossing the UI/API
boundary; do not blindly register every exception string in the repository.

**Verify**: the inventory test fails against `f7d0536` and lists each missing
UI reason by code and producer.

### Step 2: Author every known presentation

Add title, detail, consequence, action label/destination, and severity for every
inventory entry. Success reasons must describe the completed/queued outcome,
not use warning fallback language. Preserve `UNKNOWN_REASON`.

**Verify**: assert `known is True` and all required fields are non-empty for
every inventoried code.

### Step 3: Prove API meaning without changing evidence

Extend mutation response tests for representative success, blocked, stale, and
unknown codes. Assert the stable `reason_code` is unchanged, known responses
have the intended presentation, and a deliberately unknown code remains
neutral.

**Verify**: focused Workbench tests pass.

## Test plan

- Inventory completeness: every UI producer code is registered.
- Known success and failure: title/detail/consequence/action/severity complete.
- Unknown future code: neutral copy and exact technical code retained.
- Additive API: stable fields unchanged.
- Use `OperatorPresentationTests` and
  `VaultWorkbenchTests.test_json_mutation_error_uses_contract_envelope` as
  structural patterns.

## Done criteria

- [ ] No UI-path known reason resolves with `known=False`.
- [ ] The inventory test fails when a new producer code lacks authored copy.
- [ ] Stable code values and API fields are unchanged.
- [ ] Focused Django tests and boundary validator pass.
- [ ] No database migration is generated.

## STOP conditions

- A code is ambiguous between materially different outcomes; stop and request
  operator wording instead of writing misleading shared copy.
- Registering a reason would require changing its backend value.
- A candidate code is proven internal-only; exclude it with a documented reason
  rather than expanding the UI contract.

## Maintenance notes

Reviewers should compare the inventory to producer sites, not merely count
registry entries. Any new Workbench reason must land with its registry entry
and tests in the same commit.
