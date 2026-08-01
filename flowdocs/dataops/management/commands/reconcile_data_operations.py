from django.core.management.base import BaseCommand

from dataops.worker import reconcile_receipts


class Command(BaseCommand):
    help = "Execute and reconcile queued Data Operations pipelines"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        changed = reconcile_receipts(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Reconciled {changed} Data Operations receipt(s)."))
