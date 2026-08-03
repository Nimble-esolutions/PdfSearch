from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.maintenance import claim_next_job, run_job
from core.models import MaintenanceJob, MaintenanceAuditEvent
from core.worker_readiness import write_worker_heartbeat

ORPHAN_TIMEOUT_MINUTES = 5
SCHEDULER_INTERVAL_SECONDS = 60
LAST_SCHEDULER_TICK = "/tmp/worker_last_scheduler_tick"
logger = logging.getLogger(__name__)

_shutdown_flag = False


def _handle_shutdown(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True


def _write_heartbeat():
    try:
        write_worker_heartbeat()
    except OSError:
        pass


def _heartbeat_loop():
    """Keep liveness fresh while the main worker is processing a long job."""
    max_age = settings.MAINTENANCE_WORKER_HEARTBEAT_MAX_AGE_SECONDS
    interval = max(1.0, min(10.0, max_age / 3))
    while not _shutdown_flag:
        _write_heartbeat()
        time.sleep(interval)


def _write_scheduler_tick():
    try:
        with open(LAST_SCHEDULER_TICK, "w") as f:
            f.write(f"{os.getpid()}\n{int(time.time())}")
    except OSError:
        pass


def _recover_orphaned_jobs():
    cutoff = timezone.now() - timedelta(minutes=ORPHAN_TIMEOUT_MINUTES)
    orphaned = MaintenanceJob.objects.filter(
        status="running",
        started_at__lt=cutoff,
    )
    recovered = 0
    for job in orphaned:
        job.status = "queued"
        job.started_at = None
        job.save(update_fields=["status", "started_at"])
        MaintenanceAuditEvent.objects.create(
            job=job,
            event_type="worker_died",
            payload={
                "previous_status": "running",
                "started_at": str(cutoff),
                "writer_lock_released": False,
                "reason": "stale_running_job_recovered",
            },
        )
        job.items.filter(status="running").update(
            status="queued", started_at=None
        )
        recovered += 1

    if recovered:
        print(f"[worker] Recovered {recovered} orphaned job(s)")

    return recovered


_scheduler_logged_reason = False

def _should_evaluate_scheduler() -> bool:
    global _scheduler_logged_reason
    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None:
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: ENV_IDENTITY not available")
            _scheduler_logged_reason = True
        return False
    if not getattr(settings, "VAULT_SYNC_ENABLED", False):
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: VAULT_SYNC_ENABLED is not set")
            _scheduler_logged_reason = True
        return False
    if not env_identity.maintenance_scheduler_enabled:
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: MAINTENANCE_SCHEDULER_ENABLED is not set")
            _scheduler_logged_reason = True
        return False
    if not env_identity.is_backup_writer:
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: BACKUP_ROLE is not writer")
            _scheduler_logged_reason = True
        return False
    if getattr(settings, "VAULT_SYNC_MODE", "manual") in {
        "manual",
        "disabled",
    }:
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: VAULT_SYNC_MODE is manual or disabled")
            _scheduler_logged_reason = True
        return False
    if not _scheduler_logged_reason:
        print("[scheduler] Enabled")
        _scheduler_logged_reason = True
    return True


def _evaluate_scheduler() -> int:
    """Check backup policy and queue a sync if needed. Returns jobs queued."""
    if not _should_evaluate_scheduler():
        return 0
    try:
        from vaultops.services.sync import evaluate_sync_scheduler
        job = evaluate_sync_scheduler()
        _write_scheduler_tick()
        return 1 if job else 0
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", "scheduler_evaluation_failed")
        print(f"[scheduler] Evaluation failed: {reason_code}")
        return 0


def _run_dataops_cycle() -> None:
    """Reconcile DataOps always; enqueue automatic work only when enabled."""
    from dataops.job_scheduler import automatic_backup_scheduling_enabled
    from dataops.quarantine import cleanup_mirror_quarantines
    from dataops.worker import reconcile_receipts

    if automatic_backup_scheduling_enabled():
        from dataops.job_scheduler import queue_due_backup_jobs
        from dataops.worker import queue_backup_if_due

        queue_backup_if_due(trigger="scheduler")
        queue_due_backup_jobs()
    reconcile_receipts(limit=10)
    cleanup_mirror_quarantines()


def _execute_local_job(job):
    if (
        job.options.get("candidate_required")
        and not getattr(settings, "MAINTENANCE_CANDIDATE_EXECUTION", False)
    ):
        from core.candidate_maintenance import execute_candidate_job

        return execute_candidate_job(job)
    return run_job(job)


def _fail_unhandled_local_job(job, exc):
    """Persist a bounded failure instead of terminating the worker process."""
    reason_code = getattr(exc, "reason_code", "maintenance_job_failed")
    job.refresh_from_db()
    job.status = "failed"
    job.failed_items = max(
        job.failed_items,
        max(0, job.total_items - job.completed_items),
    )
    job.error_summary = reason_code
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "failed_items",
            "error_summary",
            "finished_at",
            "updated_at",
        ]
    )
    MaintenanceAuditEvent.objects.create(
        job=job,
        event_type="failed",
        payload={"reason_code": reason_code, "result": "failed"},
    )
    return job


