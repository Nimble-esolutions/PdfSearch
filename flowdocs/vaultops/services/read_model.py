import hashlib
import json

from django.conf import settings
from django.utils import timezone

from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    GarbageCollectionPlan,
    RestoreWorkspace,
    RetentionHold,
    RuntimePointerObservation,
    SourceMutationState,
    SyncPolicy,
    VaultAuditEvent,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultJob,
)
from vaultops.services.retention import generation_protection_reasons


ACTIVE_JOB_STATES = {
    VaultJob.Status.CLAIMED,
    VaultJob.Status.RUNNING,
    VaultJob.Status.WAITING,
    VaultJob.Status.CANCELLING,
}
UNHEALTHY_JOB_STATES = {
    VaultJob.Status.STALE,
    VaultJob.Status.TERMINAL_FAILED,
}
REDACTED_KEYS = {
    "access_key",
    "credential",
    "error",
    "exception",
    "owner_token",
    "password",
    "secret",
    "signature",
    "token",
    "traceback",
}


REMEDIATION_DESTINATIONS = {
    "profile_unavailable": {
        "title": "Approved Vault profile is unavailable",
        "detail": "Materialize or review the server-approved profile before requesting inventory evidence.",
        "label": "Review profile configuration",
        "section": "configuration",
    },
    "inventory_unavailable": {
        "title": "Remote inventory has not been observed",
        "detail": "Run a read-only probe, then verify the authoritative inventory from Configuration.",
        "label": "Open profile evidence",
        "section": "configuration",
    },
    "inventory_unverified": {
        "title": "Remote inventory requires verification",
        "detail": "Refresh the approved profile inventory before planning restore or publication work.",
        "label": "Verify inventory",
        "section": "configuration",
    },
    "runtime_observation_unavailable": {
        "title": "Runtime authority is not observed",
        "detail": "Review runtime and job evidence before making any activation decision.",
        "label": "Review jobs and audit",
        "section": "jobs",
    },
    "runtime_not_ready": {
        "title": "Runtime is not ready",
        "detail": "Inspect the runtime job and prepared workspaces; activation remains guarded until evidence is ready.",
        "label": "Review restore evidence",
        "section": "restore",
    },
    "critical_job_unhealthy": {
        "title": "A critical Vault job needs attention",
        "detail": "Inspect the failed or stale job and retry only from its durable checkpoint.",
        "label": "Review jobs and audit",
        "section": "jobs",
    },
    "capacity_degraded": {
        "title": "Local capacity reserve is degraded",
        "detail": "Free space or inode reserve is below the maintenance safety threshold. Do not queue a mutation until capacity is restored.",
        "label": "Review maintenance capacity",
        "section": "maintenance",
    },
}


def enrich_workbench_readiness(state):
    """Attach safe, typed remediation guidance without exposing secrets."""
    authority = state.get("authority") or {}
    reasons = list(authority.get("blocking_reasons") or [])
    maintenance = state.get("maintenance") or {}
    health = maintenance.get("health") or {}
    reserve = health.get("free_space_reserve") or {}
    if reserve.get("state") == "degraded":
        reasons.append("capacity_degraded")

    issues = []
    seen = set()
    for reason_code in reasons:
        if reason_code in seen:
            continue
        seen.add(reason_code)
        definition = REMEDIATION_DESTINATIONS.get(reason_code)
        if not definition:
            continue
        issues.append({"reason_code": reason_code, **definition})

    disabled_capabilities = []
    for operation, capability in (maintenance.get("capabilities") or {}).items():
        if not capability.get("enabled") and capability.get("reason_code"):
            disabled_capabilities.append({
                "operation": operation,
                "reason_code": capability["reason_code"],
            })

    environment = state.get("environment") or {}
    app_env = str(environment.get("app_env") or "").casefold()
    is_local_dev = app_env in {"development", "dev", "local", "test"} or not environment.get("is_production", False)
    state["readiness"] = {
        "status": "degraded" if issues else "ready",
        "issues": issues,
        "disabled_capabilities": disabled_capabilities,
        "local_development": {
            "enabled": is_local_dev,
            "title": "Local development posture" if is_local_dev else "Controlled runtime posture",
            "message": (
                "Remote publication and production activation stay disabled or explicitly gated in local development. Use this Workbench to inspect evidence and prepare candidates; it never changes remote authority implicitly."
                if is_local_dev
                else "Remote publication, restore, and activation remain separately gated by verified evidence and explicit operator confirmation."
            ),
        },
    }
    return state


