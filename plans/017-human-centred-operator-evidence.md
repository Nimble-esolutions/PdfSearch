# Plan 017 — Human-centred operator evidence

**Status:** RECONCILE — implementation exists; Plans 018-021 close audited gaps
**Boundary:** Dashboard and Vault Operations Workbench
**Backend impact:** Additive presentation only; stable reason and state values unchanged

## Decision

Machine codes remain durable API, audit, retry, and support evidence. Operator
surfaces lead with authored meaning, consequence, and next action. Authorized
superadmins can reveal bounded codes in collapsed Technical details.

## Delivery

- Central registry and safe unknown fallback in
  `core.operator_presentation`.
- Shared Django technical-evidence component with Marathi-safe LTR codes.
- Dashboard hierarchy without duplicated codes or empty-category escalation.
- Human labels for Workbench states, operations, jobs, audit events,
  protection reasons, safe errors, retention holds, and disabled rollback.
- CI template/source validator and shared Playwright visible-token helper.
- English/Marathi, accessibility, responsive, no-JavaScript, and technical
  evidence verification.

Plan 015 remains authoritative for readiness computation. This plan supersedes
only its incomplete presentation-quality outcome. The 2026-07-28 completion
audit found incomplete known-reason coverage, raw maintenance exception text
entering expanded evidence, broad English fallback in Marathi, and incomplete
rendered/accessibility enforcement. Do not mark this plan DONE until Plans
018-021 pass and Plan 022 reconciles the stack.