def _requires_source_mutation_scope(job):
    """Candidate preparation owns the source barrier before copying."""
    return not (
        job.options.get("candidate_required")
        and not getattr(settings, "MAINTENANCE_CANDIDATE_EXECUTION", False)
    )


class Command(BaseCommand):
    help = "Run queued PdfSearch maintenance jobs with optional backup scheduling"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=3.0)

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)
        _write_heartbeat()
        threading.Thread(
            target=_heartbeat_loop,
            name="maintenance-worker-heartbeat",
            daemon=True,
        ).start()

        recovered = _recover_orphaned_jobs()
        if (
            getattr(settings, "VAULT_SYNC_ENABLED", False)
            or getattr(settings, "VAULT_RESTORE_ENABLED", False)
        ):
            from vaultops.services.jobs import recover_stale_jobs

            recovered += len(
                recover_stale_jobs(
                    stale_seconds=settings.VAULT_JOB_STALE_SECONDS
                )
            )
        if recovered:
            self.stdout.write(
                self.style.WARNING(
                    f"Recovered {recovered} orphaned job(s) from prior shutdown."
                )
            )

        last_scheduler_eval = 0
        last_dataops_eval = 0

        while not _shutdown_flag:
            now = time.time()
            if now - last_scheduler_eval >= SCHEDULER_INTERVAL_SECONDS:
                queued = _evaluate_scheduler()
                if queued:
                    self.stdout.write(
                        self.style.SUCCESS("Scheduled backup queued.")
                    )
                last_scheduler_eval = now

            if (
                getattr(settings, "DATAOPS_ENABLED", False)
                and now - last_dataops_eval >= SCHEDULER_INTERVAL_SECONDS
            ):
                try:
                    _run_dataops_cycle()
                except Exception as exc:
                    logger.warning("Data Operations reconciliation failed: %s", getattr(exc, "reason_code", "dataops_reconcile_failed"))
                last_dataops_eval = now

            vault_claim = None
            if (
                getattr(settings, "VAULT_SYNC_ENABLED", False)
                or getattr(settings, "VAULT_RESTORE_ENABLED", False)
            ):
                from vaultops.services.jobs import claim_next_job as claim_next_vault_job
                from vaultops.services.sync import worker_identity

                vault_claim = claim_next_vault_job(worker_id=worker_identity())
            if vault_claim is not None:
                from vaultops.services.sync import execute_claimed_job

                vault_job, token = vault_claim
                self.stdout.write(
                    f"Running vault job {vault_job.public_id} "
                    f"({vault_job.operation})"
                )
                finished = execute_claimed_job(vault_job, token)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Vault job {finished.public_id} {finished.status}"
                    )
                )
                _write_heartbeat()
                if options["once"]:
                    return
                continue

            job = claim_next_job()
            if job is None:
                _write_heartbeat()
                if options["once"]:
                    return
                time.sleep(max(options["poll_seconds"], 0.5))
                continue

            self.stdout.write(f"Running maintenance job {job.public_id} ({job.kind})")
            try:
                if (
                    getattr(settings, "VAULT_MUTATION_TRACKING_ENABLED", False)
                    and _requires_source_mutation_scope(job)
                ):
                    from vaultops.services.mutations import (
                        SnapshotBarrierActive,
                        mutation_scope,
                    )

                    try:
                        with mutation_scope(
                            category="maintenance",
                            relative_path=str(job.public_id),
                            operation=job.kind,
                        ):
                            finished = _execute_local_job(job)
                    except SnapshotBarrierActive:
                        job.status = "queued"
                        job.started_at = None
                        job.save(update_fields=["status", "started_at"])
                        MaintenanceAuditEvent.objects.create(
                            job=job,
                            event_type="retried",
                            payload={
                                "reason_code": "snapshot_barrier_active",
                                "result": "deferred",
                            },
                        )
                        _write_heartbeat()
                        if options["once"]:
                            return
                        continue
                else:
                    finished = _execute_local_job(job)
            except Exception as exc:
                logger.exception(
                    "Maintenance job %s failed outside item execution",
                    job.public_id,
                )
                finished = _fail_unhandled_local_job(job, exc)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Job {finished.public_id} {finished.status}: "
                    f"{finished.completed_items} completed, {finished.failed_items} failed"
                )
            )
            _write_heartbeat()

            if options["once"]:
                return

        self.stdout.write(self.style.WARNING("Shutting down maintenance worker."))
