"""Human-centred presentation for stable operator evidence tokens.

Backend codes remain authoritative.  This module is the only boundary that
turns them into user-facing language for Dashboard and Workbench templates.
"""

from copy import deepcopy

from django.urls import reverse
from django.utils.translation import gettext, gettext_noop


UNKNOWN_REASON = {
    "title": "Additional technical evidence requires review.",
    "detail": "The system recorded evidence that does not yet have approved operator guidance.",
    "consequence": "Do not infer a recovery action from the technical code alone.",
    "action_label": "Review operations evidence",
    "section": "jobs",
    "severity": "warning",
}


REASONS = {
    "operations_ready": (
        "Operations are ready",
        "No immediate document operations need attention.",
        "Routine work can continue.",
        "Review operations",
        "overview",
        "success",
    ),
    "maintenance_job_failed": (
        "Maintenance work needs review",
        "A local maintenance job did not complete.",
        "The active runtime remains unchanged, but the requested work is incomplete.",
        "Review jobs",
        "jobs",
        "danger",
    ),
    "index_debt_present": (
        "Documents need index review",
        "Some documents are not available to search yet.",
        "Search results may omit those documents.",
        "Maintain documents and indexes",
        "maintenance",
        "warning",
    ),
    "provenance_review_required": (
        "Document provenance needs review",
        "Some documents do not have an accountable uploader recorded.",
        "Their custody record remains incomplete.",
        "Review categories",
        "dashboard",
        "warning",
    ),
    "maintenance_in_progress": (
        "Maintenance is in progress",
        "A bounded maintenance job is still running.",
        "The active runtime remains unchanged while work continues.",
        "View active work",
        "maintenance",
        "info",
    ),
    "empty_categories_present": (
        "Categories are awaiting intake",
        "Some categories do not contain documents.",
        "They are housekeeping items and do not affect search readiness.",
        "Show empty categories",
        "dashboard",
        "info",
    ),
    "profile_unavailable": (
        "Approved Vault profile is unavailable",
        "The server-approved remote profile could not be loaded.",
        "Remote inventory and restore work remain unavailable.",
        "Review profile configuration",
        "configuration",
        "danger",
    ),
    "legacy_profile_not_remote": (
        "Historical profile has no remote Vault connection",
        "This record preserves earlier application evidence and is not an S3 connection profile.",
        "Remote probe and inventory controls do not apply to it.",
        "Review the environment Vault profile",
        "configuration",
        "info",
    ),
    "vault_profile_incomplete": (
        "Vault profile configuration is incomplete",
        "The profile is missing server-approved connection information.",
        "Remote probe and inventory controls remain unavailable.",
        "Complete profile configuration",
        "configuration",
        "warning",
    ),
    "vault_profile_invalid": (
        "Vault profile fields need attention",
        "One or more required profile values are missing or invalid.",
        "No profile change was saved.",
        "Correct the highlighted fields",
        "configuration",
        "warning",
    ),
    "vault_profile_configuration_disabled": (
        "Browser profile configuration is disabled",
        "This environment accepts Vault profiles only from deployed configuration.",
        "No browser-submitted profile can be saved.",
        "Review deployed Vault configuration",
        "configuration",
        "warning",
    ),
    "vault_endpoint_not_allowlisted": (
        "Vault endpoint is not approved",
        "The endpoint does not exactly match a server-approved S3 origin.",
        "The profile cannot connect to that endpoint.",
        "Review the endpoint allowlist",
        "configuration",
        "warning",
    ),
    "vault_endpoint_invalid": (
        "Vault endpoint needs correction",
        "Enter only an HTTP or HTTPS origin without credentials, a path, query, or fragment.",
        "The profile was not saved.",
        "Correct the endpoint",
        "configuration",
        "warning",
    ),
    "credential_alias_not_approved": (
        "Credential alias is not approved",
        "The alias is not mapped to a server-managed credential source.",
        "The browser cannot create or use the profile.",
        "Review approved credential aliases",
        "configuration",
        "warning",
    ),
    "credential_alias_unavailable": (
        "Approved credentials are unavailable",
        "The server-approved alias is present, but its credential variables are incomplete.",
        "Remote Vault operations remain unavailable.",
        "Review deployed credentials",
        "configuration",
        "danger",
    ),
    "profile_fingerprint_changed": (
        "Vault profile evidence changed",
        "The stored profile no longer matches its verified connection fingerprint.",
        "Remote operations remain blocked until the profile is reviewed.",
        "Review profile configuration",
        "configuration",
        "danger",
    ),
    "registration_read_failed": (
        "No published Vault inventory is available yet",
        "The bucket is reachable, but it does not contain a readable dataset registration.",
        "There is no authoritative remote generation to verify.",
        "Publish the first verified generation",
        "sync",
        "info",
    ),
    "vault_inventory_setup_required": (
        "Vault storage is ready for its first generation",
        "The approved profile reached its bucket, but no verified dataset inventory has been observed yet.",
        "Restore and authority decisions remain guarded until a generation is published and verified.",
        "Review first-generation publication",
        "sync",
        "info",
    ),
    "maintenance_worker_unavailable": (
        "Document maintenance is temporarily unavailable",
        "No maintenance worker has reported through the shared control channel recently.",
        "New validation, repair, and reindex work cannot be queued safely.",
        "Review maintenance worker status",
        "jobs",
        "warning",
    ),
    "inventory_unavailable": (
        "Remote inventory is unavailable",
        "No verified remote inventory observation is available.",
        "Remote authority cannot be relied on for recovery decisions.",
        "Open profile evidence",
        "configuration",
        "warning",
    ),
    "inventory_unverified": (
        "Remote inventory requires verification",
        "The latest inventory evidence has not passed verification.",
        "Restore and publication decisions remain guarded.",
        "Verify inventory",
        "configuration",
        "warning",
    ),
    "inventory_observation_stale": (
        "Remote inventory observation is stale",
        "The last verified inventory is older than the allowed evidence window.",
        "Refresh it before relying on remote authority.",
        "Refresh inventory evidence",
        "configuration",
        "warning",
    ),
    "runtime_observation_unavailable": (
        "Runtime observation is unavailable",
        "The active runtime has not been observed safely.",
        "Activation and rollback decisions remain guarded.",
        "Review jobs and audit",
        "jobs",
        "danger",
    ),
    "runtime_observation_stale": (
        "Runtime observation is stale",
        "The last runtime observation is older than the allowed evidence window.",
        "Refresh runtime evidence before changing activation state.",
        "Review runtime evidence",
        "jobs",
        "warning",
    ),
    "runtime_not_ready": (
        "Runtime is not ready",
        "The runtime does not yet satisfy the verified readiness conditions.",
        "Activation remains unavailable.",
        "Review restore evidence",
        "restore",
        "warning",
    ),
    "critical_job_unhealthy": (
        "A critical Vault job needs attention",
        "A critical job failed or stopped reporting progress.",
        "Dependent Vault work remains guarded.",
        "Review jobs and audit",
        "jobs",
        "danger",
    ),
    "capacity_degraded": (
        "Local capacity reserve is low",
        "Free space or inode reserve is below the maintenance safety threshold.",
        "New mutation work remains disabled until capacity is restored.",
        "Review maintenance capacity",
        "maintenance",
        "danger",
    ),
    "vault_admin_mutations_disabled": (
        "Vault changes are disabled",
        "This environment does not permit administrative Vault mutations.",
        "The control remains unavailable.",
        "Review configuration",
        "configuration",
        "warning",
    ),
    "production_activation_disabled": (
        "Activation is unavailable in production",
        "This browser workflow cannot activate a production runtime.",
        "Use the separately controlled production promotion process.",
        "Review runtime evidence",
        "jobs",
        "warning",
    ),
    "staging_activation_disabled": (
        "Staging activation is disabled",
        "This environment is not configured for staging runtime activation.",
        "Activation and signed rollback remain unavailable.",
        "Review configuration",
        "configuration",
        "warning",
    ),
    "runtime_activation_in_progress": (
        "A runtime change is already in progress",
        "Another activation request is being applied or recovered.",
        "Rollback remains unavailable until it reaches a durable outcome.",
        "Review jobs and audit",
        "jobs",
        "warning",
    ),
    "rollback_pointer_missing": (
        "Rollback evidence is incomplete",
        "The signed active or previous runtime pointer is missing.",
        "Signed rollback remains unavailable.",
        "Review runtime evidence",
        "jobs",
        "danger",
    ),
    "rollback_pointer_unverified": (
        "Rollback evidence could not be verified",
        "The signed runtime pointers did not pass verification.",
        "Signed rollback remains unavailable.",
        "Review runtime evidence",
        "jobs",
        "danger",
    ),
    "rollback_generation_unprojected": (
        "Rollback generation is not projected",
        "The previous runtime is not present in the verified generation projection.",
        "Signed rollback remains unavailable.",
        "Review generations",
        "generations",
        "danger",
    ),
    "rollback_active_generation_ineligible": (
        "The active runtime is not eligible for this rollback",
        "Only a verified local-maintenance child can use this rollback control.",
        "Signed rollback remains unavailable.",
        "Review generations",
        "generations",
        "warning",
    ),
    "rollback_lineage_invalid": (
        "Rollback lineage could not be verified",
        "The active and previous runtimes do not have the required parent relationship.",
        "Signed rollback remains unavailable.",
        "Review generations",
        "generations",
        "danger",
    ),
    "rollback_pointer_observation_stale": (
        "Rollback observation is stale",
        "The signed runtime pointer evidence is older than the allowed window.",
        "Refresh evidence before reviewing rollback.",
        "Review runtime evidence",
        "jobs",
        "warning",
    ),
    "activation_recovery_superadmin_unproven": (
        "Recovery authority is not verified",
        "The required recovery superadmin evidence could not be proven.",
        "Signed rollback remains unavailable.",
        "Review recovery evidence",
        "jobs",
        "danger",
    ),
    "lease_observation_failed": (
        "Writer lease observation is unavailable",
        "The current writer lease could not be observed safely.",
        "Do not assume that the dataset is available for writes.",
        "Review configuration",
        "configuration",
        "warning",
    ),
    "worker_heartbeat_expired": (
        "A worker stopped reporting progress",
        "The job heartbeat expired before the work reached a durable outcome.",
        "Review the checkpoint before retrying.",
        "Review jobs",
        "jobs",
        "danger",
    ),
    "restore_failed": (
        "Restore preparation did not complete",
        "The restore workspace could not be prepared safely.",
        "Nothing was activated.",
        "Review restore evidence",
        "restore",
        "danger",
    ),
    "activation_failed": (
        "Activation did not complete",
        "The runtime change did not reach a verified durable outcome.",
        "Review recovery evidence before retrying.",
        "Review jobs and audit",
        "jobs",
        "danger",
    ),
    "publication_failed": (
        "Publication did not complete",
        "The candidate was not published as verified remote authority.",
        "Remote authority remains unchanged.",
        "Review jobs and audit",
        "jobs",
        "danger",
    ),
    "maintenance_candidate_import_failed": (
        "Candidate import did not complete",
        "The maintenance candidate could not be imported safely.",
        "The active runtime remains unchanged.",
        "Review maintenance jobs",
        "maintenance",
        "danger",
    ),
    "external_embeddings_disabled": (
        "Embedding service is unavailable",
        "New embeddings cannot be generated in this environment.",
        "Reindex operations remain disabled.",
        "Review embedding configuration",
        "configuration",
        "warning",
    ),
    "bulk_reindex_disabled": (
        "Bulk reindexing is unavailable",
        "This environment does not permit a bulk reindex.",
        "Existing search artifacts remain unchanged.",
        "Review maintenance controls",
        "maintenance",
        "warning",
    ),
    "candidate_preparation_disabled": (
        "Candidate preparation is unavailable",
        "A bounded maintenance candidate cannot be prepared in this environment.",
        "The active runtime remains unchanged.",
        "Review maintenance controls",
        "maintenance",
        "warning",
    ),
    "candidate_not_ready": (
        "The candidate is not ready",
        "Required validation evidence is incomplete.",
        "Activation review remains unavailable.",
        "Review candidate evidence",
        "maintenance",
        "warning",
    ),
    "insufficient_maintenance_capacity": (
        "Maintenance capacity is insufficient",
        "The operation cannot preserve the required free-space or inode reserve.",
        "The maintenance control remains unavailable.",
        "Review maintenance capacity",
        "maintenance",
        "danger",
    ),
    "generation_authoritative": (
        "This generation is authoritative",
        "The generation is the verified remote authority.",
        "It remains protected from deletion.",
        "Review generations",
        "generations",
        "info",
    ),
    "generation_runtime_referenced": (
        "This generation is referenced by the runtime",
        "An active or previous runtime pointer refers to this generation.",
        "It remains protected from deletion.",
        "Review runtime evidence",
        "jobs",
        "info",
    ),
    "workspace_referenced": (
        "A restore workspace uses this generation",
        "A non-expired restore workspace still refers to this generation.",
        "It remains protected from deletion.",
        "Review restore workspaces",
        "restore",
        "info",
    ),
    "generation_job_in_progress": (
        "A Vault job is using this generation",
        "An active job still refers to this generation.",
        "It remains protected from deletion.",
        "Review jobs",
        "jobs",
        "info",
    ),
    "retention_hold_active": (
        "A retention hold is active",
        "An operator hold requires this generation to be preserved.",
        "It remains protected from deletion.",
        "Review retention holds",
        "retention",
        "info",
    ),
    "incident_investigation": (
        "Incident investigation",
        "This generation is preserved for an incident investigation.",
        "Deletion remains blocked until the hold is released.",
        "Review retention holds",
        "retention",
        "info",
    ),
    "legal_or_policy_review": (
        "Legal or policy review",
        "This generation is preserved for a legal or policy review.",
        "Deletion remains blocked until the hold is released.",
        "Review retention holds",
        "retention",
        "info",
    ),
    "operator_requested_preservation": (
        "Operator-requested preservation",
        "An operator explicitly requested that this generation be preserved.",
        "Deletion remains blocked until the hold is released.",
        "Review retention holds",
        "retention",
        "info",
    ),
}