def _safe_mapping(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in REDACTED_KEYS):
            continue
        if isinstance(item, dict):
            result[key] = _safe_mapping(item)
        elif isinstance(item, list):
            result[key] = [
                _safe_mapping(entry) if isinstance(entry, dict) else entry
                for entry in item[:50]
            ]
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
    return result


def _state_digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def generation_state_digest(generation):
    return _state_digest(
        {
            "profile_id": generation.profile_id,
            "dataset_id": generation.dataset_id,
            "generation_id": generation.generation_id,
            "manifest_digest": generation.manifest_digest,
            "vault_state": generation.vault_state,
            "runtime_state": generation.runtime_state,
            "local_presence": generation.local_presence,
            "updated_at": generation.updated_at,
        }
    )


def workspace_state_digest(workspace):
    return _state_digest(
        {
            "workspace_id": str(workspace.public_id),
            "generation_id": workspace.generation.generation_id,
            "manifest_digest": workspace.manifest_digest,
            "state": workspace.state,
            "runtime_path": workspace.runtime_path,
            "prepared_at": workspace.prepared_at,
            "updated_at": workspace.updated_at,
        }
    )


def _authority_state(profile, dataset_id, deployment_id):
    observed_at = timezone.now()
    projection = (
        VaultDatasetProjection.objects.filter(
            profile=profile, dataset_id=dataset_id
        )
        .order_by("-inventory_observed_at")
        .first()
    )
    runtime = (
        RuntimePointerObservation.objects.filter(
            deployment_id=deployment_id
        )
        .order_by("-observed_at")
        .first()
    )
    critical_job = (
        VaultJob.objects.filter(
            profile=profile,
            dataset_id=dataset_id,
            status__in=ACTIVE_JOB_STATES | UNHEALTHY_JOB_STATES,
        )
        .order_by("-updated_at")
        .first()
    )
    runtime_generation = None
    if runtime and runtime.active_generation_id:
        runtime_generation = ArtifactGeneration.objects.filter(
            profile=profile,
            dataset_id=dataset_id,
            generation_id=runtime.active_generation_id,
        ).first()

    blocking_reasons = []
    if projection is None:
        blocking_reasons.append("inventory_unavailable")
    elif projection.inventory_state != "verified":
        blocking_reasons.append("inventory_unverified")
    if runtime is None:
        blocking_reasons.append("runtime_observation_unavailable")
    elif runtime.status != "ready":
        blocking_reasons.append("runtime_not_ready")
    if critical_job and critical_job.status in UNHEALTHY_JOB_STATES:
        blocking_reasons.append("critical_job_unhealthy")

    allowed_actions = ["refresh_inventory"]
    if projection and projection.inventory_state == "verified":
        allowed_actions.extend(["plan_restore", "compare_generations"])
    return {
        "status": "healthy" if not blocking_reasons else "degraded",
        "reason_code": (
            "" if not blocking_reasons else blocking_reasons[0]
        ),
        "observed_at": observed_at,
        "remote": {
            "state": (
                projection.inventory_state if projection else "unknown"
            ),
            "authoritative_generation_id": (
                projection.authoritative_generation_id
                if projection
                else ""
            ),
            "pointer_digest": (
                projection.pointer_digest if projection else ""
            ),
            "observed_at": (
                projection.inventory_observed_at if projection else None
            ),
        },
        "runtime": {
            "state": runtime.status if runtime else "unknown",
            "active_generation_id": (
                runtime.active_generation_id if runtime else ""
            ),
            "previous_generation_id": (
                runtime.previous_generation_id if runtime else ""
            ),
            "projected_runtime_state": (
                runtime_generation.runtime_state
                if runtime_generation
                else "unknown"
            ),
            "observed_at": runtime.observed_at if runtime else None,
        },
        "critical_job": (
            {
                "public_id": str(critical_job.public_id),
                "operation": critical_job.operation,
                "phase": critical_job.phase,
                "status": critical_job.status,
                "heartbeat_at": critical_job.heartbeat_at,
                "safe_error_code": critical_job.safe_error_code,
            }
            if critical_job
            else None
        ),
        "allowed_actions": allowed_actions,
        "blocking_reasons": blocking_reasons,
    }


def build_authority_state(*, profile_key, dataset_id, deployment_id):
    try:
        profile = VaultConnectionProfile.objects.get(
            key=profile_key, enabled=True
        )
    except VaultConnectionProfile.DoesNotExist:
        return {
            "status": "unknown",
            "reason_code": "profile_unavailable",
            "observed_at": timezone.now(),
            "remote": {"state": "unknown"},
            "runtime": {"state": "unknown"},
            "critical_job": None,
            "allowed_actions": [],
            "blocking_reasons": ["profile_unavailable"],
        }
    state = _authority_state(profile, dataset_id, deployment_id)
    state["profile"] = {
        "key": profile.key,
        "dataset_id": dataset_id,
        "read_only": profile.read_only,
        "fingerprint": profile.fingerprint,
    }
    return state


