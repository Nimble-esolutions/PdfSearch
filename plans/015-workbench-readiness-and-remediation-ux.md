# Plan 015: Make Workbench readiness failures actionable

> **Executor instructions**: This plan improves operator guidance without
> weakening safety gates. Do not enable remote publication or production
> activation as a side effect.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: 013, 014
- **Category**: bug
- **Planned at**: commit `6c9a262`, 2026-07-28
- **Implementation**: DONE — succeeded by Plans 017–022, which completed
  operator-language presentation, redaction, Marathi parity, and enforcement.

## Why this matters

The live Workbench reports `profile_unavailable`, unknown remote/runtime
authority, disabled admin mutations, and disabled garbage collection. Those are
valid local-dev safety states, but the page only says “Configure or materialize
the locked profile” without linking to the exact configuration surface or
explaining which operation is safe to perform next. Operators cannot tell
whether the environment is intentionally read-only or simply incomplete.

## Current state

- `flowdocs/core/templates/vaultops/workbench.html:55-89` shows the evidence
  alert and summary status, but the alert has no remediation link.
- `flowdocs/core/templates/vaultops/workbench.html:95-118` repeats health data
  in a collapsed disclosure, leaving critical recovery/capacity posture hidden
  by default.
- `flowdocs/core/maintenance_plans.py:90-122` emits typed capability reasons,
  while `docker-compose.dev.yml:57-59` defaults local maintenance, force
  reindex, and external embeddings to disabled.
- `flowdocs/vaultops/views.py:324-349` builds the state/API envelope and already
  carries a recommended-action field that can be reused for remediation.

## Commands you will need

| Purpose | Command | Expected result |
|---|---|---|
| Workbench tests | `docker compose -f docker-compose.dev.yml exec web python flowdocs/manage.py test flowdocs.vaultops.tests.test_workbench flowdocs.core.tests_maintenance_contract` | all pass |
| Browser review | `npx playwright test browser_tests/vault-workbench.spec.ts` | all pass |
| Compose verification | `PDFSEARCH_IMAGE=pdfsearch-web REDIS_IMAGE=redis:7-alpine bash scripts/ci/run_compose_smoke.sh` | smoke gate passes |

## Scope

**In scope**: Workbench read-model fields, remediation links/copy, configuration
page guidance, health disclosure defaults, tests, and operator documentation.

**Out of scope**: changing default safety flags, creating credentials, remote
Vault connectivity, or automatically activating candidates.

## Steps

### Step 1: Model remediation as typed, role-aware actions

For `profile_unavailable`, missing local recovery capacity, runtime read-only,
and external embedding disabled, include a stable action code, label, URL, and
whether the action is informational or mutating. Link profile/configuration
issues to Settings; link recovery/capacity issues to Documents & Indexes health;
link runtime authority issues to the appropriate read-only evidence section.

**Verify**: read-model tests assert every degraded reason has a non-empty
explanation and a safe destination; no action is offered to a non-superadmin.

### Step 2: Improve the visible posture contract

Keep the evidence alert visible, add the remediation action, and expose a short
“why disabled / what is safe now” summary without requiring a deep scroll. Keep
the detailed health facts in the disclosure, but make its summary announce
whether recovery is healthy, degraded, or unknown.

**Verify**: browser snapshots find the reason, explanation, destination, and
safe-next-step copy in English and Marathi; Axe and keyboard checks pass.

### Step 3: Align local-dev configuration guidance

Document the default disabled flags as intentional. On Settings, show the
current posture and prerequisites without rendering secret values. Add a
read-only “verify prerequisites” action if the existing settings flow supports
it; otherwise provide a command/documentation link and do not invent a mutation.

**Verify**: docs checks and Workbench browser tests confirm no secret values or
production endpoints appear in the rendered page.

## Test plan

Extend `flowdocs/vaultops/tests/test_workbench.py` with one test per degraded
reason and authorization role. Extend `browser_tests/vault-workbench.spec.ts`
with a degraded local posture assertion and a healthy test-fixture posture.

## Done criteria

- Historical acceptance checklist: these conditions were completed by Plans
  017–023. The checked items record completion; they are not an active backlog.

- [x] `profile_unavailable` and other degraded states have actionable,
  safe-next-step destinations.
- [x] Disabled flags are explained as posture, not presented as unexplained dead
  buttons.
- [x] Health visibility improves without exposing secrets or enabling mutations.
- [x] English/Marathi, Axe, browser, and Workbench tests pass.

## STOP conditions

- The remediation would require creating or rotating a credential; stop and
  request operator-managed configuration.
- A destination would cross from local maintenance into remote publication;
  preserve the explicit authority boundary and stop.

## Maintenance notes

Every new typed degraded state must define an operator explanation and a safe
destination before it is allowed into the Workbench envelope.
# Successor note

At this plan's original completion point, readiness structure had improved but
presentation quality was still incomplete: raw machine reasons and state tokens
remained visible in multiple Workbench sections. Plan 017 superseded that
operator-language portion, and Plans 018–022 later closed it, while preserving
this plan's backend readiness and remediation work.
