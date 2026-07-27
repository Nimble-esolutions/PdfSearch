import json
import os

from django.core.management.base import BaseCommand, CommandError

from core.maintenance import run_job
from core.models import MaintenanceJob


class Command(BaseCommand):
    help = "Internal: execute one maintenance job inside an isolated workspace"

    def add_arguments(self, parser):
        parser.add_argument("--job-id", required=True)

    def handle(self, *args, **options):
        if os.environ.get("MAINTENANCE_CANDIDATE_EXECUTION") != "1":
            raise CommandError("candidate_execution_environment_required")
        try:
            job = MaintenanceJob.objects.get(public_id=options["job_id"])
        except MaintenanceJob.DoesNotExist as exc:
            raise CommandError("candidate_job_not_found") from exc
        finished = run_job(job)
        self.stdout.write(
            json.dumps(
                {
                    "job_id": str(finished.public_id),
                    "status": finished.status,
                    "completed_items": finished.completed_items,
                    "failed_items": finished.failed_items,
                },
                sort_keys=True,
            )
        )
        if finished.status not in {"completed", "failed", "cancelled"}:
            raise CommandError("candidate_job_incomplete")