def _environment_summary(identity):
    return {
        "app_env": identity.app_env.value,
        "dataset_id": identity.dataset_id,
        "authoritative_dataset_id": identity.authoritative_dataset_id,
        "restore_source_dataset_id": identity.restore_source_dataset_id,
        "production_source_id": identity.production_source_id,
        "deployment_id": identity.deployment_id,
        "backup_role": identity.backup_role.value,
        "data_mode": identity.data_mode.value,
        "app_release": identity.app_release_version,
        "image_digest": identity.app_image_digest,
        "is_production": identity.is_production,
    }


def _profile_records():
    return [
        {
            "key": profile.key,
            "display_name": profile.display_name,
            "source": profile.source,
            "enabled": profile.enabled,
            "read_only": profile.read_only,
            "environment_locked": profile.environment_locked,
            "endpoint_origin": profile.endpoint_origin,
            "bucket": profile.bucket,
            "region": profile.region,
            "dataset_id": profile.dataset_id,
            "production_source_id": profile.production_source_id,
            "credential_alias": (
                "configured" if profile.credential_alias else "unavailable"
            ),
            "capability_evidence": _safe_mapping(
                profile.capability_evidence
            ),
            "last_probed_at": profile.last_probed_at,
        }
        for profile in VaultConnectionProfile.objects.order_by("key")
    ]


def _generation_records(profile, dataset_id, limit=50):
    queryset = ArtifactGeneration.objects.filter(
        profile=profile, dataset_id=dataset_id
    ).order_by("-created_at")[:limit]
    records = []
    for generation in queryset:
        protection_reasons = generation_protection_reasons(generation)
        records.append({
            "public_id": generation.pk,
            "generation_id": generation.generation_id,
            "manifest_digest": generation.manifest_digest,
            "vault_state": generation.vault_state,
            "runtime_state": generation.runtime_state,
            "local_presence": generation.local_presence,
            "deployment_id": generation.deployment_id,
            "created_at": generation.created_at,
            "observed_at": generation.observed_at,
            "state_digest": generation_state_digest(generation),
            "file_count": len(generation.manifest.get("files", [])),
            "byte_count": sum(
                int(item.get("bytes", 0))
                for item in generation.manifest.get("files", [])
                if isinstance(item, dict)
            ),
            "protection_reasons": protection_reasons,
            "retirement_allowed": (
                generation.vault_state
                in {
                    ArtifactGeneration.VaultState.CANDIDATE,
                    ArtifactGeneration.VaultState.LEGACY_READ_ONLY,
                }
                and not any(
                    reason
                    in {
                        "generation_authoritative",
                        "generation_runtime_referenced",
                        "generation_job_in_progress",
                    }
                    for reason in protection_reasons
                )
            ),
            "unretirement_allowed": (
                generation.vault_state
                == ArtifactGeneration.VaultState.RETIRED
            ),
        })
    return records


def _workspace_records(profile, dataset_id, limit=25):
    queryset = (
        RestoreWorkspace.objects.select_related("generation")
        .filter(
            generation__profile=profile,
            generation__dataset_id=dataset_id,
        )
        .order_by("-created_at")[:limit]
    )
    return [
        {
            "public_id": str(workspace.public_id),
            "generation_id": workspace.generation.generation_id,
            "state": workspace.state,
            "downloaded_objects": workspace.downloaded_objects,
            "downloaded_bytes": workspace.downloaded_bytes,
            "safe_error_code": workspace.safe_error_code,
            "prepared_at": workspace.prepared_at,
            "created_at": workspace.created_at,
            "state_digest": workspace_state_digest(workspace),
            "activation_allowed": (
                workspace.state
                == RestoreWorkspace.State.ACTIVATION_READY
                and not settings.ENV_IDENTITY.is_production
                and settings.STAGING_RUNTIME_ACTIVATION_ENABLED
            ),
        }
        for workspace in queryset
    ]


def _job_records(profile, dataset_id, limit=50):
    return [
        {
            "public_id": str(job.public_id),
            "operation": job.operation,
            "phase": job.phase,
            "status": job.status,
            "generation_id": job.generation_id,
            "progress": _safe_mapping(job.progress),
            "heartbeat_at": job.heartbeat_at,
            "safe_error_code": job.safe_error_code,
            "state_version": job.state_version,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "cancellable": job.status
            in (
                {VaultJob.Status.QUEUED} | ACTIVE_JOB_STATES
            ),
        }
        for job in VaultJob.objects.filter(
            profile=profile, dataset_id=dataset_id
        ).order_by("-created_at")[:limit]
    ]


