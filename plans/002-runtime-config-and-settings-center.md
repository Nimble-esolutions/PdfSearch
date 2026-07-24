# Runtime Configuration and Settings Center

## Evidence and defect

- `flowdocs/core/views.py:1733-1776` exposes only nine allowlisted settings and resolves values with `db_value or env_value`, which loses intentional false/empty values and does not explain precedence.
- `flowdocs/flowdocs/settings.py:204-286` defines substantially more configuration: Redis, search limits, folder scope, cache, CSRF/CORS, upload limits, PDF/embedding parameters, sessions/security, email, vector/index paths, backup paths, and environment identity.
- `flowdocs/core/views.py:1831-1848` says settings take effect immediately, although many Django settings are startup-only.
- The settings vault status currently infers reachability and bucket existence from `env_identity.is_backup_writer` rather than executing a real capability probe.

Impact: operators cannot distinguish editable runtime controls from deployment configuration, may believe a change is active when it is not, and receive misleading infrastructure health.

## Target architecture

Create a declarative, typed configuration registry in an application-owned module. Each entry should define key, type, safe display policy, source precedence, editable scope, restart requirement, validation, redaction, and consumer. Split the settings page into:

1. Environment facts: deployment, build, instance, data mode, release, and read-only source.
2. Runtime controls: explicitly supported database-backed feature flags and rates.
3. Search policy: limits and folder visibility, with dangerous exposure controls gated and audited.
4. Infrastructure: Redis, vault, indexes, and backup capability, represented as health facts rather than editable secrets.
5. Security and delivery: CSRF/CORS, session, email, and upload posture, read-only with safe summaries.

Use a resolver that preserves `False`, `0`, and empty values intentionally; report effective value, source, validation status, and restart requirement. Never return secret values. A save should either persist a supported runtime setting and re-read it, or clearly say that a restart/deployment is required.

## Implementation steps

1. Inventory every supported env/settings key and classify it; add regression tests for omissions and redaction.
2. Introduce typed resolver/registry APIs without changing existing consumers.
3. Refactor current settings view to consume the registry and return structured metadata.
4. Add validation, optimistic concurrency/versioning, audit event, and post-save effective-state response for runtime settings.
5. Update the existing Hallmark settings template to show grouped rows, source badges, restart badges, safe values, and actionable validation errors.
6. Add an operator-facing “configuration health” summary with links to the relevant operational page.

## Tests and done criteria

- Every settings key in the supported inventory is either rendered or explicitly classified as deployment-only.
- False/zero values survive resolution; precedence and restart semantics are tested.
- Secrets are redacted in HTML, JSON, logs, and error responses.
- Invalid values do not persist; concurrent stale saves are rejected.
- A settings change reports its actual effective state.
- Existing settings, dashboard access, and role restrictions remain green.
