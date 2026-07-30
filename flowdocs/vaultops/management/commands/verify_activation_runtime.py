import json
from django.core.management.base import BaseCommand, CommandError
from vaultops.services.runtime_verification import (
    RUNTIME_VERIFICATION_ERRORS,
    verify_activation_runtime,
)


class Command(BaseCommand):
    help = "Fail-closed smoke verification for the active runtime generation"

    def add_arguments(self, parser):
        parser.add_argument("--intent-id", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        intent_id = options["intent_id"]
        try:
            evidence = verify_activation_runtime(intent_id)
        except RUNTIME_VERIFICATION_ERRORS as exc:
            reason_code = getattr(
                exc, "reason_code", "activation_runtime_smoke_failed"
            )
            raise CommandError(reason_code) from exc
        if options["json"]:
            self.stdout.write(json.dumps(evidence, sort_keys=True))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Activation runtime verified: "
                    f"{evidence['generation_id']}"
                )
            )
