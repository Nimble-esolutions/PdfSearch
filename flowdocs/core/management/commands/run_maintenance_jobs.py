from __future__ import annotations

import os
import signal
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.maintenance import claim_next_job, run_job
from core.models import MaintenanceJob, MaintenanceAuditEvent

HEARTBEAT_FILE = "/tmp/worker_heartbeat"
ORPHAN_TIMEOUT_MINUTES = 5
SCHEDULER_INTERVAL_SECONDS = 60
LAST_SCHEDULER_TICK = "/tmp/worker_last_scheduler_tick"

_shutdown_flag = False


def _handle_shutdown(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True


def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(f"{os.getpid()}\n{int(time.time())}")
    except OSError:
        pass


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
        if job.kind == "sync_generation":
            _release_writer_lock_for_orphaned_job(job)

        job.status = "queued"
        job.started_at = None
        job.save(update_fields=["status", "started_at"])
        MaintenanceAuditEvent.objects.create(
            job=job,
            event_type="worker_died",
            payload={
                "previous_status": "running",
                "started_at": str(cutoff),
                "writer_lock_released": job.kind == "sync_generation",
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


def _release_writer_lock_for_orphaned_job(job):
    """Release any held global writer lock for an orphaned sync job."""
    try:
        from core.maintenance import get_global_writer, release_global_writer
        from core.maintenance import get_lease_status as _get_lease_status

        writer_status = get_global_writer()
        if writer_status and writer_status.get("lease_held"):
            lease_info = writer_status.get("lease", {})
            held_by = lease_info.get("instance_id", "")
            current_instance = os.environ.get("INSTANCE_ID", "")
            if held_by == current_instance or not held_by:
                release_global_writer(
                    reason=f"Crash recovery for orphaned job {job.public_id}"
                )
                print(f"[worker] Released stale writer lock for job {job.public_id}")

        try:
            lease_status = _get_lease_status()
            if lease_status and lease_status.get("held"):
                held_by = lease_status.get("instance_id", "")
                if held_by == current_instance or not held_by:
                    from django.core.cache import cache
                    cache.delete("global_writer_lease")
                    print(f"[worker] Released stale cache lease for {job.public_id}")
        except Exception:
            pass

    except Exception as e:
        print(f"[worker] Could not release writer lock for {job.public_id}: {e}")


_scheduler_logged_reason = False

def _should_evaluate_scheduler() -> bool:
    global _scheduler_logged_reason
    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None:
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: ENV_IDENTITY not available")
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
    if env_identity.backup_sync_mode.value == "manual":
        if not _scheduler_logged_reason:
            print("[scheduler] Disabled: BACKUP_SYNC_MODE is manual")
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
        from core.backup_policy import queue_backup_if_needed
        job = queue_backup_if_needed(min_interval_seconds=SCHEDULER_INTERVAL_SECONDS)
        _write_scheduler_tick()
        return 1 if job else 0
    except Exception:
        return 0


class Command(BaseCommand):
    help = "Run queued PdfSearch maintenance jobs with optional backup scheduling"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=3.0)

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)

        recovered = _recover_orphaned_jobs()
        if recovered:
            self.stdout.write(
                self.style.WARNING(
                    f"Recovered {recovered} orphaned job(s) from prior shutdown."
                )
            )

        last_scheduler_eval = 0

        while not _shutdown_flag:
            now = time.time()
            if now - last_scheduler_eval >= SCHEDULER_INTERVAL_SECONDS:
                queued = _evaluate_scheduler()
                if queued:
                    self.stdout.write(
                        self.style.SUCCESS("Scheduled backup queued.")
                    )
                last_scheduler_eval = now

            job = claim_next_job()
            if job is None:
                _write_heartbeat()
                if options["once"]:
                    return
                time.sleep(max(options["poll_seconds"], 0.5))
                continue

            self.stdout.write(f"Running maintenance job {job.public_id} ({job.kind})")
            finished = run_job(job)
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