def _audit_records(limit=50):
    return [
        {
            "public_id": str(event.public_id),
            "action": event.action,
            "result": event.result,
            "reason_code": event.reason_code,
            "safe_error_code": event.safe_error_code,
            "actor_name": event.actor_name,
            "job_public_id": (
                str(event.job_public_id) if event.job_public_id else ""
            ),
            "evidence": _safe_mapping(event.evidence),
            "created_at": event.created_at,
        }
        for event in VaultAuditEvent.objects.order_by("-created_at")[:limit]
    ]


def _retention_records(profile, dataset_id, limit=50):
    now = timezone.now()
    return [
        {
            "public_id": hold.pk,
            "generation_id": hold.generation.generation_id,
            "reason_code": hold.reason_code,
            "owner_reference": hold.owner_reference,
            "expires_at": hold.expires_at,
            "released_at": hold.released_at,
            "active": (
                hold.released_at is None
                and (hold.expires_at is None or hold.expires_at > now)
            ),
            "created_at": hold.created_at,
            "updated_at": hold.updated_at,
        }
        for hold in RetentionHold.objects.select_related("generation")
        .filter(
            generation__profile=profile,
            generation__dataset_id=dataset_id,
        )
        .order_by("-created_at")[:limit]
    ]


def _gc_plan_records(profile, dataset_id, limit=25):
    return [
        {
            "public_id": str(plan.public_id),
            "state": plan.state,
            "plan_digest": plan.plan_digest,
            "inventory_version": plan.inventory_version,
            "pointer_version": plan.pointer_version,
            "candidate_generation_ids": [
                item.get("generation_id", "")
                for item in plan.candidates
                if isinstance(item, dict)
            ],
            "estimates": _safe_mapping(plan.estimates),
            "expires_at": plan.expires_at,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }
        for plan in GarbageCollectionPlan.objects.filter(
            profile=profile,
            dataset_id=dataset_id,
        ).order_by("-created_at")[:limit]
    ]


def _lease_summary(dataset_id):
    try:
        from core.lease import get_lease_status

        lease = get_lease_status(dataset_id)
    except Exception:
        return {
            "state": "unknown",
            "reason_code": "lease_observation_failed",
        }
    if lease is None:
        return {"state": "not_held", "reason_code": ""}
    holder = str(lease.get("instance_id", ""))
    fingerprint = (
        hashlib.sha256(holder.encode("utf-8")).hexdigest()[:12]
        if holder
        else ""
    )
    return {
        "state": "held",
        "holder_fingerprint": fingerprint,
        "writer_epoch": lease.get("writer_epoch"),
        "renewed_at": lease.get("renewed_at"),
        "expires_at": lease.get("expires_at"),
        "reason_code": "",
    }


