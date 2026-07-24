# Vault and Operations: Truthful State and Safe Actions

## Evidence and defect

- `flowdocs/core/views.py:1689-1730` reduces vault state to `vault_healthy` plus a truncated error after listing manifests; it does not expose separate enabled, configured, reachable, bucket, credentials, manifest, or freshness states.
- The settings page currently synthesizes reachability and bucket existence from backup-writer identity instead of a real probe.
- `dashboard_s3ops.html` mixes status, sync, restore, generation, and lease actions in a long page with generic card styling, making it unclear what is safe, pending, failed, or complete.
- Existing `flowdocs/core/artifact_vault.py` already contains the service boundary, config, manifest listing, validation, and capability helpers. Reuse it instead of creating template-level clients.

Impact: operators cannot trust the status, cannot predict action consequences, and may repeat or abandon long-running operations because the UI lacks durable job state.

## Target architecture

Define a typed `VaultHealth` result with independent fields: enabled, configuration-valid, endpoint-reachable, bucket-present, credentials-valid, manifest-readable, last-success, last-error, checked-at, and capability flags. Probe with bounded timeout, no secret leakage, and explicit unavailable/unknown states.

Model maintenance actions as idempotent jobs with request id, actor, operation, queued/running/succeeded/failed/cancelled state, timestamps, safe summary, and retry policy. The UI should show current job state and poll or refresh with backoff. Destructive restore/purge actions require explicit confirmation and display the target generation, scope, and rollback path.

## Implementation steps

1. Verify whether the prior generation fencing and vault health branches are present in the target branch; treat them as prerequisites if not.
2. Add service-level health probing with a small timeout and typed result; unit-test disabled, missing config, unreachable, forbidden, missing bucket, and success states.
3. Refactor views to return health and job summaries, never raw client errors or credentials.
4. Add durable/idempotent action handling for sync, restore, generation promotion, purge, and lease recovery; preserve existing endpoints while improving response semantics.
5. Recompose the existing Hallmark vault page into: health summary, active job, safe primary action, generation inventory, restore/promotion controls, and recent audit events.
6. Add clear empty, loading, error, retry, disabled, and success states. Keep all current operations available.

## Tests and done criteria

- Health badges correspond to real probes and distinguish unknown from false.
- Repeating the same action does not duplicate work; retries are bounded and observable.
- Promotion/restore fencing and writer lease release are race-tested.
- No action reports success before persistence completes.
- Role/CSRF checks, audit events, and existing endpoint compatibility remain green.
