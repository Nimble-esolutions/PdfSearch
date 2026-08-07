# Plan 002: Reconcile the runtime configuration registry and settings center

> **Executor instructions**: Most of this plan has been implemented. Verify the
> current registry and UI before editing. Close only confirmed residual gaps;
> do not build a second settings system.

## Status

- **Priority**: P1
- **Effort**: S/M
- **Risk**: MED
- **Depends on**: none
- **Category**: configuration / DX
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: RECONCILE

## Relationship to Plan 034

This plan remains the technical reconciliation contract for the typed registry,
source precedence, redaction, and runtime-setting safety. The dedicated
operator-experience redesign, including scoped errors, change review,
optimistic concurrency, information architecture, and visual refinement, is
now tracked in [Plan 034](034-settings-control-desk.md). Do not duplicate its
UI work here or use it to create a parallel settings abstraction.

## Drift check

Run:

```bash
git diff --stat f742b59..HEAD -- \
  flowdocs/core/configuration_registry.py flowdocs/core/models.py \
  flowdocs/core/views.py flowdocs/core/templates/settings.html \
  flowdocs/core/tests.py flowdocs/flowdocs/settings.py
```

If these files changed, compare this plan to the live implementation before
proceeding.

## Historical defect and current evidence

- `flowdocs/core/configuration_registry.py:9-97` now defines typed
  configuration definitions, display redaction, and grouped inventory output.
- `flowdocs/core/models.py:325-351` provides the database-backed `SiteSetting`
  projection.
- `flowdocs/core/views.py:1795-1893` renders vault/configuration state and
  persists named runtime settings.
- `flowdocs/core/tests.py:2610-2635` covers feature flags, safe inventory, and
  runtime persistence.

The original defect is therefore substantially implemented. Remaining work is
verification of complete key coverage, explicit precedence, false/zero/empty
semantics, restart requirements, optimistic concurrency, and audit evidence.

Impact: operators cannot distinguish editable runtime controls from deployment configuration, may believe a change is active when it is not, and receive misleading infrastructure health.

## Target architecture

Create a declarative, typed configuration registry in an application-owned module. Each entry should define key, type, safe display policy, source precedence, editable scope, restart requirement, validation, redaction, and consumer. Split the settings page into:

1. Environment facts: deployment, build, instance, data mode, release, and read-only source.
2. Runtime controls: explicitly supported database-backed feature flags and rates.
3. Search policy: limits and folder visibility, with dangerous exposure controls gated and audited.
4. Infrastructure: Redis, vault, indexes, and backup capability, represented as health facts rather than editable secrets.
5. Security and delivery: CSRF/CORS, session, email, and upload posture, read-only with safe summaries.

Use a resolver that preserves `False`, `0`, and empty values intentionally; report effective value, source, validation status, and restart requirement. Never return secret values. A save should either persist a supported runtime setting and re-read it, or clearly say that a restart/deployment is required.

## Reconciliation steps

1. Run the focused tests and inspect rendered groups. Record implemented,
   missing, and intentionally deployment-only keys.
2. Add characterization tests for `False`, `0`, empty string, invalid integer,
   source precedence, redaction, and startup-only settings.
3. If a test fails, patch the existing registry/resolver; do not create a
   parallel configuration abstraction.
4. Verify stale concurrent saves cannot silently overwrite an operator change.
   If versioning is absent, add the smallest model/form contract and audit event.
5. Update environment/configuration docs from the same registry or add a drift
   test; do not duplicate hand-maintained key lists.

## Commands and scope

```bash
python manage.py test \
  core.tests.LegalPageTests core.tests.EnvironmentContractTests
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

In scope: `configuration_registry.py`, `SiteSetting`, settings views/template,
focused tests, and configuration docs. Out of scope: secret values, production
environment mutation, unrelated search/index behavior, and protected lifecycle
modules.

## Tests and done criteria

- Every settings key in the supported inventory is either rendered or explicitly classified as deployment-only.
- False/zero values survive resolution; precedence and restart semantics are tested.
- Secrets are redacted in HTML, JSON, logs, and error responses.
- Invalid values do not persist; concurrent stale saves are rejected.
- A settings change reports its actual effective state.
- Existing settings, dashboard access, and role restrictions remain green.

## STOP conditions

Stop if a setting is consumed only at Django import/startup and the proposed UI
would claim immediate activation; if a secret would need to be displayed; if
the registry and deployment documentation disagree on a security-sensitive
default; or if the fix requires changing protected backend files.
