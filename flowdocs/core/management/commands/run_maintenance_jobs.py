from __future__ import annotations

import os
import signal
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.maintenance import claim_next_job, run_job
from core.models import MaintenanceJob, MaintenanceAuditEvent

HEARTBEAT_FILE = "/tmp/worker_heartbeat"
ORPHAN_TIMEOUT_MINUTES = 5

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


class Command(BaseCommand):
    help = "Run queued PdfSearch maintenance jobs"

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

        while not _shutdown_flag:
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