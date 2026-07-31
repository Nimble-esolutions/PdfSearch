import os
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.artifact_vault import ArtifactVault
from vaultops.models import (
    ArtifactGeneration,
    SourceMutationState,
    SourceSnapshot,
    SyncPolicy,
    VaultJob,
)
from vaultops.services.audit import append_event
from vaultops.services.jobs import (
    cancellation_requested,
    complete_owned_job,
    fail_owned_job,
    heartbeat_job,
    start_job,
)
from vaultops.services.profiles import materialize_environment_profile
from vaultops.services.publication import (
    PublicationCancelled,
    PromotionError,
    promote_candidate,
    publish_snapshot_candidate,
)
from vaultops.services.restore import RestoreCancelled, run_restore_job
from vaultops.services.snapshot import SnapshotCancelled, create_consistent_snapshot


class SyncPolicyError(RuntimeError):
    reason_code = "sync_policy_blocked"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def materialize_sync_policy(profile):
    policy, _ = SyncPolicy.objects.update_or_create(
        profile=profile,
        dataset_id=settings.ENV_IDENTITY.dataset_id,
        defaults={
            "mode": settings.VAULT_SYNC_MODE,
            "promotion_mode": settings.VAULT_SYNC_PROMOTION_MODE,
            "quiet_period_seconds": settings.VAULT_SYNC_QUIET_PERIOD_SECONDS,
            "interval_seconds": settings.VAULT_SYNC_INTERVAL_SECONDS,
            "max_lag_seconds": settings.VAULT_SYNC_MAX_LAG_SECONDS,
            "environment_locked": True,
        },
    )
    return policy


def _mutation_state():
    state, _ = SourceMutationState.objects.get_or_create(
        deployment_id=settings.ENV_IDENTITY.deployment_id
    )
    return state


def queue_sync_job(*, trigger, requested_by_id=None, requested_by_name=""):
    if not settings.VAULT_SYNC_ENABLED:
        raise SyncPolicyError("vault_sync_disabled")
    profile = materialize_environment_profile()
    policy = materialize_sync_policy(profile)
    if policy.mode == SyncPolicy.Mode.DISABLED:
        raise SyncPolicyError("vault_sync_policy_disabled")
    state = _mutation_state()
    if (
        policy.last_run_at is not None
        and state.current_epoch <= policy.last_completed_epoch
    ):
        return None
    idempotency_key = (
        f"sync:{settings.ENV_IDENTITY.deployment_id}:"
        f"{profile.fingerprint[:16]}:{state.current_epoch}"
    )
    with transaction.atomic(using="control"):
        job, created = VaultJob.objects.get_or_create(
            operation="sync_publish",
            idempotency_key=idempotency_key,
            defaults={
                "profile": profile,
                "profile_fingerprint": profile.fingerprint,
                "dataset_id": profile.dataset_id,
                "requested_by_id": requested_by_id,
                "requested_by_name": requested_by_name,
                "progress": {
                    "trigger": trigger,
                    "source_epoch": state.current_epoch,
                },
            },
        )
        policy.pending_epoch = max(policy.pending_epoch, state.current_epoch)
        policy.last_evaluated_at = timezone.now()
        policy.save(
            update_fields=[
                "pending_epoch",
                "last_evaluated_at",
                "updated_at",
            ]
        )
        if created:
            append_event(
                action="job_queued",
                result="succeeded",
                correlation_id=job.correlation_id,
                actor_id=requested_by_id,
                actor_name=requested_by_name,
                job_public_id=job.public_id,
                after_state={
                    "job_state": job.status,
                    "operation": job.operation,
                },
                evidence={
                    "trigger": trigger,
                    "source_epoch": state.current_epoch,
                },
            )
    return job


