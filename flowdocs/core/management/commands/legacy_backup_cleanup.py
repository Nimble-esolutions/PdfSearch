import json

from django.core.management.base import BaseCommand, CommandError

from core.artifact_cleanup import (
    CleanupError,
    apply_legacy_backup_cleanup,
    legacy_backup_cleanup_plan,
)


class Command(BaseCommand):
    help = (
        "Plan or explicitly apply bounded cleanup of retired flat SQLite "
        "startup backups"
    )

    def add_arguments(self, parser):
        parser.add_argument("operation", choices=("plan", "apply"))
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        try:
            if options["operation"] == "plan":
                result = legacy_backup_cleanup_plan()
            else:
                if not options["confirm"]:
                    raise CommandError("--confirm <plan-id> is required")
                result = apply_legacy_backup_cleanup(options["confirm"])
        except CleanupError as exc:
            raise CommandError(f"{exc.reason_code}: {exc}") from exc
        self.stdout.write(
            json.dumps(result, indent=2, sort_keys=True, default=str)
        )
