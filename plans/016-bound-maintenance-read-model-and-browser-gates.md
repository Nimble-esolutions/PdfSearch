# Plan 016: Bound maintenance read models and verify the full workflow

> **Executor instructions**: Treat this as a performance and verification plan;
> do not change data semantics without adding characterization tests.

## Status

- **Roadmap status**: DONE in PR #102; lifecycle evidence verified 2026-07-28
- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: 014, 015
- **Category**: perf
- **Planned at**: commit `6c9a262`, 2026-07-28

The implementation bounds preview materialization and the disposable lifecycle
now proves preview, queue, progress, failure/retry, candidate preparation,
signed activation, English/Marathi search, rollback, and content custody. Keep
this plan as the maintenance contract; do not execute it again.

## Why this matters

The Workbench read model performs filesystem inventory, cleanup planning,
recovery-set listing, capacity checks, job listing, and plan listing during page
render. Maintenance previews also materialize every matching PDF ID into a
JSON-backed plan, and estimate work as only `pdfs + folders`. Small local data
made this appear fine; a real catalogue will make the page slow and can make the
control database payload large before any job starts.

## Current state

- `flowdocs/core/maintenance_plans.py:354-454` calls `list_sets`, `plan_prune`,
  `cleanup_plan`, `inventory_local_artifacts`, and `capacity_report` in the
  Workbench state path, then lists up to 20 jobs/plans.
- `flowdocs/core/maintenance_plans.py:219-245` builds an unbounded `pdf_ids`
  list and counts work as `len(pdf_ids) + len(folder_ids)`.
- `flowdocs/core/maintenance_plans.py:248-285` persists the complete preview
  and selection in `MaintenancePlan` before queueing.
- Existing browser coverage in
  `browser_tests/operations-cockpit.spec.ts` and
  `browser_tests/vault-workbench.spec.ts` checks presence and accessibility but
  not latency, bounded payloads, or the complete preview → queue → progress →
  retry/cancel → candidate flow.

## Commands you will need

| Purpose | Command | Expected result |
|---|---|---|
| Performance tests | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test flowdocs.core.tests_maintenance_contract` | all tests pass |
| Full suite | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test` | all tests pass |
| Browser workflow | `npx playwright test browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts` | all tests pass |
| Smoke gate | `PDFSEARCH_IMAGE=pdfsearch-web REDIS_IMAGE=redis:7-alpine bash scripts/ci/run_compose_smoke.sh` | smoke gate passes |

## Scope

**In scope**: maintenance read-model boundaries, preview representation,
capacity/health refresh strategy, timing instrumentation, integration/browser
tests, and documentation.

**Out of scope**: changing worker extraction/index algorithms, replacing the
queue, changing SQLite schema unless a measured bound requires a migration, or
adding a client-side framework.

## Steps

### Step 1: Establish characterization and timing baselines

Add tests/diagnostics that record query count, filesystem scan duration, response
payload size, and preview materialization size for empty, small, and large
fixtures. Keep secrets and document contents out of diagnostics.

**Verify**: baseline tests pass and report bounded numeric metrics without
  relying on wall-clock thresholds that are flaky in CI.

### Step 2: Bound preview and health work

Store a bounded selection digest plus server-side query criteria rather than an
unbounded PDF-ID payload, or introduce a deliberate maximum with a typed
“scope_too_large” preview error. Move expensive health/inventory work behind an
explicit refresh or short-lived cached read model while preserving fresh state
checks immediately before queueing.

**Verify**: large-fixture tests prove bounded plan size and that queue-time
  recalculation still rejects changed data/runtime authority.

### Step 3: Add end-to-end browser workflow coverage

Using a disposable fixture, drive filtered preview, disabled/enabled operation
states, confirmation, queueing, progress, cancellation, retry from checkpoint,
activation-ready candidate evidence, and the explicit “publish a new Vault
generation” boundary. Add a test for inverted date filters and duplicate
submissions.

**Verify**: both desktop and mobile browser specs pass; no serious Axe issues,
overflow, or unbounded request payloads are observed.

## Done criteria

- [ ] Workbench render and preview payloads have measured bounds.
- [ ] Fresh queue-time validation remains authoritative.
- [ ] End-to-end local maintenance workflow is browser-tested, including failure
  and retry lifecycle.
- [ ] Full Django and Compose smoke gates pass.

## STOP conditions

- Bounding the selection requires a schema migration or changes audit semantics;
  stop and split that into a new migration plan.
- A timing threshold fails only under CI contention; record the measurement and
  choose a deterministic bound instead of weakening safety checks.

## Maintenance notes

Keep health scans and queue-time validation separate. Any new health signal must
declare whether it is cached, observed-at render time, or authoritative at
mutation time.
