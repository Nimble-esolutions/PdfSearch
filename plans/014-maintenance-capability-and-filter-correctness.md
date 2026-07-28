# Plan 014: Correct maintenance capability gates and selection validation

> **Executor instructions**: Follow this plan step by step. Stop on any drift
> or failed assumption; do not broaden the mutation scope.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: 013
- **Category**: bug
- **Planned at**: commit `6c9a262`, 2026-07-28

## Why this matters

The capability matrix is a safety boundary. It currently uses an `if/elif`
chain that can leave `reindex_needed` enabled when force reindex is disabled but
external embeddings are also disabled. That can expose a queue path whose
operation contract says embeddings are required. Selection validation also
accepts an uploaded-after date later than uploaded-before, creating confusing
empty previews instead of a typed error.

## Current state

- `flowdocs/core/maintenance_plans.py:90-122` computes all four capability
  reasons. The `not FORCE_REINDEX_ENABLED` branch at `:114-115` prevents the
  external-embeddings branch at `:117-121` from running.
- `flowdocs/core/maintenance_plans.py:134-165` validates each date format but
  does not compare the two dates.
- `flowdocs/core/templates/vaultops/workbench.html:122-199` renders operation
  cards and preview buttons from those reasons; a wrong reason directly changes
  the operator's available mutation.
- `flowdocs/core/tests_maintenance_contract.py:15-199` contains the existing
  override-settings and stale/expiry/idempotency patterns to extend.

## Commands you will need

| Purpose | Command | Expected result |
|---|---|---|
| Focused tests | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test flowdocs.core.tests_maintenance_contract` | all tests pass |
| Full Django suite | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test` | all tests pass |
| Browser contract | `npx playwright test browser_tests/vault-workbench.spec.ts` | all tests pass |

## Scope

**In scope**: `flowdocs/core/maintenance_plans.py`, maintenance contract tests,
the Workbench template only if a reason label/action needs correction, and the
browser contract.

**Out of scope**: worker extraction/embedding algorithms, remote Vault gates,
database schema changes, and enabling production flags by default.

## Steps

### Step 1: Replace capability precedence with an explicit matrix

Compute common blockers first. Then independently apply operation-specific
requirements: `reindex_selected` requires force-reindex authorization and
external embeddings; `reindex_needed` requires external embeddings; validate and
repair require only local maintenance readiness. If multiple blockers apply,
return a deterministic typed reason with the safest actionable explanation.
Keep the existing reason vocabulary (`runtime_read_only`,
`bulk_reindex_disabled`, `external_embeddings_disabled`,
`snapshot_in_progress`).

**Verify**: add override-settings tests for every combination of local
maintenance, force reindex, and external embeddings; assert both reason and
enabled state.

### Step 2: Validate filter ranges and scope semantics

Parse dates once, reject `uploaded_after > uploaded_before` with a dedicated
typed reason (or the existing malformed-filter contract if that is the chosen
public vocabulary), and preserve empty-scope rejection. Add bounded limits for
submitted folder/PDF IDs before query construction while preserving normalized
deduplication.

**Verify**: tests cover inverted ranges, malformed dates, duplicate IDs,
non-numeric IDs, empty scopes, and a valid boundary where the two dates match.

### Step 3: Keep UI explanations aligned with server gates

For every disabled operation, render the typed reason once near the operation
and once near its preview control only if the second instance improves context.
Do not let the browser infer availability from flags; the server remains the
authority.

**Verify**: browser assertions confirm the disabled reason shown for the same
operation matches the API/read-model state.

## Test plan

Model tests after `MaintenancePlanningTests` in
`flowdocs/core/tests_maintenance_contract.py`. Add one browser assertion for
each operation's disabled reason in a safe local configuration and one enabled
configuration using test-only overrides.

## Done criteria

- [ ] No operation is enabled when any of its independent prerequisites are
  disabled.
- [ ] Inverted date ranges return a typed, non-mutating error.
- [ ] Selection IDs are bounded and normalized before database queries.
- [ ] Existing idempotency, stale-version, expiry, and authorization tests pass.
- [ ] Full Django and Workbench browser gates pass.

## STOP conditions

- The product intentionally permits local reindex without external embeddings;
  stop and document the alternate embedding contract before changing gates.
- A new public reason code would break audit consumers; stop and update the
  compatibility decision first.

## Maintenance notes

Keep the capability matrix in one server function. Any new maintenance
operation must declare prerequisites there and add a truth-table test before a
template card is added.