def evaluate_sync_scheduler():
    """Coalesce durable source epochs into at most one pending publication job."""
    if (
        not settings.VAULT_SYNC_ENABLED
        or not settings.ENV_IDENTITY.maintenance_scheduler_enabled
    ):
        return None
    profile = materialize_environment_profile()
    policy = materialize_sync_policy(profile)
    if policy.mode in {SyncPolicy.Mode.DISABLED, SyncPolicy.Mode.MANUAL}:
        return None
    state = _mutation_state()
    now = timezone.now()
    policy.last_evaluated_at = now
    policy.save(update_fields=["last_evaluated_at", "updated_at"])
    if (
        policy.last_run_at is not None
        and state.current_epoch <= policy.last_completed_epoch
    ):
        return None
    if policy.mode == SyncPolicy.Mode.SCHEDULED:
        if (
            policy.last_run_at
            and (now - policy.last_run_at).total_seconds()
            < policy.interval_seconds
        ):
            return None
    if policy.mode == SyncPolicy.Mode.CONTINUOUS_COALESCED:
        if (
            state.last_mutation_at
            and (now - state.last_mutation_at).total_seconds()
            < policy.quiet_period_seconds
        ):
            policy.pending_epoch = max(policy.pending_epoch, state.current_epoch)
            policy.save(update_fields=["pending_epoch", "updated_at"])
            return None
    return queue_sync_job(trigger=policy.mode)


def _cancellation_check(job_public_id):
    return cancellation_requested(job_public_id)


def _heartbeat(job, token, fencing_epoch):
    def send(**kwargs):
        heartbeat_job(
            job.public_id,
            token=token,
            fencing_epoch=fencing_epoch,
            **kwargs,
        )

    return send


def _run_publish(job, token, fencing_epoch, vault):
    from vaultops.services.snapshot import eligible_finalized_snapshot

    profile = job.profile or materialize_environment_profile(vault)
    policy = materialize_sync_policy(profile)
    snapshot = eligible_finalized_snapshot(job)
    if snapshot is None:
        snapshot = create_consistent_snapshot(
            job,
            cancellation_check=lambda: _cancellation_check(job.public_id),
            progress_callback=_heartbeat(job, token, fencing_epoch),
        )
    generation = publish_snapshot_candidate(
        snapshot=snapshot,
        profile=profile,
        job=job,
        vault=vault,
        cancellation_check=lambda: _cancellation_check(job.public_id),
        heartbeat=_heartbeat(job, token, fencing_epoch),
    )
    with transaction.atomic(using="control"):
        policy = SyncPolicy.objects.select_for_update().get(pk=policy.pk)
        state = _mutation_state()
        policy.last_completed_epoch = max(
            policy.last_completed_epoch, snapshot.included_epoch
        )
        policy.pending_epoch = (
            state.current_epoch
            if state.current_epoch > snapshot.included_epoch
            else 0
        )
        policy.last_run_at = timezone.now()
        policy.save(
            update_fields=[
                "last_completed_epoch",
                "pending_epoch",
                "last_run_at",
                "updated_at",
            ]
        )
    if policy.promotion_mode == SyncPolicy.PromotionMode.AUTO_AFTER_VALIDATION:
        try:
            VaultJob.objects.create(
                operation="promote_generation",
                profile=profile,
                profile_fingerprint=profile.fingerprint,
                dataset_id=generation.dataset_id,
                generation_id=generation.generation_id,
                manifest_digest=generation.manifest_digest,
                idempotency_key=(
                    f"promote:{generation.dataset_id}:"
                    f"{generation.generation_id}:{generation.manifest_digest}"
                ),
                progress={"confirmation": "policy:auto_after_validation"},
            )
        except IntegrityError:
            pass
    return generation


def _run_promotion(job, vault):
    if not job.profile_id:
        raise PromotionError("profile_required")
    try:
        generation = ArtifactGeneration.objects.get(
            profile=job.profile,
            dataset_id=job.dataset_id,
            generation_id=job.generation_id,
        )
    except ArtifactGeneration.DoesNotExist as exc:
        raise PromotionError("generation_not_found") from exc
    policy = materialize_sync_policy(job.profile)
    confirmed = (
        policy.promotion_mode == SyncPolicy.PromotionMode.AUTO_AFTER_VALIDATION
        and job.progress.get("confirmation") == "policy:auto_after_validation"
    ) or str(job.progress.get("confirmation", "")).startswith("operator:")
    generation, _ = promote_candidate(
        generation=generation,
        job=job,
        profile=job.profile,
        confirmed=confirmed,
        vault=vault,
    )
    return generation


