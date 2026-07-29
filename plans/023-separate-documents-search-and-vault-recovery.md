# Plan 023: Separate Documents & Search from Vault & Recovery

**Status:** RECONCILE
**Priority:** P1
**Depends on:** 013–022
**Scope:** Dashboard and Vault Operations presentation only

## Problem

The Dashboard correctly identified local search-index debt but sent operators
into an eight-section specialist control plane. The ordinary maintenance path
began with remote authority evidence, while an empty but reachable Vault looked
like failed setup. This increased cognitive load without adding safety.

## Decision

- Present local validation, stored-index repair, and bounded reindexing as
  **Documents & Search**.
- Present publication, authoritative inventory, generations, restore,
  activation, retention, profiles, and recovery evidence as advanced
  **Vault & Recovery**.
- Deep-link Dashboard maintenance actions to the ordinary section.
- Collapse advanced Vault/runtime evidence on the ordinary path.
- Explain a configured Vault with no published generation as a first-run
  condition; preserve the existing backend verification rules.
- Review affected Marathi as authored operator copy, preferring precise literal
  Marathi and Marathi-script transliteration only for specialist terms.

## Acceptance gate

- Dashboard and section navigation distinguish the two journeys.
- Documents & Search provides an explicit validate-first sequence.
- Advanced evidence remains available without changing authority or state
  machines.
- Empty configured Vault copy explains probe versus inventory semantics.
- English and Marathi browser assertions, operator-language validation, locale
  compilation, Django checks, and responsive accessibility gates pass.
