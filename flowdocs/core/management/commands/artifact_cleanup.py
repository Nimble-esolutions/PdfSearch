import json

from django.core.management.base import BaseCommand, CommandError

from core.artifact_cleanup import CleanupError, apply_cleanup, cleanup_plan


class Command(BaseCommand):
    help = "Plan or explicitly apply bounded local-artifact cleanup"

    def add_arguments(self, parser):
        parser.add_argument("operation", choices=("plan", "apply"))
        parser.add_argument("--confirm", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        try:
            if options["operation"] == "plan":
                result = cleanup_plan()
            else:
                if not options["confirm"]:
                    raise CommandError("--confirm <plan-id> is required")
                result = apply_cleanup(options["confirm"])
        except CleanupError as exc:
            raise CommandError(f"{exc.reason_code}: {exc}") from exc
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str))
