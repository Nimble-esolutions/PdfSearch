import json
import os

from django.core.management.base import BaseCommand, CommandError

from core.utils import SearchDataIntegrityError
from vaultops.runtime_control import RuntimeControlError
from vaultops.services.snapshot import SnapshotError
from vaultops.services.runtime_verification import (
    RUNTIME_VERIFICATION_ERRORS,
    verify_activation_runtime,
)
from vaultops.runtime_verification_contract import (
    write_runtime_verification_failure,
    write_runtime_verification_success,
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
            if isinstance(exc, RuntimeControlError):
                reason_code = getattr(
                    exc, "reason_code", "activation_runtime_smoke_failed"
                )
            elif isinstance(exc, SnapshotError):
                reason_code = "activation_runtime_faiss_invalid"
            elif isinstance(exc, SearchDataIntegrityError):
                reason_code = "activation_search_probe_failed"
            else:
                reason_code = "activation_runtime_smoke_failed"
            failure_path = os.environ.get(
                "ACTIVATION_VERIFICATION_FAILURE_PATH", ""
            )
            if failure_path:
                write_runtime_verification_failure(
                    failure_path,
                    reason_code,
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
        success_path = os.environ.get(
            "ACTIVATION_VERIFICATION_SUCCESS_PATH", ""
        )
        if success_path:
            write_runtime_verification_success(
                success_path,
                evidence["unavailable_documents"],
            )
