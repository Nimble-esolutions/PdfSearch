from django.utils import timezone

from vaultops.models import (
    ArtifactGeneration,
    RuntimePointerObservation,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultJob,
)


def build_authority_state(*, profile_key, dataset_id, deployment_id):
    """Return independent remote, local-projection, job, and runtime authority."""
    observed_at = timezone.now()
    try:
        profile = VaultConnectionProfile.objects.get(key=profile_key, enabled=True)
    except VaultConnectionProfile.DoesNotExist:
        return {
            "status": "unknown",
            "reason_code": "profile_unavailable",
            "observed_at": observed_at,
            "remote": {"state": "unknown"},
            "runtime": {"state": "unknown"},
            "critical_job": None,
            "allowed_actions": [],
            "blocking_reasons": ["profile_unavailable"],
        }

    projection = (
        VaultDatasetProjection.objects.filter(
            profile=profile, dataset_id=dataset_id
        )
        .order_by("-inventory_observed_at")
        .first()
    )
    runtime = (
        RuntimePointerObservation.objects.filter(deployment_id=deployment_id)
        .order_by("-observed_at")
        .first()
    )
    critical_job = (
        VaultJob.objects.filter(
            profile=profile,
            dataset_id=dataset_id,
            status__in=[
                VaultJob.Status.CLAIMED,
                VaultJob.Status.RUNNING,
                VaultJob.Status.WAITING,
                VaultJob.Status.CANCELLING,
                VaultJob.Status.STALE,
                VaultJob.Status.TERMINAL_FAILED,
            ],
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
    if critical_job and critical_job.status in {
        VaultJob.Status.STALE,
        VaultJob.Status.TERMINAL_FAILED,
    }:
        blocking_reasons.append("critical_job_unhealthy")

    allowed_actions = ["refresh_inventory"]
    if projection and projection.inventory_state == "verified":
        allowed_actions.extend(["plan_restore", "compare_generations"])

    remote_state = {
        "state": projection.inventory_state if projection else "unknown",
        "authoritative_generation_id": (
            projection.authoritative_generation_id if projection else ""
        ),
        "pointer_digest": projection.pointer_digest if projection else "",
        "observed_at": projection.inventory_observed_at if projection else None,
    }
    runtime_state = {
        "state": runtime.status if runtime else "unknown",
        "active_generation_id": runtime.active_generation_id if runtime else "",
        "previous_generation_id": (
            runtime.previous_generation_id if runtime else ""
        ),
        "projected_runtime_state": (
            runtime_generation.runtime_state if runtime_generation else "unknown"
        ),
        "observed_at": runtime.observed_at if runtime else None,
    }
    return {
        "status": "healthy" if not blocking_reasons else "degraded",
        "reason_code": "" if not blocking_reasons else blocking_reasons[0],
        "observed_at": observed_at,
        "profile": {
            "key": profile.key,
            "dataset_id": dataset_id,
            "read_only": profile.read_only,
            "fingerprint": profile.fingerprint,
        },
        "remote": remote_state,
        "runtime": runtime_state,
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
