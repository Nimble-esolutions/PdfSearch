import json

from django.core.management.base import BaseCommand, CommandError

from vaultops.services.certification import (
    RecoveryCertificationError,
    verify_recovery_certification,
)
from vaultops.services.runtime_verification import RUNTIME_VERIFICATION_ERRORS


class Command(BaseCommand):
    help = "Verify fail-closed recovery-certification evidence"

    def add_arguments(self, parser):
        parser.add_argument("--intent-id", required=True)
        parser.add_argument("--generation-id", required=True)
        parser.add_argument("--manifest-digest", required=True)
        parser.add_argument("--require-initial", action="store_true")

    def handle(self, *args, **options):
        try:
            evidence = verify_recovery_certification(
                intent_id=options["intent_id"],
                expected_generation_id=options["generation_id"],
                expected_manifest_digest=options["manifest_digest"],
                require_initial=options["require_initial"],
            )
        except (RecoveryCertificationError, *RUNTIME_VERIFICATION_ERRORS) as exc:
            reason_code = getattr(
                exc,
                "reason_code",
                "recovery_certification_runtime_verification_failed",
            )
            raise CommandError(reason_code) from exc
        self.stdout.write(json.dumps(evidence, sort_keys=True))
