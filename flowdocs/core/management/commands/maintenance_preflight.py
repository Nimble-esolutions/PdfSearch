from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import MaintenanceAuditEvent, MaintenanceJob


OBSOLETE_GENERATION_KINDS = {"sync_generation", "restore_generation"}


class Command(BaseCommand):
    help = "Fence obsolete legacy generation jobs before deployment"

    def handle(self, *args, **options):
        running = MaintenanceJob.objects.filter(
            kind__in=OBSOLETE_GENERATION_KINDS,
            status__in=("running", "cancel_requested"),
        )
        if running.exists():
            raise CommandError("obsolete_generation_job_running")
        queued = list(
            MaintenanceJob.objects.filter(
                kind__in=OBSOLETE_GENERATION_KINDS, status="queued"
            )
        )
        for job in queued:
            job.status = "cancelled"
            job.finished_at = timezone.now()
            job.error_summary = "operation_replaced"
            job.save(
                update_fields=[
                    "status", "finished_at", "error_summary", "updated_at"
                ]
            )
            MaintenanceAuditEvent.objects.create(
                job=job,
                event_type="cancelled",
                payload={"reason_code": "operation_replaced"},
            )
        self.stdout.write(f"Cancelled {len(queued)} obsolete queued job(s).")