def _authored(title, detail, consequence, action_label, section, severity):
    """Mark registry copy for catalog extraction while deferring translation."""
    return (
        gettext_noop(title),
        gettext_noop(detail),
        gettext_noop(consequence),
        gettext_noop(action_label),
        section,
        severity,
    )


for _code in {
    "vault_endpoint_scheme_rejected",
    "vault_endpoint_https_required",
    "vault_endpoint_port_invalid",
    "vault_endpoint_dns_failed",
    "vault_endpoint_dns_empty",
    "vault_endpoint_dns_invalid",
    "vault_endpoint_private_address",
}:
    REASONS.setdefault(
        _code,
        _authored(
            "Vault endpoint could not be approved",
            "The endpoint did not satisfy the deployed transport, DNS, or network safety policy.",
            "The profile was not saved and no connection was attempted.",
            "Review the endpoint and allowlist",
            "configuration",
            "warning",
        ),
    )

for _code in {
    "credential_alias_configuration_invalid",
    "environment_credential_alias_reserved",
    "environment_profile_locked",
    "vault_profile_disabled",
}:
    REASONS.setdefault(
        _code,
        _authored(
            "Vault profile cannot be changed",
            "The requested profile or credential source is controlled by deployed configuration.",
            "No profile change was made.",
            "Review deployed Vault configuration",
            "configuration",
            "warning",
        ),
    )


