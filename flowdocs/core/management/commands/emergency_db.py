import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.emergency_recovery import (
    REASONS,
    RecoverySetError,
    apply_prune,
    create_set,
    list_sets,
    plan_prune,
    prepare_set,
    validate_workspace,
    verify_set,
)


class Command(BaseCommand):
    help = "Create, verify, prepare, and prune bounded database emergency sets"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="operation", required=True)
        create = subparsers.add_parser("create")
        create.add_argument("--reason", required=True, choices=sorted(REASONS))

        listing = subparsers.add_parser("list")
        listing.add_argument("--json", action="store_true")

        verify = subparsers.add_parser("verify")
        verify.add_argument("--set", dest="set_id", required=True)

        prune = subparsers.add_parser("prune")
        prune.add_argument("--apply", action="store_true")
        prune.add_argument("--confirm", default="")

        prepare = subparsers.add_parser("prepare")
        prepare.add_argument("--set", dest="set_id", required=True)
        prepare.add_argument("--target-root", required=True)
        prepare.add_argument("--ack-database-only", action="store_true")

        validate = subparsers.add_parser("validate")
        validate.add_argument("--workspace", required=True)

    def handle(self, *args, **options):
        try:
            operation = options["operation"]
            if operation == "create":
                result = create_set(options["reason"])
            elif operation == "list":
                result = list_sets()
                if not options["json"]:
                    for item in result:
                        self.stdout.write(
                            f"{item['set_id']} {item['created_at']} "
                            f"{item['reason']} {item['verification_state']}"
                        )
                    return
            elif operation == "verify":
                result = verify_set(options["set_id"])
            elif operation == "prune":
                if options["apply"]:
                    if not options["confirm"]:
                        raise CommandError("--confirm <plan-id> is required with --apply")
                    result = apply_prune(options["confirm"])
                else:
                    result = plan_prune()
            elif operation == "prepare":
                if not options["ack_database_only"]:
                    raise CommandError("--ack-database-only is required")
                result = prepare_set(
                    options["set_id"], Path(options["target_root"])
                )
            else:
                result = validate_workspace(Path(options["workspace"]))
        except RecoverySetError as exc:
            raise CommandError(f"{exc.reason_code}: {exc}") from exc
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str))
        if operation == "validate" and not result["recovery_ready"]:
            raise CommandError("workspace_not_recovery_ready")
