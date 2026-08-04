"""Apply only recovery-backed additive migrations to an activated runtime."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from django.core.management import BaseCommand, CommandError, call_command

from core.emergency_recovery import create_set
from core.runtime_migrations import runtime_migration_plan


class Command(BaseCommand):
    help = (
        "Apply pending additive runtime migrations after creating a verified "
        "paired recovery set; reject every unclassified operation."
    )

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        using = options["database"]
        control_root = Path(
            os.environ.get("DATA_CONTROL_ROOT", "/app/data-control")
        )
        lock_dir = control_root / "runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "safe-runtime-migrations.lock"
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            decisions = runtime_migration_plan(using=using)
            summary = {
                "database": using,
                "migration_count": len({item.migration for item in decisions}),
                "operation_count": len(decisions),
                "safe": all(item.safe for item in decisions),
                "operations": [
                    {
                        "migration": item.migration,
                        "operation": item.operation,
                        "reason": item.reason,
                        "safe": item.safe,
                    }
                    for item in decisions
                ],
            }
            self.stdout.write(
                "runtime_migration_plan="
                + json.dumps(summary, sort_keys=True, separators=(",", ":"))
            )
            unsafe = [item for item in decisions if not item.safe]
            if unsafe:
                blocked = ",".join(
                    f"{item.migration}:{item.operation}" for item in unsafe[:10]
                )
                raise CommandError(
                    "runtime_migration_requires_isolated_candidate:" + blocked
                )
            if not decisions or options["check"]:
                return

            recovery = create_set("pre-migration")
            self.stdout.write(
                "runtime_migration_recovery="
                + json.dumps(
                    {
                        "set_id": recovery["set_id"],
                        "reused": bool(recovery.get("reused")),
                        "verification_state": recovery["verification_state"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            call_command(
                "migrate",
                database=using,
                interactive=False,
                verbosity=options["verbosity"],
            )
            remaining = runtime_migration_plan(using=using)
            if remaining:
                raise CommandError("runtime_migration_incomplete")
            self.stdout.write("runtime_migration_status=applied_and_verified")