# Stable reasons emitted directly by Dashboard, maintenance, and Workbench UI
# producers. Keep this inventory explicit: adding a producer reason must add
# authored guidance in the same change.
UI_REASON_CODES = frozenset(
    {
        *REASONS,
        "activation",
        "activation_scheduled",
        "browser_secret_entry_rejected",
        "cleanup_inventory_unavailable",
        "confirmation_action_invalid",
        "confirmation_issued",
        "control_database_unavailable",
        "control_plane_unavailable",
        "gc_execution_unavailable",
        "gc_plan_created",
        "generation_not_candidate",
        "generation_not_retirable",
        "generation_not_retired",
        "generation_retired",
        "generation_unretired",
        "idempotency_conflict",
        "idempotency_key_required",
        "invalid_idempotency_key",
        "job_cancellation_requested",
        "job_not_completed",
        "job_retry_queued",
        "maintenance_candidate_prepared",
        "maintenance_job_cancellation_recorded",
        "maintenance_job_action_not_allowed",
        "maintenance_job_queued",
        "maintenance_job_requeued",
        "maintenance_plan_created",
        "malformed_filters",
        "no_source_changes",
        "operation_failed",
        "plan_expired",
        "plan_owner_mismatch",
        "profile_configured",
        "profile_inventory_verified",
        "profile_probe_completed",
        "promotion_queued",
        "rate_limited",
        "request_invalid",
        "request_json_invalid",
        "restore_queued",
        "retention_hold_created",
        "retention_hold_expiry_invalid",
        "retention_hold_released",
        "rollback_workspace_invalid",
        "runtime_read_only",
        "runtime_rollback",
        "runtime_rollback_scheduled",
        "selection_too_large",
        "snapshot_in_progress",
        "stale_plan",
        "stale_state",
        "stale_state_version",
        "state_version_required",
        "sync_queued",
        "typed_confirmation_required",
        "unknown_operation",
        "workspace_not_activation_ready",
        "empty_scope",
    }
)


