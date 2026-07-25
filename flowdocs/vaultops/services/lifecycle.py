from django.db import transaction

from vaultops.models import ArtifactGeneration, RestoreWorkspace, VaultJob
from vaultops.services.audit import append_event


CONTROL_DB = "control"


class LifecycleConflict(Exception):
    def __init__(self, reason_code, *, current_state=None, requested_state=None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.current_state = current_state
        self.requested_state = requested_state


VAULT_TRANSITIONS = {
    ArtifactGeneration.VaultState.CANDIDATE: {
        ArtifactGeneration.VaultState.AUTHORITATIVE,
        ArtifactGeneration.VaultState.RETIRED,
        ArtifactGeneration.VaultState.INVALID,
    },
    ArtifactGeneration.VaultState.AUTHORITATIVE: {
        ArtifactGeneration.VaultState.CANDIDATE,
        ArtifactGeneration.VaultState.RETIRED,
    },
    ArtifactGeneration.VaultState.RETIRED: {
        ArtifactGeneration.VaultState.CANDIDATE,
    },
    ArtifactGeneration.VaultState.UNKNOWN: {
        ArtifactGeneration.VaultState.CANDIDATE,
        ArtifactGeneration.VaultState.AUTHORITATIVE,
        ArtifactGeneration.VaultState.INVALID,
        ArtifactGeneration.VaultState.LEGACY_READ_ONLY,
    },
    ArtifactGeneration.VaultState.INVALID: {
        ArtifactGeneration.VaultState.CANDIDATE,
    },
    ArtifactGeneration.VaultState.LEGACY_READ_ONLY: {
        ArtifactGeneration.VaultState.RETIRED,
    },
}

RUNTIME_TRANSITIONS = {
    ArtifactGeneration.RuntimeState.INACTIVE: {
        ArtifactGeneration.RuntimeState.PENDING,
    },
    ArtifactGeneration.RuntimeState.PENDING: {
        ArtifactGeneration.RuntimeState.APPLYING,
        ArtifactGeneration.RuntimeState.ACTIVATION_FAILED,
        ArtifactGeneration.RuntimeState.INACTIVE,
    },
    ArtifactGeneration.RuntimeState.APPLYING: {
        ArtifactGeneration.RuntimeState.ACTIVE,
        ArtifactGeneration.RuntimeState.ROLLBACK_PENDING,
        ArtifactGeneration.RuntimeState.ACTIVATION_FAILED,
    },
    ArtifactGeneration.RuntimeState.ACTIVE: {
        ArtifactGeneration.RuntimeState.PREVIOUS,
        ArtifactGeneration.RuntimeState.ROLLBACK_PENDING,
    },
    ArtifactGeneration.RuntimeState.PREVIOUS: {
        ArtifactGeneration.RuntimeState.ACTIVE,
        ArtifactGeneration.RuntimeState.INACTIVE,
    },
    ArtifactGeneration.RuntimeState.ROLLBACK_PENDING: {
        ArtifactGeneration.RuntimeState.ROLLED_BACK,
        ArtifactGeneration.RuntimeState.ACTIVATION_FAILED,
    },
    ArtifactGeneration.RuntimeState.ROLLED_BACK: {
        ArtifactGeneration.RuntimeState.PREVIOUS,
        ArtifactGeneration.RuntimeState.ACTIVE,
    },
    ArtifactGeneration.RuntimeState.ACTIVATION_FAILED: {
        ArtifactGeneration.RuntimeState.PENDING,
    },
    ArtifactGeneration.RuntimeState.UNKNOWN: set(
        ArtifactGeneration.RuntimeState.values
    )
    - {ArtifactGeneration.RuntimeState.UNKNOWN},
}

WORKSPACE_TRANSITIONS = {
    RestoreWorkspace.State.ABSENT: {RestoreWorkspace.State.PLANNED},
    RestoreWorkspace.State.PLANNED: {
        RestoreWorkspace.State.DOWNLOADING,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.DOWNLOADING: {
        RestoreWorkspace.State.DOWNLOAD_PAUSED,
        RestoreWorkspace.State.DOWNLOADED,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.DOWNLOAD_PAUSED: {
        RestoreWorkspace.State.DOWNLOADING,
        RestoreWorkspace.State.EXPIRED,
    },
    RestoreWorkspace.State.DOWNLOADED: {
        RestoreWorkspace.State.VALIDATING,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.VALIDATING: {
        RestoreWorkspace.State.SANITIZING,
        RestoreWorkspace.State.MIGRATION_REHEARSAL,
        RestoreWorkspace.State.ACTIVATION_READY,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.SANITIZING: {
        RestoreWorkspace.State.MIGRATION_REHEARSAL,
        RestoreWorkspace.State.ACTIVATION_READY,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.MIGRATION_REHEARSAL: {
        RestoreWorkspace.State.ACTIVATION_READY,
        RestoreWorkspace.State.FAILED,
    },
    RestoreWorkspace.State.ACTIVATION_READY: {
        RestoreWorkspace.State.EXPIRED,
    },
    RestoreWorkspace.State.FAILED: {
        RestoreWorkspace.State.EXPIRED,
    },
    RestoreWorkspace.State.EXPIRED: set(),
}

JOB_TRANSITIONS = {
    VaultJob.Status.QUEUED: {
        VaultJob.Status.CLAIMED,
        VaultJob.Status.CANCELLED,
    },
    VaultJob.Status.CLAIMED: {
        VaultJob.Status.RUNNING,
        VaultJob.Status.STALE,
        VaultJob.Status.CANCELLING,
    },
    VaultJob.Status.RUNNING: {
        VaultJob.Status.WAITING,
        VaultJob.Status.CANCELLING,
        VaultJob.Status.RETRYABLE_FAILED,
        VaultJob.Status.TERMINAL_FAILED,
        VaultJob.Status.SUCCEEDED,
        VaultJob.Status.STALE,
    },
    VaultJob.Status.WAITING: {
        VaultJob.Status.RUNNING,
        VaultJob.Status.CANCELLING,
        VaultJob.Status.STALE,
    },
    VaultJob.Status.CANCELLING: {
        VaultJob.Status.CANCELLED,
        VaultJob.Status.SUCCEEDED,
    },
    VaultJob.Status.RETRYABLE_FAILED: {VaultJob.Status.QUEUED},
    VaultJob.Status.STALE: {VaultJob.Status.QUEUED},
    VaultJob.Status.CANCELLED: set(),
    VaultJob.Status.TERMINAL_FAILED: set(),
    VaultJob.Status.SUCCEEDED: set(),
}


def _guard_transition(current, requested, transitions, reason_code):
    if requested == current:
        return
    if requested not in transitions.get(current, set()):
        raise LifecycleConflict(
            reason_code,
            current_state=current,
            requested_state=requested,
        )


def transition_generation_vault_state(
    generation,
    requested_state,
    *,
    correlation_id,
    actor_id=None,
    actor_name="",
    reason_code="",
):
    with transaction.atomic(using=CONTROL_DB):
        locked = ArtifactGeneration.objects.select_for_update().get(pk=generation.pk)
        current = locked.vault_state
        _guard_transition(
            current,
            requested_state,
            VAULT_TRANSITIONS,
            "invalid_vault_transition",
        )
        if requested_state == current:
            return locked
        locked.vault_state = requested_state
        locked.save(update_fields=["vault_state", "updated_at"])
        append_event(
            action="generation_vault_state_changed",
            result="succeeded",
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
            before_state={"vault_state": current},
            after_state={"vault_state": requested_state},
            evidence={
                "dataset_id": locked.dataset_id,
                "generation_id": locked.generation_id,
            },
        )
        return locked


def transition_generation_runtime_state(
    generation,
    requested_state,
    *,
    correlation_id,
    actor_id=None,
    actor_name="",
    reason_code="",
):
    with transaction.atomic(using=CONTROL_DB):
        locked = ArtifactGeneration.objects.select_for_update().get(pk=generation.pk)
        current = locked.runtime_state
        _guard_transition(
            current,
            requested_state,
            RUNTIME_TRANSITIONS,
            "invalid_runtime_transition",
        )
        if requested_state == current:
            return locked
        locked.runtime_state = requested_state
        locked.save(update_fields=["runtime_state", "updated_at"])
        append_event(
            action="generation_runtime_state_changed",
            result="succeeded",
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
            before_state={"runtime_state": current},
            after_state={"runtime_state": requested_state},
            evidence={
                "deployment_id": locked.deployment_id,
                "generation_id": locked.generation_id,
            },
        )
        return locked


def transition_workspace(
    workspace,
    requested_state,
    *,
    correlation_id,
    actor_id=None,
    actor_name="",
    reason_code="",
):
    with transaction.atomic(using=CONTROL_DB):
        locked = RestoreWorkspace.objects.select_for_update().get(pk=workspace.pk)
        current = locked.state
        _guard_transition(
            current,
            requested_state,
            WORKSPACE_TRANSITIONS,
            "invalid_workspace_transition",
        )
        if requested_state == current:
            return locked
        locked.state = requested_state
        locked.save(update_fields=["state", "updated_at"])
        append_event(
            action="workspace_state_changed",
            result="succeeded",
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
            before_state={"workspace_state": current},
            after_state={"workspace_state": requested_state},
            evidence={"workspace_id": str(locked.public_id)},
        )
        return locked


def transition_job(
    job,
    requested_state,
    *,
    correlation_id=None,
    reason_code="",
    safe_error_code="",
):
    with transaction.atomic(using=CONTROL_DB):
        locked = VaultJob.objects.select_for_update().get(pk=job.pk)
        current = locked.status
        _guard_transition(
            current,
            requested_state,
            JOB_TRANSITIONS,
            "invalid_job_transition",
        )
        if requested_state == current:
            return locked
        locked.status = requested_state
        locked.state_version += 1
        if safe_error_code:
            locked.safe_error_code = safe_error_code
        locked.save(
            update_fields=[
                "status",
                "state_version",
                "safe_error_code",
                "updated_at",
            ]
        )
        append_event(
            action="job_state_changed",
            result="succeeded",
            correlation_id=correlation_id or locked.correlation_id,
            actor_id=locked.requested_by_id,
            actor_name=locked.requested_by_name,
            job_public_id=locked.public_id,
            reason_code=reason_code,
            before_state={"job_state": current},
            after_state={"job_state": requested_state},
            safe_error_code=safe_error_code,
        )
        return locked
