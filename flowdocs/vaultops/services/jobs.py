import hashlib
import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from vaultops.models import VaultJob
from vaultops.services.audit import append_event
from vaultops.services.lifecycle import (
    CONTROL_DB,
    LifecycleConflict,
    _guard_transition,
    JOB_TRANSITIONS,
)


class ClaimRejected(LifecycleConflict):
    pass


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_owner(job, token, fencing_epoch):
    supplied_hash = _token_hash(token)
    if (
        not job.claim_token_hash
        or not hmac.compare_digest(job.claim_token_hash, supplied_hash)
        or job.fencing_epoch != fencing_epoch
    ):
        raise ClaimRejected(
            "stale_job_owner",
            current_state=job.status,
            requested_state=job.status,
        )


def claim_job(job_public_id, *, worker_id, now=None):
    now = now or timezone.now()
    token = secrets.token_urlsafe(32)
    with transaction.atomic(using=CONTROL_DB):
        job = VaultJob.objects.select_for_update().get(public_id=job_public_id)
        _guard_transition(
            job.status,
            VaultJob.Status.CLAIMED,
            JOB_TRANSITIONS,
            "job_not_claimable",
        )
        previous = job.status
        job.status = VaultJob.Status.CLAIMED
        job.claim_token_hash = _token_hash(token)
        job.claimed_by = worker_id
        job.fencing_epoch += 1
        job.heartbeat_at = now
        job.state_version += 1
        job.save(
            update_fields=[
                "status",
                "claim_token_hash",
                "claimed_by",
                "fencing_epoch",
                "heartbeat_at",
                "state_version",
                "updated_at",
            ]
        )
        append_event(
            action="job_claimed",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            before_state={"job_state": previous},
            after_state={
                "job_state": job.status,
                "fencing_epoch": job.fencing_epoch,
                "worker_id": worker_id,
            },
        )
    return job, token


def start_job(job_public_id, *, token, fencing_epoch, now=None):
    now = now or timezone.now()
    with transaction.atomic(using=CONTROL_DB):
        job = VaultJob.objects.select_for_update().get(public_id=job_public_id)
        _verify_owner(job, token, fencing_epoch)
        _guard_transition(
            job.status,
            VaultJob.Status.RUNNING,
            JOB_TRANSITIONS,
            "job_not_startable",
        )
        job.status = VaultJob.Status.RUNNING
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.state_version += 1
        job.save(
            update_fields=[
                "status",
                "started_at",
                "heartbeat_at",
                "state_version",
                "updated_at",
            ]
        )
        append_event(
            action="job_started",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            before_state={"job_state": VaultJob.Status.CLAIMED},
            after_state={
                "job_state": job.status,
                "fencing_epoch": job.fencing_epoch,
            },
        )
        return job


def heartbeat_job(
    job_public_id,
    *,
    token,
    fencing_epoch,
    phase=None,
    progress=None,
    now=None,
):
    now = now or timezone.now()
    with transaction.atomic(using=CONTROL_DB):
        job = VaultJob.objects.select_for_update().get(public_id=job_public_id)
        _verify_owner(job, token, fencing_epoch)
        if job.status not in {
            VaultJob.Status.CLAIMED,
            VaultJob.Status.RUNNING,
            VaultJob.Status.WAITING,
            VaultJob.Status.CANCELLING,
        }:
            raise ClaimRejected(
                "job_not_owned",
                current_state=job.status,
                requested_state=job.status,
            )
        fields = ["heartbeat_at", "updated_at"]
        job.heartbeat_at = now
        if phase is not None:
            job.phase = phase
            fields.append("phase")
        if progress is not None:
            job.progress = progress
            fields.append("progress")
        job.save(update_fields=fields)
        return job


def request_cancellation(job_public_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic(using=CONTROL_DB):
        job = VaultJob.objects.select_for_update().get(public_id=job_public_id)
        if job.status == VaultJob.Status.QUEUED:
            requested = VaultJob.Status.CANCELLED
        else:
            requested = VaultJob.Status.CANCELLING
        _guard_transition(
            job.status,
            requested,
            JOB_TRANSITIONS,
            "job_not_cancellable",
        )
        previous = job.status
        job.status = requested
        job.cancellation_requested_at = now
        job.state_version += 1
        if requested == VaultJob.Status.CANCELLED:
            job.finished_at = now
        job.save(
            update_fields=[
                "status",
                "cancellation_requested_at",
                "finished_at",
                "state_version",
                "updated_at",
            ]
        )
        append_event(
            action="job_cancellation_requested",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            before_state={"job_state": previous},
            after_state={"job_state": requested},
        )
        return job


def recover_stale_jobs(*, stale_seconds, now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=stale_seconds)
    recovered = []
    candidate_ids = list(
        VaultJob.objects.filter(
            status__in=[
                VaultJob.Status.CLAIMED,
                VaultJob.Status.RUNNING,
                VaultJob.Status.WAITING,
            ],
            heartbeat_at__lt=cutoff,
        ).values_list("pk", flat=True)
    )
    for job_id in candidate_ids:
        with transaction.atomic(using=CONTROL_DB):
            job = VaultJob.objects.select_for_update().get(pk=job_id)
            if job.heartbeat_at is None or job.heartbeat_at >= cutoff:
                continue
            previous = job.status
            _guard_transition(
                previous,
                VaultJob.Status.STALE,
                JOB_TRANSITIONS,
                "job_not_recoverable",
            )
            job.status = VaultJob.Status.STALE
            job.claim_token_hash = ""
            job.claimed_by = ""
            job.state_version += 1
            job.safe_error_code = "worker_heartbeat_expired"
            job.save(
                update_fields=[
                    "status",
                    "claim_token_hash",
                    "claimed_by",
                    "state_version",
                    "safe_error_code",
                    "updated_at",
                ]
            )
            append_event(
                action="job_marked_stale",
                result="succeeded",
                correlation_id=job.correlation_id,
                job_public_id=job.public_id,
                before_state={"job_state": previous},
                after_state={
                    "job_state": job.status,
                    "fencing_epoch": job.fencing_epoch,
                },
                safe_error_code="worker_heartbeat_expired",
            )
            recovered.append(job.public_id)
    return recovered


def requeue_job(job_public_id):
    with transaction.atomic(using=CONTROL_DB):
        job = VaultJob.objects.select_for_update().get(public_id=job_public_id)
        _guard_transition(
            job.status,
            VaultJob.Status.QUEUED,
            JOB_TRANSITIONS,
            "job_not_retryable",
        )
        previous = job.status
        job.status = VaultJob.Status.QUEUED
        job.retry_count += 1
        job.safe_error_code = ""
        job.heartbeat_at = None
        job.state_version += 1
        job.save(
            update_fields=[
                "status",
                "retry_count",
                "safe_error_code",
                "heartbeat_at",
                "state_version",
                "updated_at",
            ]
        )
        append_event(
            action="job_requeued",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            before_state={"job_state": previous},
            after_state={"job_state": job.status},
        )
        return job
