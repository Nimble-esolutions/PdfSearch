# Users & Access Workspace

## Current release

The Users & Access workspace is an authenticated operator surface. It provides
search and filtering by username/email, role, department, and status; account
summary metrics; account creation; access-detail editing; and protected
activation/deactivation actions.

Public search is intentionally separate from account administration. `/register/`
is never public and requires an authenticated `admin` or `superadmin`.

## Role safeguards

- `superadmin` can grant `superadmin` access.
- `admin` can create ordinary users and admins, but cannot grant superadmin.
- A user cannot deactivate their own account.
- The last active superadmin cannot be deactivated.
- Admins cannot edit or deactivate superadmin accounts.
- Destructive deletion remains blocked when the account owns folders or PDFs.

## Phase 2 boundary

Department values remain a compatibility field in the current release. They are
organizational metadata, not an authorization boundary. Department-scoped access
requires a reviewed `Department`, `Role`, `Permission`, and membership model,
with server-side policy checks for folders, PDFs, search scope, and maintenance
operations. Do not infer permissions from the current department string or
expand the UI to imply that it already limits data access.

## UX contract

The workspace is optimized for repeated operator work: summary metrics first,
filterable directory second, and explicit row actions last. Role/status meaning
must remain visible without relying on color alone. New strings must be added to
the Marathi catalog and tested with the English-default admin language flow.