def build_workbench_state(*, profile_key=None):
    identity = settings.ENV_IDENTITY
    profile_key = profile_key or settings.VAULT_DEFAULT_PROFILE
    observed_at = timezone.now()
    try:
        profile = VaultConnectionProfile.objects.get(
            key=profile_key, enabled=True
        )
    except VaultConnectionProfile.DoesNotExist:
        return {
            "status": "unknown",
            "reason_code": "profile_unavailable",
            "severity": "warning",
            "recommended_action": "Configure or materialize the locked profile.",
            "observed_at": observed_at,
            "state_version": _state_digest(
                {
                    "profile": profile_key,
                    "environment": _environment_summary(identity),
                    "profile_state": "unavailable",
                }
            ),
            "environment": _environment_summary(identity),
            "authority": {
                "remote": {"state": "unknown"},
                "runtime": {"state": "unknown"},
                "blocking_reasons": ["profile_unavailable"],
                "allowed_actions": [],
            },
            "profiles": _profile_records(),
            "generations": [],
            "workspaces": [],
            "jobs": [],
            "audit": _audit_records(),
            "retention_holds": [],
            "gc_plans": [],
            "sync": {"state": "disabled"},
            "lease": _lease_summary(identity.dataset_id),
            "feature_flags": _feature_flags(),
        }
    authority = _authority_state(
        profile, profile.dataset_id, identity.deployment_id
    )
    policy = SyncPolicy.objects.filter(
        profile=profile, dataset_id=profile.dataset_id
    ).first()
    mutation = SourceMutationState.objects.filter(
        deployment_id=identity.deployment_id
    ).first()
    pending_activation = ActivationIntent.objects.filter(
        deployment_id=identity.deployment_id,
        state__in=[
            ActivationIntent.State.PENDING,
            ActivationIntent.State.APPLYING,
        ],
    ).first()
    generations = _generation_records(profile, profile.dataset_id)
    workspaces = _workspace_records(profile, profile.dataset_id)
    jobs = _job_records(profile, profile.dataset_id)
    retention_holds = _retention_records(profile, profile.dataset_id)
    gc_plans = _gc_plan_records(profile, profile.dataset_id)
    sync_state = (
        next(
            (
                job["phase"] or job["status"]
                for job in jobs
                if job["operation"] == "sync_publish"
                and job["status"] in ACTIVE_JOB_STATES
            ),
            "idle",
        )
        if settings.VAULT_SYNC_ENABLED
        else "disabled"
    )
    payload = {
        "status": authority["status"],
        "reason_code": authority["reason_code"],
        "severity": (
            "info" if authority["status"] == "healthy" else "warning"
        ),
        "recommended_action": (
            "Review blocking reasons before changing authority."
            if authority["blocking_reasons"]
            else "No immediate operator action is required."
        ),
        "observed_at": observed_at,
        "environment": _environment_summary(identity),
        "profile": {
            "key": profile.key,
            "display_name": profile.display_name,
            "dataset_id": profile.dataset_id,
            "read_only": profile.read_only,
            "fingerprint": profile.fingerprint,
        },
        "authority": authority,
        "profiles": _profile_records(),
        "generations": generations,
        "workspaces": workspaces,
        "jobs": jobs,
        "audit": _audit_records(),
        "retention_holds": retention_holds,
        "gc_plans": gc_plans,
        "sync": {
            "state": sync_state,
            "mode": policy.mode if policy else settings.VAULT_SYNC_MODE,
            "promotion_mode": (
                policy.promotion_mode
                if policy
                else settings.VAULT_SYNC_PROMOTION_MODE
            ),
            "pending_epoch": policy.pending_epoch if policy else 0,
            "last_completed_epoch": (
                policy.last_completed_epoch if policy else 0
            ),
            "source_epoch": mutation.current_epoch if mutation else 0,
            "last_mutation_at": (
                mutation.last_mutation_at if mutation else None
            ),
        },
        "lease": _lease_summary(profile.dataset_id),
        "pending_activation": (
            {
                "public_id": str(pending_activation.public_id),
                "state": pending_activation.state,
                "target_generation_id": (
                    pending_activation.target_generation_id
                ),
            }
            if pending_activation
            else None
        ),
        "feature_flags": _feature_flags(),
    }
    payload["state_version"] = _state_digest(
        {
            "authority": {
                "status": authority["status"],
                "reason_code": authority["reason_code"],
                "remote": authority["remote"],
                "runtime": authority["runtime"],
                "critical_job": authority["critical_job"],
                "allowed_actions": authority["allowed_actions"],
                "blocking_reasons": authority["blocking_reasons"],
            },
            "profile": {
                "key": profile.key,
                "fingerprint": profile.fingerprint,
                "enabled": profile.enabled,
                "read_only": profile.read_only,
            },
            "sync": payload["sync"],
            "pending_activation": payload["pending_activation"],
            "generation_states": [
                item["state_digest"] for item in generations
            ],
            "workspace_states": [
                item["state_digest"] for item in workspaces
            ],
            "job_versions": [
                (item["public_id"], item["state_version"]) for item in jobs
            ],
            "retention_versions": [
                (
                    item["public_id"],
                    item["active"],
                    item["updated_at"],
                )
                for item in retention_holds
            ],
            "gc_plan_versions": [
                (
                    item["public_id"],
                    item["state"],
                    item["plan_digest"],
                    item["expires_at"],
                )
                for item in gc_plans
            ],
        }
    )
    return payload


def _feature_flags():
    return {
        "admin_mutations_enabled": settings.VAULT_ADMIN_MUTATIONS_ENABLED,
        "sync_enabled": settings.VAULT_SYNC_ENABLED,
        "restore_enabled": settings.VAULT_RESTORE_ENABLED,
        "staging_activation_enabled": (
            settings.STAGING_RUNTIME_ACTIVATION_ENABLED
        ),
        "production_activation_enabled": False,
        "gc_enabled": settings.VAULT_GC_ENABLED,
        "ui_profile_configuration_enabled": (
            settings.VAULT_UI_PROFILE_CONFIGURATION_ENABLED
        ),
        "ui_secret_entry_enabled": False,
    }
