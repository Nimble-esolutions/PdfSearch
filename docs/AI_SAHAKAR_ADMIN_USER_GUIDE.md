# AI Sahakar Admin User Guide

**Status:** Active
**Audience:** Authorized administrators and superadmins
**Last verified:** 2026-07-25
**Related:** [`CLIENT_USER_MANUAL.md`](CLIENT_USER_MANUAL.md), [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md)

## What the console is for

The admin console maintains the official documents used by AI Sahakar. It is
an operations workspace: inspect readiness, manage categories and PDFs, queue
maintenance, and review role-appropriate system controls. It is not a public
search page and it does not replace source review.

The current composition is the **Operations Cockpit**: official header, dark
console navigation, operational metrics, Category Yard, Recent Intake, and a
right-side action rail.

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

## Operations and maintenance

Use Operations and Maintenance Jobs to inspect queued, active, failed, or
retryable work. Bulk indexing and OCR repair are durable maintenance jobs; do
not turn them into repeated browser submissions. Preserve cancellation,
retry, and per-item failure evidence.

Vault, generation, settings, and data controls are superadmin-sensitive. Read
the deployment and data-custody runbooks before any restore, promotion, or
destructive action. Never run production cleanup from a local browser session.

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