REASONS.update(
    {
        "maintenance_plan_created": _authored(
            "Maintenance plan is ready",
            "The requested scope and safety evidence were recorded for review.",
            "No document data has changed yet.",
            "Review maintenance plan",
            "maintenance",
            "success",
        ),
        "maintenance_job_queued": _authored(
            "Maintenance work is queued",
            "The approved maintenance plan is waiting for a worker.",
            "The active runtime remains unchanged while the work is prepared.",
            "Review maintenance jobs",
            "maintenance",
            "success",
        ),
        "maintenance_job_cancellation_recorded": _authored(
            "Cancellation was requested",
            "The maintenance worker has been asked to stop at a safe checkpoint.",
            "Work may continue briefly until that checkpoint is reached.",
            "Review maintenance jobs",
            "maintenance",
            "info",
        ),
        "maintenance_job_requeued": _authored(
            "Maintenance work is queued again",
            "The failed maintenance job was returned to the worker queue.",
            "The active runtime remains unchanged while the retry runs.",
            "Review maintenance jobs",
            "maintenance",
            "success",
        ),
        "maintenance_job_action_not_allowed": _authored(
            "This job action is unavailable",
            "The maintenance job is not in a state that permits the requested action.",
            "The job remains unchanged.",
            "Refresh and review the maintenance job",
            "maintenance",
            "warning",
        ),
        "maintenance_candidate_prepared": _authored(
            "Maintenance candidate is prepared",
            "The completed maintenance result was imported as a reviewable candidate.",
            "The candidate is not active until a separately verified activation.",
            "Review candidate evidence",
            "maintenance",
            "success",
        ),
        "restore_queued": _authored(
            "Restore preparation is queued",
            "A worker will prepare and verify an isolated restore workspace.",
            "The active runtime remains unchanged.",
            "Review restore jobs",
            "restore",
            "success",
        ),
        "job_cancellation_requested": _authored(
            "Job cancellation was requested",
            "The worker has been asked to stop at a durable checkpoint.",
            "The job may continue briefly while it reaches that checkpoint.",
            "Review jobs",
            "jobs",
            "info",
        ),
        "job_retry_queued": _authored(
            "Job retry is queued",
            "The job will resume from its recorded durable checkpoint.",
            "Authority remains unchanged until the retry succeeds and is verified.",
            "Review jobs",
            "jobs",
            "success",
        ),
        "profile_configured": _authored(
            "Vault profile is configured",
            "The server-approved read-only connection profile was recorded.",
            "Remote evidence is not trusted until the profile is probed and verified.",
            "Review profile evidence",
            "configuration",
            "success",
        ),
        "profile_probe_completed": _authored(
            "Profile probe completed",
            "The approved profile completed its bounded read-only connectivity check.",
            "Verify inventory evidence before relying on remote authority.",
            "Review profile evidence",
            "configuration",
            "success",
        ),
        "profile_inventory_verified": _authored(
            "Remote inventory is verified",
            "The approved profile inventory passed bounded verification.",
            "Recovery planning may rely on this observation until it becomes stale.",
            "Review generations",
            "generations",
            "success",
        ),
        "sync_queued": _authored(
            "Synchronization is queued",
            "A worker will prepare and verify the requested synchronization.",
            "Remote authority remains unchanged until publication completes.",
            "Review synchronization jobs",
            "sync",
            "success",
        ),
        "no_source_changes": _authored(
            "No source changes need synchronization",
            "The verified source state already matches the latest synchronization evidence.",
            "No new candidate or publication job was created.",
            "Review synchronization evidence",
            "sync",
            "info",
        ),
        "confirmation_issued": _authored(
            "Confirmation is ready",
            "A short-lived confirmation was issued for the reviewed action and target.",
            "The action is still unchanged until the confirmation is submitted.",
            "Complete the confirmed action",
            "overview",
            "success",
        ),
        "promotion_queued": _authored(
            "Publication is queued",
            "The confirmed candidate is waiting for verified publication.",
            "Remote authority remains unchanged until publication succeeds.",
            "Review publication jobs",
            "jobs",
            "success",
        ),
        "generation_retired": _authored(
            "Generation is retired",
            "The generation was removed from normal candidate use.",
            "Protected evidence is retained until cleanup is separately planned and approved.",
            "Review generations",
            "generations",
            "success",
        ),
        "generation_unretired": _authored(
            "Generation is available for review again",
            "The retired generation was restored to its eligible review state.",
            "It is not authoritative unless separately promoted and verified.",
            "Review generations",
            "generations",
            "success",
        ),
        "retention_hold_created": _authored(
            "Retention hold is active",
            "The generation was placed under the recorded preservation reason.",
            "Cleanup cannot remove it while the hold remains active.",
            "Review retention holds",
            "retention",
            "success",
        ),
        "retention_hold_released": _authored(
            "Retention hold is released",
            "The selected preservation hold is no longer active.",
            "Other protections may still prevent cleanup.",
            "Review generation protections",
            "retention",
            "success",
        ),
        "gc_plan_created": _authored(
            "Cleanup plan is ready",
            "Eligible artifacts and their protection evidence were recorded for review.",
            "Nothing has been deleted.",
            "Review cleanup plan",
            "retention",
            "success",
        ),
        "activation_scheduled": _authored(
            "Runtime activation is scheduled",
            "The confirmed workspace is queued for a verified runtime change.",
            "The current runtime remains authoritative until activation succeeds.",
            "Review activation jobs",
            "jobs",
            "success",
        ),
        "runtime_rollback_scheduled": _authored(
            "Runtime rollback is scheduled",
            "The confirmed previous runtime is queued for signed rollback.",
            "The current runtime remains authoritative until rollback succeeds.",
            "Review rollback jobs",
            "jobs",
            "success",
        ),
        "control_database_unavailable": _authored(
            "Control evidence is unavailable",
            "The Workbench could not read its control database safely.",
            "Vault decisions remain guarded until control evidence is restored.",
            "Review service health",
            "configuration",
            "danger",
        ),
        "control_plane_unavailable": _authored(
            "Mutation controls are unavailable",
            "The Workbench could not verify its request safety controls.",
            "No mutation request was accepted.",
            "Review service health",
            "configuration",
            "danger",
        ),
        "runtime_read_only": _authored(
            "This runtime is read-only",
            "The environment does not permit document mutation work.",
            "The requested maintenance control remains unavailable.",
            "Review runtime configuration",
            "configuration",
            "warning",
        ),
        "snapshot_in_progress": _authored(
            "A protected snapshot is in progress",
            "Maintenance must wait while the current snapshot or publication work completes.",
            "The requested maintenance control remains unavailable.",
            "Review active jobs",
            "jobs",
            "warning",
        ),
        "cleanup_inventory_unavailable": _authored(
            "Cleanup inventory is unavailable",
            "Protected and eligible local artifacts could not be inventoried safely.",
            "Cleanup remains unavailable.",
            "Review maintenance evidence",
            "maintenance",
            "danger",
        ),
        "request_invalid": _authored(
            "The request could not be accepted",
            "Required request evidence was missing or invalid.",
            "No operational change was made.",
            "Review the request and try again",
            "overview",
            "warning",
        ),
        "request_json_invalid": _authored(
            "The request format is invalid",
            "The submitted JSON body could not be read as an object.",
            "No operational change was made.",
            "Correct the request and try again",
            "overview",
            "warning",
        ),
        "rate_limited": _authored(
            "Too many requests were submitted",
            "The mutation request limit was reached for this operator and connection.",
            "No additional request was accepted.",
            "Wait before trying again",
            "overview",
            "warning",
        ),
        "idempotency_key_required": _authored(
            "A valid request key is required",
            "The mutation needs a bounded idempotency key to prevent duplicate work.",
            "No operational change was made.",
            "Refresh the form and try again",
            "overview",
            "warning",
        ),
        "invalid_idempotency_key": _authored(
            "The request key is invalid",
            "The maintenance request key does not meet the required format.",
            "No maintenance work was created.",
            "Refresh the form and try again",
            "maintenance",
            "warning",
        ),
        "idempotency_conflict": _authored(
            "This request key was already used",
            "The same request key refers to different work.",
            "Duplicate or ambiguous work was not created.",
            "Refresh and submit a new request",
            "overview",
            "warning",
        ),
        "state_version_required": _authored(
            "Fresh Workbench evidence is required",
            "The request did not include the observed state version.",
            "No operational change was made.",
            "Refresh the Workbench and try again",
            "overview",
            "warning",
        ),
        "stale_state": _authored(
            "Workbench evidence has changed",
            "The request was based on an older observed state.",
            "No operational change was made.",
            "Refresh the Workbench and review again",
            "overview",
            "warning",
        ),
        "stale_state_version": _authored(
            "Maintenance evidence has changed",
            "The request was based on an older maintenance plan or job state.",
            "No maintenance change was accepted.",
            "Refresh maintenance evidence and review again",
            "maintenance",
            "warning",
        ),
        "browser_secret_entry_rejected": _authored(
            "Secrets cannot be entered here",
            "Vault credentials must come from the approved server-side credential source.",
            "The submitted profile was not changed.",
            "Review profile configuration",
            "configuration",
            "warning",
        ),
        "selection_too_large": _authored(
            "The maintenance selection is too large",
            "The selected scope exceeds the bounded preview or work limit.",
            "No maintenance plan was created.",
            "Choose a smaller scope",
            "maintenance",
            "warning",
        ),
        "malformed_filters": _authored(
            "A maintenance filter is invalid",
            "One or more filter values could not be interpreted safely.",
            "No maintenance plan was created.",
            "Correct the filters and review again",
            "maintenance",
            "warning",
        ),
        "empty_scope": _authored(
            "No documents match this maintenance scope",
            "The selected folders, documents, and filters produced no eligible work.",
            "No maintenance plan was created.",
            "Choose a different scope",
            "maintenance",
            "info",
        ),
        "unknown_operation": _authored(
            "This maintenance operation is unavailable",
            "The requested operation is not an approved Workbench operation.",
            "No maintenance plan was created.",
            "Choose an available operation",
            "maintenance",
            "warning",
        ),
        "plan_owner_mismatch": _authored(
            "This plan belongs to another operator",
            "Only the operator who prepared the plan can queue it.",
            "No maintenance work was queued.",
            "Prepare a new maintenance plan",
            "maintenance",
            "warning",
        ),
        "plan_expired": _authored(
            "The maintenance plan expired",
            "Its bounded review window ended before the work was queued.",
            "No maintenance work was queued.",
            "Prepare and review a new plan",
            "maintenance",
            "warning",
        ),
        "stale_plan": _authored(
            "The maintenance plan is stale",
            "Source or runtime evidence changed after the plan was prepared.",
            "No maintenance work was queued.",
            "Prepare and review a new plan",
            "maintenance",
            "warning",
        ),
        "typed_confirmation_required": _authored(
            "Typed confirmation is required",
            "This maintenance operation needs the displayed confirmation phrase.",
            "No maintenance work was queued.",
            "Enter the confirmation and review again",
            "maintenance",
            "warning",
        ),
        "job_not_completed": _authored(
            "Maintenance work is not complete",
            "A candidate can only be prepared from a completed maintenance job.",
            "Candidate preparation remains unavailable.",
            "Review maintenance jobs",
            "maintenance",
            "warning",
        ),
        "generation_not_candidate": _authored(
            "This generation is not a candidate",
            "Only a verified candidate generation can be promoted.",
            "Publication was not queued.",
            "Review generation evidence",
            "generations",
            "warning",
        ),
        "generation_not_retirable": _authored(
            "This generation cannot be retired",
            "Its current lifecycle state is not eligible for retirement.",
            "The generation remains unchanged.",
            "Review generation evidence",
            "generations",
            "warning",
        ),
        "generation_not_retired": _authored(
            "This generation is not retired",
            "Only a retired generation can be returned to candidate review.",
            "The generation remains unchanged.",
            "Review generation evidence",
            "generations",
            "warning",
        ),
        "workspace_not_activation_ready": _authored(
            "This workspace is not ready for activation",
            "Required restore validation evidence is incomplete.",
            "Activation was not scheduled.",
            "Review restore evidence",
            "restore",
            "warning",
        ),
        "rollback_workspace_invalid": _authored(
            "This workspace cannot be used for rollback",
            "The workspace is not verified for the one-step rollback purpose.",
            "Rollback was not scheduled.",
            "Review rollback evidence",
            "restore",
            "warning",
        ),
        "confirmation_action_invalid": _authored(
            "This confirmation action is unavailable",
            "The requested action and target do not match an approved confirmation flow.",
            "No operational change was made.",
            "Refresh the Workbench and review again",
            "overview",
            "warning",
        ),
        "retention_hold_expiry_invalid": _authored(
            "The hold expiry is invalid",
            "The optional expiry must be a valid future date and time.",
            "No retention hold was created.",
            "Correct the expiry and try again",
            "retention",
            "warning",
        ),
        "gc_execution_unavailable": _authored(
            "Browser cleanup execution is unavailable",
            "Cleanup plans can be reviewed here but cannot be executed from this interface.",
            "No artifacts were deleted.",
            "Review cleanup evidence",
            "retention",
            "warning",
        ),
        "operation_failed": _authored(
            "The operation did not complete",
            "The Workbench could not reach a verified outcome.",
            "Review current evidence before retrying.",
            "Review jobs and audit",
            "jobs",
            "danger",
        ),
        "activation": _authored(
            "Activation confirmation required",
            "The reviewed workspace requires explicit confirmation before scheduling.",
            "The active runtime remains unchanged.",
            "Review activation evidence",
            "restore",
            "info",
        ),
        "runtime_rollback": _authored(
            "Rollback confirmation required",
            "The reviewed previous runtime requires explicit confirmation before scheduling.",
            "The active runtime remains unchanged.",
            "Review rollback evidence",
            "restore",
            "info",
        ),
    }
)


