Status: Resolved documentation incident; implementation cleanup remains tracked
Audience: Maintainer, Operator, Developer, Reviewer, AI agent
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical current state: [HANDOFF.md](../HANDOFF.md)

# VaultOps/DataOps documentation drift

## Incident

The DataOps v3 product cutover retired the VaultOps operator workbench, but it
did not delete the `vaultops` Django app, control schema, authenticated internal
API, selected search-maintenance handlers, or signed activation/runtime bridge.
Several documents and diagrams used “replaced” or “removed” without naming the
boundary. Readers could therefore infer either of two false states:

- VaultOps was completely deleted; or
- VaultOps remained the supported backup/restore operator product.

At the same time, dated stage evidence and v2 profile choreography remained in
active architecture, environment, deployment, and recovery pages.

## Root causes

| Cause | How drift appeared |
| --- | --- |
| Product and implementation boundaries shared one name | “DataOps replaced VaultOps” was copied as a package-deletion claim even though the cutover applied only to operator UI and normal workflow ownership. |
| Historical execution records were indexed as current instructions | Pending PR #176 gates, empty-stage posture, July counts, and pre-activation evidence contradicted the living handoff. |
| Moving evidence was copied into stable architecture | Branch SHAs, stage counts, and temporary rollout state aged immediately. |
| V2 and v3 configuration were documented together | Operators were asked to assemble source/destination profiles even though v3 derives same-dataset restore versus foreign import from provenance. |
| Diagram review checked syntax, not semantics | Valid Mermaid still showed obsolete Vault workbench names, symlink activation, missing control volumes, and stale release identities. |
| Documentation CI covered too little semantic truth | Link, shell, and Mermaid checks passed while active operator terms and implementation boundaries were wrong. |

## Correct truth matrix

| Boundary | Current truth |
| --- | --- |
| Operator product | DataOps v3 is the only supported Data protection workbench for backup, import/rebind, restore candidates, and recovery points. |
| Search repair | Search maintenance is a separate local document/index journey. |
| Storage | DataOps v3 reads/writes immutable recovery points directly through an environment-owned RustFS connection. |
| Routing | Same dataset selects restore; foreign provenance selects import/rebind. Operators do not choose ordinary source/destination profiles. |
| VaultOps code | Still installed as internal compatibility schema/API, selected maintenance surface, and signed staging activation/runtime bridge. |
| Activation | Signed JSON runtime pointers support staging. Production activation is currently rejected by code and needs a future certified implementation. |
| Historical Vault docs | Preserved only as explicitly labeled design/migration evidence with links to DataOps v3 and the living handoff. |

## Impact

- Operators could search for screens and profile settings that no longer exist.
- Environment setup appeared much more complex than the v3 runtime contract.
- Maintainers could delete still-required VaultOps activation/runtime primitives
  under the mistaken belief that the package was dead.
- Production activation could be planned from documentation even though the
  runtime rejects it.
- Stage rollout claims could be attached to a newer repository revision that
  was not yet deployed.
- Stale symlink diagrams could lead incident responders to inspect or mutate
  paths that are no longer authoritative.

No runtime data was changed by this incident. The risk was incorrect operator
action and unsafe future maintenance.

## Corrections

- Re-established DataOps v3 as the product boundary everywhere current.
- Described retained VaultOps components as internal compatibility, never as a
  second workbench and never as deleted code.
- Replaced profile-driven v2 examples with one owned v3 connection and automatic
  provenance routing; retained old selectors only in compatibility inventories.
- Corrected staging-only activation, JSON pointer, quarantine, control-volume,
  web/maintenance, and RustFS diagrams.
- Moved mutable release/deployment evidence back to `HANDOFF.md` and labeled
  dated status, migration, release, and Vault documents as historical.
- Added semantic documentation checks for obsolete current operator language
  and required implementation-boundary language.

## Prevention rules

1. Current operator docs use **Data protection** and **Search maintenance**.
2. A current doc may mention VaultOps only when it says **internal
   compatibility**, **historical**, or an equally explicit implementation
   boundary.
3. Stable architecture never hard-codes a moving branch head or temporary stage
   count; it links to `HANDOFF.md`.
4. A historical page states its observation date and current replacement.
5. Diagrams are reviewed for ownership, persistence, activation, network, and
   release-identity semantics in addition to Mermaid compilation.
6. “Removed” names the exact surface: route, UI, setting, model, migration, API,
   or package.
7. Production capability claims must match fail-closed code paths, not target
   architecture.

## Follow-up implementation debt

The authenticated VaultOps API, duplicate models/settings, scheduler paths, and
activation dependency may be removed only through a separate impact-analysis
change that migrates every caller, database history, worker path, and recovery
test. Documentation cleanup is not authorization to delete them.
