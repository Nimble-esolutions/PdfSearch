"""Human-centred presentation for stable operator evidence tokens.

Backend codes remain authoritative.  This module is the only boundary that
turns them into user-facing language for Dashboard and Workbench templates.
"""

from copy import deepcopy

from django.urls import reverse
from django.utils.translation import gettext


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


LABELS = {
    "unknown": "Unavailable",
    "unavailable": "Unavailable",
    "available": "Available",
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
            "vault_state",
            "runtime_state",
            "local_presence",
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