TERMINAL_ERROR_CODES = {
    "vault_sync_disabled",
    "vault_sync_policy_disabled",
    "writer_environment_required",
    "profile_identity_mismatch",
    "profile_fingerprint_changed",
    "environment_identity_incomplete",
    "restore_environment_required",
    "restore_dataset_mismatch",
    "restore_profile_read_only_required",
    "conditional_operations_unsupported",
    "registration_identity_mismatch",
    "typed_confirmation_required",
    "generation_not_candidate",
    "fresh_validation_required",
    "vault_restore_disabled",
    "vault_admin_mutations_disabled",
    "profile_required",
    "generation_compatibility_failed",
    "production_restore_sanitization_required",
    "restore_capacity_bytes_insufficient",
    "restore_capacity_inodes_insufficient",
    "restore_object_digest_mismatch",
    "restore_checkpoint_digest_mismatch",
    "restored_database_invalid",
    "restored_database_integrity_failed",
    "restored_database_foreign_keys_failed",
    "restored_faiss_count_mismatch",
    "restore_sanitization_validation_failed",
    "migration_rehearsal_failed",
    "migration_rehearsal_integrity_failed",
    "migration_rehearsal_foreign_keys_failed",
    "runtime_workspace_exists",
}


def execute_claimed_job(job, token, *, vault=None):
    """Run one fenced VaultJob and persist only typed error codes."""
    fencing_epoch = job.fencing_epoch
    if cancellation_requested(job.public_id):
        return complete_owned_job(
            job.public_id,
            token=token,
            fencing_epoch=fencing_epoch,
        )
    job = start_job(
        job.public_id,
        token=token,
        fencing_epoch=fencing_epoch,
    )
    irreversible_completed = False
    try:
        if job.operation == "sync_publish":
            vault = vault or ArtifactVault()
            result = _run_publish(job, token, fencing_epoch, vault)
            irreversible_completed = bool(result.manifest_digest)
        elif job.operation == "promote_generation":
            vault = vault or ArtifactVault()
            result = _run_promotion(job, vault)
            irreversible_completed = True
        elif job.operation == "restore_generation":
            result = run_restore_job(
                job,
                vault=vault,
                cancellation_check=lambda: _cancellation_check(job.public_id),
                heartbeat=_heartbeat(job, token, fencing_epoch),
            )
        else:
            raise SyncPolicyError("unsupported_vault_job_operation")
        return complete_owned_job(
            job.public_id,
            token=token,
            fencing_epoch=fencing_epoch,
            irreversible_completed=irreversible_completed,
        )
    except (PublicationCancelled, SnapshotCancelled, RestoreCancelled):
        return complete_owned_job(
            job.public_id,
            token=token,
            fencing_epoch=fencing_epoch,
            irreversible_completed=irreversible_completed,
        )
    except Exception as exc:
        if cancellation_requested(job.public_id):
            return complete_owned_job(
                job.public_id,
                token=token,
                fencing_epoch=fencing_epoch,
                irreversible_completed=irreversible_completed,
            )
        safe_error_code = getattr(
            exc, "reason_code", "vault_job_failed"
        )
        return fail_owned_job(
            job.public_id,
            token=token,
            fencing_epoch=fencing_epoch,
            safe_error_code=safe_error_code,
            retryable=getattr(
                exc,
                "retryable",
                safe_error_code not in TERMINAL_ERROR_CODES,
            ),
        )


def worker_identity():
    return (
        getattr(settings.ENV_IDENTITY, "instance_id", "")
        or f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
