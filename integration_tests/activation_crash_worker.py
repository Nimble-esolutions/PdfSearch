"""Activation crash worker — runs in a separate subprocess.

Called by the coordinator test to perform activation with checkpoint
signalling. Accepts SIGKILL without running cleanup code.

Usage (from coordinator):
  python activation_crash_worker.py <workspace_path> <checkpoint_file> [<checkpoint_to_pause_at>]

Signals:
  Writes checkpoint name to <checkpoint_file> whenever a hook checkpoint fires.
  If <checkpoint_to_pause_at> matches, waits for a "proceed" signal before
  continuing (for long-running activation tests).
  If killed with SIGKILL (kill -9), no except/finally blocks execute.
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings_integration")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "worker-key")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")

import django
django.setup()

from core.activation_test_hook import enable as hook_enable, set_injector
from core.activate import activate_generation


def _signal(checkpoint: str, checkpoint_file: str, pause_at: str = "") -> None:
    """Write checkpoint to signal file. If this is the pause point, wait."""
    try:
        with open(checkpoint_file, "w") as f:
            f.write(f"{checkpoint}\n{os.getpid()}\n{time.time()}")
    except Exception:
        pass

    if pause_at and checkpoint == pause_at:
        import time
        time.sleep(120)


def main():
    if len(sys.argv) < 3:
        print("Usage: activation_crash_worker.py <workspace_path> <checkpoint_file> [pause_at]")
        sys.exit(1)

    workspace_path = sys.argv[1]
    checkpoint_file = sys.argv[2]
    pause_at = sys.argv[3] if len(sys.argv) > 3 else ""

    hook_enable()

    for chkpt in ("pointer_switch_pending", "pointer_switched", "activation_confirmed"):
        set_injector(chkpt, lambda c=chkpt: _signal(c, checkpoint_file, pause_at))

    try:
        result = activate_generation(workspace_path)
        with open(checkpoint_file, "a") as f:
            f.write(f"SUCCESS\n{result}\n")
    except Exception as exc:
        with open(checkpoint_file, "a") as f:
            f.write(f"ERROR:{exc}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
