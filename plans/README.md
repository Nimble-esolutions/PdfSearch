# PdfSearch Improvement Plans

These plans are the durable implementation brief for future agents. They supersede the partially implemented ideas in `docs/ADMIN_UI_REDESIGN_SRS.md` where the two conflict.

## Baseline

- Baseline commit: `d3fc328` (`docs(operations): define Dokploy data persistence contract`)
- Branch for this planning pass: `docs/dokploy-data-persistence`
- Scope: preserve the Civic Knowledge Workbench and operations contract while
  planning durable data custody, retrieval normalization, and platform evolution.
- Application code is unchanged by this planning pass; only planning documents
  are added or updated.

## Delivery order

1. `001-registrar-domain-brief.md` — DONE: domain vocabulary, identity, and content provenance.
2. `002-runtime-config-and-settings-center.md` — TODO, P1: typed configuration inventory and safe settings UX.
3. `003-vault-operational-state-and-lifecycle.md` — TODO, P1: truthful vault health and operational workflows.
4. `004-breadcrumb-dashboard-category-workbench.md` — TODO, P1: route-aware navigation and dashboard/category workbench.
5. `005-hallmark-public-search-redesign.md` — TODO, P1: preserve the Hallmark visual direction while rebuilding the search experience.
6. `006-cross-surface-verification.md` — TODO, P1: automated and visual release gates.
7. `007-search-reference-audit.md` — existing plan: source-reference parity and validation.
8. `011-immutable-evidence-pack-foundation.md` — TODO, P1: make document custody and reconciliation authoritative before changing databases.
9. `012-compatibility-and-agent-capability-contracts.md` — TODO, P1: relax internal coupling while preserving Django, the UI, and the public API adapter.
10. `008-postgres-object-storage-migration.md` — TODO, P1: move custody to object storage and conditionally migrate relational state to PostgreSQL.
11. `009-search-domain-and-index-normalization.md` — TODO, P1: benchmark hybrid retrieval and normalize documents/chunks/embeddings/index generations.
12. `010-reliable-civic-ai-platform-architecture.md` — TODO, P2: evolve the modular monolith, worker, observability, security, and cost posture.

Plans 002–004 should land as small cherry-pickable PRs. Plan 005 may proceed in parallel after the domain brief, but its backend/API contract must be agreed before visual work. Plan 006 gates merging.

Plans 008–012 are future architecture work and must not be started as one
large rewrite. Plan 011 establishes evidence custody first. Plan 012 defines
compatibility seams and agent-safe capabilities. Plan 008 then separates
object custody and makes PostgreSQL an evidence-based decision. Plan 009
normalizes retrieval and adds lexical/semantic/provenance checks. Plan 010
consolidates the resulting boundaries and operational evidence.

## Non-negotiable constraints

- Preserve the current Hallmark header, maroon navigation, CC identity mark, typography direction, and information hierarchy unless a measurable improvement is demonstrated.
- Purge competing/legacy search themes only after the canonical Hallmark search has feature parity and responsive coverage.
- Never display secrets, raw `.env` values, tokens, or connection credentials.
- Do not claim an action succeeded until the backend has completed and returned a persisted result.
- Every route must have a breadcrumb or an intentional documented exception.
- Backend changes must be additive/refactoring-oriented and covered by tests; no deletion of working capabilities.
- Branch, commit, verify, push, and open PR. Do not merge without explicit operator approval.