LABELS = {
    "unknown": "Unavailable",
    "unavailable": "Unavailable",
    "available": "Available",
    "setup_required": "Setup required",
    "worker_offline": "Worker unavailable",
    "ready": "Ready",
    "passed": "Passed",
    "idle": "No work in progress",
    "degraded": "Needs attention",
    "healthy": "Healthy",
    "stale": "Observation is stale",
    "not_held": "Not held",
    "held": "Held",
    "queued": "Queued",
    "claimed": "Claimed by a worker",
    "running": "Running",
    "waiting": "Waiting",
    "cancelling": "Cancellation in progress",
    "cancel_requested": "Cancellation requested",
    "cancelled": "Cancelled",
    "succeeded": "Completed",
    "success": "Completed",
    "failed": "Failed",
    "terminal_failed": "Failed",
    "retryable_failed": "Retry available",
    "candidate": "Candidate",
    "authoritative": "Authoritative",
    "retired": "Retired",
    "activation_ready": "Ready for activation review",
    "building": "Candidate preparation in progress",
    "prepared": "Prepared",
    "verified": "Verified",
    "local_only": "Local only",
    "remote_only": "Remote only",
    "present": "Present",
    "absent": "Not present",
    "completed": "Completed",
    "skipped": "Skipped",
    "allowed": "Available",
    "blocked": "Unavailable",
    "pending": "Pending",
    "active": "Active",
    "released": "Released",
    "expired": "Expired",
    "created": "Created",
    "planned": "Planned",
    "restore": "Restore preparation",
    "publish": "Publish candidate",
    "promotion": "Promotion",
    "inventory": "Inventory verification",
    "probe": "Read-only profile probe",
    "sync": "Active synchronization",
    "cleanup": "Artifact cleanup",
    "reindex": "Reindex documents",
    "validate": "Validate documents",
    "validate_files": "Validate documents",
    "repair_indexes": "Repair stored indexes",
    "reindex_needed": "Reindex documents that need attention",
    "reindex_selected": "Reindex selected documents",
    "run_maintenance_candidate": "Prepare maintenance candidate",
    "ocr_repair": "Repair document text",
}


