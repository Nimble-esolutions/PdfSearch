# AI Sahakar Admin User Guide

**Status:** Active
**Audience:** Authorized administrators and superadmins
**Last verified:** 2026-07-28
**Related:** [`CLIENT_USER_MANUAL.md`](CLIENT_USER_MANUAL.md), [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md)

## What the console is for

The admin console maintains the official documents used by AI Sahakar. It is
an operations workspace: inspect readiness, manage categories and PDFs, queue
maintenance, and review role-appropriate system controls. It is not a public
search page and it does not replace source review.

The current composition is the **Operations Cockpit**: official header, dark
console navigation, an observed operational posture, prioritized attention,
Category Yard, Recent Intake, Active Work, and a compact Vault posture. It is
the daily triage surface; the Vault Operations Workbench remains the specialist
control plane.

## Sign in and navigation

1. Open the organization-provided URL and choose Login.
2. Sign in with the account issued by an administrator.
3. Use Dashboard for the cockpit, Search for public document questions, and
   Users/Operations/Vault/Settings only when your role exposes them.
4. Use the language selector when available; do not rely on browser translation
   for legal or administrative wording.
5. Log out on shared devices.

Admin, superadmin, and ordinary authenticated-user actions remain controlled by
the existing Django permissions. A missing link is normally a permission
boundary, not a broken visual control.

## Daily document workflow

The operational flow is:

```text
Dashboard → Category Yard → PDF intake → queued maintenance/indexing
→ Search readiness → public source-backed answers
```

Create or select the correct category, upload an approved PDF, and wait for its
processing/indexing state before relying on search. Use descriptive titles and
retain the original official file through the approved document policy.

The attention queue is ordered by operational impact. Category filters are
server-calculated and can be combined for readiness, provenance, occupancy, and
ordering. Clear the filters before assuming that a category is missing. Large
category sets are paginated; changing pages does not change document state.

## Operations and maintenance

Use the Dashboard's **Active Work** projection for quick triage, then open the
Workbench for full progress, cancellation, retry, checkpoints, failures, and
candidate evidence. Dashboard links never queue index mutations directly.

Superadmins use the **Vault Operations Workbench** under Operations for vault
and runtime custody. Its persistent summary deliberately shows remote
authoritative generation, locally prepared workspace, runtime generation,
Active Sync, writer lease, and critical job as independent evidence. Unknown
or different values are warnings; the newest generation is never assumed to
be authoritative.

Active Sync creates an immutable candidate without moving the authoritative
pointer. Promotion and staging activation require a fresh, one-use typed
confirmation bound to the observed state. Restore downloads into quarantine
and prepares an immutable runtime workspace; it never activates automatically.
Production activation and garbage collection remain hard-disabled.

The signed rollback control is always visible in the Restore section. It is
enabled only when the current signed runtime is a verified local-maintenance
child of the exact signed previous runtime. A disabled control shows a typed
reason, such as a missing or unverified pointer, stale runtime observation,
invalid lineage, unavailable recovery login, or activation already in
progress. Rollback schedules the same signed supervisor protocol as activation
and does not change remote Vault authority.

All critical Workbench actions are ordinary server-rendered forms and remain
available without JavaScript. Refresh the page to update evidence when
JavaScript is disabled. The browser receives only redacted profile and lease
evidence—never credentials, raw owner tokens, or raw object-store errors.

Vault, generation, settings, and data controls are superadmin-sensitive. Read
the deployment and data-custody runbooks before any restore or promotion.
Legacy Promote, Rollback, and Purge controls no longer relabel or delete data;
use the guarded Workbench flow. Never run production cleanup from a local
browser session.

Under **Documents & Indexes**, the guided sequence is:

```text
choose operation → define scope → preview → confirm → monitor job
→ review candidate → activate separately → publish separately
```

Validate and Repair Stored Indexes never call external embedding services.
Reindex Needed and Reindex Selected may call them; force reindexing requires
typed confirmation. The active runtime and remote Vault remain unchanged while
maintenance runs. A successful reindex creates a derived candidate and makes
the prior published generation stale.

## Destructive actions and support

Renaming or deleting a category can affect every PDF inside it. Confirm the
backup and rollback evidence before proceeding. If readiness or source counts
look wrong, record the visible category/document, action, approximate time,
role, and error message; never include passwords, tokens, database paths, or
private object-store keys.

For a source or answer problem, verify the PDF itself, its processing state,
and the cited page before reporting a defect. AI explanations are assistance;
the official PDF and department support remain authoritative.

## Design and accessibility expectations

The console deliberately uses restrained cards, dividers, high-contrast status
labels, clear breadcrumbs, and role-aware actions. It must remain usable by
keyboard, at mobile/tablet widths, and in English/Marathi. Do not reintroduce
the competing mastheads, cramped admin links, tiny status text, or decorative
AI imagery described in the historical SRS.

See the [operator workflow diagram](diagrams/admin-operator-workflow.mmd) and
the [active UI contract](design/AI_SAHAKAR_UI_CONTRACT.md) for the protected
visual and interaction rules.
