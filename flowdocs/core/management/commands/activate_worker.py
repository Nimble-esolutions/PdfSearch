"""Management command: run activation worker with checkpoint signalling.

Usage: python manage.py activate_worker <workspace_path> <checkpoint_file> [pause_at]

The worker activates a generation, signalling checkpoints to the
coordinator via a file. Used for crash-recovery integration tests.
"""

from __future__ import annotations

import os
import sys
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Activate a generation workspace with crash-test checkpoint signalling"

    def add_arguments(self, parser):
        parser.add_argument("workspace_path")
        parser.add_argument("checkpoint_file")
        parser.add_argument("--pause-at", default="")

    def handle(self, *args, **options):
        workspace_path = options["workspace_path"]
        checkpoint_file = options["checkpoint_file"]
        pause_at = options["pause_at"]

        from core.activation_test_hook import enable as hook_enable, set_injector
        from core.activate import activate_generation

        hook_enable()

        for chkpt in ("pointer_switch_pending", "pointer_switched", "activation_confirmed"):
            set_injector(
                chkpt,
                (lambda c=chkpt: self._signal(c, checkpoint_file, pause_at)),
            )

        try:
            result = activate_generation(workspace_path)
            with open(checkpoint_file, "a") as f:
                f.write(f"SUCCESS\n{result}\n")
        except Exception as exc:
            with open(checkpoint_file, "a") as f:
                f.write(f"ERROR:{exc}\n")
            sys.exit(2)

    def _signal(self, checkpoint: str, checkpoint_file: str, pause_at: str) -> None:
        try:
            with open(checkpoint_file, "w") as f:
                f.write(f"{checkpoint}\n{os.getpid()}\n{time.time()}")
        except Exception:
            pass
        if pause_at and checkpoint == pause_at:
            time.sleep(120)