def label_for(value):
    """Return an authored label without inventing copy from underscores."""
    if value in (None, ""):
        return ""
    return gettext(LABELS.get(str(value), "Additional evidence"))


def present_reason(code, *, action_url=""):
    """Resolve a stable reason code to translated operator guidance."""
    definition = REASONS.get(str(code), UNKNOWN_REASON)
    if isinstance(definition, tuple):
        title, detail, consequence, action_label, section, severity = definition
    else:
        title = definition["title"]
        detail = definition["detail"]
        consequence = definition["consequence"]
        action_label = definition["action_label"]
        section = definition["section"]
        severity = definition["severity"]
    if not action_url:
        if section == "dashboard":
            action_url = reverse("dashboard")
        else:
            action_url = f"{reverse('operations_panel')}?section={section}"
    return {
        "title": gettext(title),
        "detail": gettext(detail),
        "consequence": gettext(consequence),
        "action_label": gettext(action_label),
        "action_url": action_url,
        "severity": severity,
        "technical_code": str(code or ""),
        "known": str(code) in REASONS,
    }


def _decorate_mapping(mapping):
    for key, value in list(mapping.items()):
        if isinstance(value, dict):
            _decorate_mapping(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _decorate_mapping(item)

        if key in {
            "state",
            "status",
            "kind",
            "operation",
            "phase",
            "action",
            "result",
            "event_type",
            "vault_state",
            "runtime_state",
            "local_presence",
            "candidate_state",
        }:
            mapping[f"{key}_label"] = label_for(value)

    code = mapping.get("reason_code")
    if code:
        mapping["presentation"] = present_reason(
            code, action_url=mapping.get("action_url", "")
        )
    safe_code = mapping.get("safe_error_code") or mapping.get("error_summary")
    if safe_code:
        mapping["error_presentation"] = present_reason(safe_code)
    reasons = mapping.get("protection_reasons")
    if isinstance(reasons, list):
        mapping["protection_presentations"] = [
            present_reason(reason) for reason in reasons
        ]


def decorate_operator_state(state):
    """Add presentation fields while preserving the stable evidence shape."""
    _decorate_mapping(state)
    return state


def decorate_dashboard_state(state):
    """Attach presentations and separate low-impact housekeeping."""
    decorate_operator_state(state)
    attention = state.get("attention_items", [])
    state["attention_items"] = [
        item for item in attention if item.get("severity") != "muted"
    ]
    state["housekeeping_items"] = [
        item for item in attention if item.get("severity") == "muted"
    ]
    posture = state.get("posture") or {}
    if posture.get("reason_code"):
        posture["presentation"] = present_reason(
            posture["reason_code"],
            action_url=(posture.get("recommended_action") or {}).get(
                "action_url", ""
            ),
        )
    return state


def presented_copy(value):
    """Return a decorated copy for additive API presentation objects."""
    result = deepcopy(value)
    if isinstance(result, dict):
        decorate_operator_state(result)
    return result
