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
the daily triage surface. **Documents & Search** is the ordinary maintenance
destination; **Vault & Recovery** is the specialist control plane.

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
Dashboard → Category Yard → reviewed PDF intake → grouped maintenance/indexing
→ Search readiness → public source-backed answers
```

Create or select the correct category, choose up to 50 approved PDFs, review
their generated titles, and select **Receive Selected Files**. Resolve any
individual rejection before selecting **Process Ready Documents**. The intake
then queues one grouped job while retaining per-document status. Wait for the
Searchable state before relying on a new document in search.

Use **Actions** for contextual recovery. **Remove from Search** asks why the
document should be hidden and preserves the PDF. **Retry Processing** queues
only the incomplete document. Permanent deletion is restricted to the
superadmin Danger Zone and is not the normal way to remove a document from
search. See [DOCUMENT_INTAKE_WORKBENCH.md](DOCUMENT_INTAKE_WORKBENCH.md).

The attention queue is ordered by operational impact. Category filters are
server-calculated and can be combined for readiness, provenance, occupancy, and
ordering. Clear the filters before assuming that a category is missing. Large
category sets are paginated; changing pages does not change document state.

## Operations and maintenance

Use the Dashboard's **Active Work** projection for quick triage, then open
**Documents & Search** for validation, repair, bounded reindexing, progress,
cancellation, retry, checkpoints, and failures. Dashboard links never queue
index mutations directly.

Superadmins use **Vault & Recovery** under Operations for remote Vault
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

Warnings and disabled controls now lead with plain-language guidance: what the
evidence means, what remains unavailable, and what to review next. Stable
support codes are not ordinary interface labels. Superadmins can expand
**Technical details** when a support or audit workflow needs the exact,
redacted code. Do not treat the code alone as remediation advice.

All critical Workbench actions are ordinary server-rendered forms and remain
available without JavaScript. Refresh the page to update evidence when
JavaScript is disabled. The browser receives only redacted profile and lease
evidence—never credentials, raw owner tokens, or raw object-store errors.

Vault, generation, settings, and data controls are superadmin-sensitive. Read
the deployment and data-custody runbooks before any restore or promotion.
Legacy Promote, Rollback, and Purge controls no longer relabel or delete data;
use the guarded Workbench flow. Never run production cleanup from a local
browser session.

Under **Documents & Search**, the guided sequence is:

```text
choose operation → define scope → preview → confirm → monitor job
→ review candidate → activate separately → publish separately
```

Validate and Repair Stored Indexes never call external embedding services.
Reindex Needed and Reindex Selected may call them; force reindexing requires
typed confirmation. The active runtime and remote Vault remain unchanged while
maintenance runs. A successful reindex creates a derived candidate and makes
the prior published generation stale.

Documents & Search is local maintenance and does not require a remote Vault
profile. If a control is disabled, read its adjacent guidance: local
maintenance policy, runtime read-only posture, external embedding policy, and
force-reindex approval are independent gates.

Scope defaults are shown beside each maintenance action. With no category
selected, **Validate files**, **Repair stored indexes**, and **Reindex only what
is needed** preview all eligible records (still bounded by the preview limit);
they do not start work until the preview is confirmed. **Reindex a selected
scope** never assumes all documents and requires an explicit category or
document scope. A filter that matches no eligible document produces an
informational no-match result and changes nothing.

Failed work appears under **Needs attention** with a plain-language cause,
consequence, next action, and collapsed technical evidence. Retry is offered
only when the operation is currently capable of running. If a signed source,
worker, embedding provider, or maintenance policy is still unavailable, resolve
that requirement first; the retry control becomes available after the server
can prove the prerequisite.

Category pages use those same server checks before presenting index actions. If
the active search source has not been verified, the page disables repair and
reprocessing, explains that no signed active generation is available, and links
to Restore/Activation guidance. This is not a missing API key or a failed PDF;
it prevents maintenance from deriving a candidate from unverified mounted
bytes. After a verified generation is activated, refresh the category page and
use the bounded preview flow. A direct or stale form submission is checked
again and redirects to the same recovery guidance instead of creating a job.
Recovery guidance opens the Data protection recovery-point register; routine
index guidance opens Search maintenance. Older bookmarked `?section=...` links
are translated to the same current task and retain any selected plan, job, or
profile context.

Advanced Vault and runtime evidence is collapsed on the Documents & Search
page. Expand it only when diagnosing publication or recovery; routine document
care does not require interpreting generation, lease, or authority identifiers.

Under **Vault & Recovery → Configuration → Vault profiles**, an
**Environment Vault** profile is
created automatically when the server has a complete `ARTIFACT_VAULT_*`
configuration. A **Legacy application database** record is historical
projection evidence, not an S3 connection; its probe and inventory controls
are disabled. A read-only probe confirms endpoint and bucket reachability.
Authoritative inventory verification additionally requires a published dataset
registration, pointer, manifest, and objects. A configured but empty Vault is
presented as a healthy first-run condition: storage access can be probed, while
authoritative inventory becomes meaningful only after the first publication.

The profile form never accepts secret values. Correct highlighted fields using
the server-approved endpoint, dataset identity, and credential alias supplied
by the deployment operator.

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
## When a document file is unavailable

Use **Mark unavailable** only after confirming that the preserved document row
does not currently have its approved source file. Expand the action, type
the expected SHA-256 and byte size from approved custody evidence when they are
known, choose a human-readable reason, add a bounded case reference, type
`MARK UNAVAILABLE`, and confirm. If definitive absence is known before the
exact evidence pair is available, leave both evidence fields blank. The record,
identifier, metadata, expected-media evidence, prior lifecycle, and
maintenance history remain preserved, but the document is excluded from search,
index work, and runtime readiness.

Archiving or deprecating a document does not authorize a missing file. Do not
edit the database lifecycle directly. Only **Mark unavailable** creates the
audited custody posture used by inventory, candidate, activation, rollback, and
recovery checks.

Recover the verified file to its approved storage location before choosing
**Restore availability**. If exact evidence was not recorded during quarantine,
first use **Bind recovery evidence** with approved custody evidence. The
application refuses restoration unless the path
is a regular non-symlink file, remains stable while read, and exactly matches
the recorded SHA-256 and byte size. A restored document returns to its prior
lifecycle; it is not automatically made searchable. After restoration, validate
the document and use the bounded
Documents & Search reindex workflow. Do not delete the row merely to clear a
readiness warning, and do not use a similarly named file unless its identity
and provenance are verified.
