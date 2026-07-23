from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from core.maintenance import claim_next_job, run_job


class Command(BaseCommand):
    help = "Run queued PdfSearch maintenance jobs"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one job")
        parser.add_argument("--poll-seconds", type=float, default=3.0)

    def handle(self, *args, **options):
        while True:
            job = claim_next_job()
            if job is None:
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
            if options["once"]:
                return
