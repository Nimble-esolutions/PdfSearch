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
    for job in orphaned:
        job.status = "queued"
        job.started_at = None
        job.save(update_fields=["status", "started_at"])
        MaintenanceAuditEvent.objects.create(
            job=job,
            event_type="worker_died",
            payload={
                "previous_status": "running",
                "started_at": str(job.started_at),
                "note": "Recovered after worker restart; previous run may have crashed.",
            },
        )
        job.items.filter(status="running").update(
            status="queued", started_at=None
        )
    return orphaned.count()


def _should_evaluate_scheduler() -> bool:
    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None:
        return False
    if not env_identity.maintenance_scheduler_enabled:
        return False
    if not env_identity.is_backup_writer:
        return False
    if env_identity.backup_sync_mode.value == "manual":
        return False
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
