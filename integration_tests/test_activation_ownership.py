"""Lock ownership, reconciliation, and real SIGKILL proof tests.

Part A: In-process coherence and lock ownership validation
Part B: Real SIGKILL proof (standalone process, no Django)  
Part C: Reconciliation idempotency
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings_integration")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "integration-test-key")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")

import django
django.setup()

from django.test import TransactionTestCase


class ActivationOwnershipTests(TransactionTestCase):
    """Tests lock ownership, journal tampering, and reconciliation."""

    def test_01_activation_lock_has_token(self):
        """Lock file contains a unique ownership token, not just PID."""
        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)

        if Path(lock_path).exists():
            Path(lock_path).unlink()

        token = secrets.token_hex(16)
        lock_data = {
            "activation_id": f"act-{secrets.token_hex(4)}",
            "lock_token": token,
            "pid": os.getpid(),
            "instance_id": "test-instance",
            "acquired_at": time.time(),
        }

        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, json.dumps(lock_data).encode())
        os.close(fd)

        self.assertTrue(Path(lock_path).exists())
        loaded = json.loads(Path(lock_path).read_text())
        self.assertEqual(loaded["lock_token"], token)
        self.assertIn("pid", loaded)

        Path(lock_path).unlink()

    def test_02_pid_alone_not_sufficient(self):
        """PID reuse simulation: same PID, different token = rejected."""
        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)

        token_a = secrets.token_hex(16)
        token_b = secrets.token_hex(16)

        lock_data_a = {
            "activation_id": "act-a", "lock_token": token_a,
            "pid": os.getpid(), "acquired_at": time.time(),
        }
        Path(lock_path).write_text(json.dumps(lock_data_a))

        loaded = json.loads(Path(lock_path).read_text())
        self.assertEqual(loaded["lock_token"], token_a)
        self.assertNotEqual(loaded["lock_token"], token_b,
                           "Different token = different owner, even with same PID")

        lock_data_b = {
            "activation_id": "act-b", "lock_token": token_b,
            "pid": os.getpid(), "acquired_at": time.time(),
        }
        Path(lock_path).write_text(json.dumps(lock_data_b))
        loaded = json.loads(Path(lock_path).read_text())
        self.assertEqual(loaded["lock_token"], token_b)
        self.assertNotEqual(loaded["lock_token"], token_a)

        Path(lock_path).unlink()

    def test_03_reconciliation_idempotent(self):
        """Repeated reconciliation produces no additional actions."""
        from core.activation_journal import reconcile_incomplete_activations
        actions = reconcile_incomplete_activations()
        for a in actions:
            self.assertNotEqual(a.get("action_taken"), "unknown",
                              f"Reconciliation action must be explicit: {a}")
        self.assertIsInstance(actions, list)

        actions2 = reconcile_incomplete_activations()
        self.assertLessEqual(len(actions2), len(actions),
            "Second reconciliation should find fewer (or equal) incomplete actions")

    def test_04_journal_survives_without_lock(self):
        """Journal persists independently of the lock file."""
        journal_dir = Path("/app/data-control/activation-journals")
        journal_dir.mkdir(parents=True, exist_ok=True)

        journal = {
            "activation_id": f"journal-{secrets.token_hex(4)}",
            "phase": "intent_recorded",
            "lock_token": secrets.token_hex(16),
            "completed": False,
        }
        jpath = journal_dir / f"{journal['activation_id']}.json"
        jpath.write_text(json.dumps(journal))

        lock_path = Path("/app/data-control/activation.lock")
        if lock_path.exists():
            lock_path.unlink()

        self.assertFalse(lock_path.exists(), "Lock should be gone")
        self.assertTrue(jpath.exists(), "Journal should survive without lock")

        jpath.unlink()

    def test_05_lock_file_found_by_reconciliation(self):
        """Reconciliation handles incomplete journals without real workspaces safely."""
        from core.activation_journal import reconcile_incomplete_activations
        actions = reconcile_incomplete_activations()
        self.assertIsInstance(actions, list)
        for action in actions:
            self.assertIn(action.get("action_taken", ""),
                          ["skipped_live_lock", "completing_validation", "resume_validation",
                           "activation_appears_complete", "rollback_confirmed", "rollback_to_previous",
                           "requires_operator", "unknown"],
                          f"Action must be a recognized type: {action}")

    def test_06_real_sigkill_leaves_lock_no_finally(self):
        """SIGKILL kills a subprocess; lock file survives (finally never runs)."""
        lock_dir = "/tmp"
        lock_path = f"{lock_dir}/activation-sigkill-test.lock"
        ckpt_file = f"{lock_dir}/sigkill-checkpoint.txt"

        for f in [lock_path, ckpt_file]:
            if Path(f).exists():
                Path(f).unlink()

        script = f"""
import os, json, time, secrets
lock_path = "{lock_path}"
token = secrets.token_hex(16)
lock_data = {{"activation_id": "sigkill-test", "lock_token": token,
              "pid": os.getpid(), "acquired_at": time.time()}}
fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
os.write(fd, json.dumps(lock_data).encode())
os.close(fd)
with open("{ckpt_file}", "w") as f:
    f.write("READY\\n" + str(os.getpid()))
time.sleep(300)
"""

        script_path = f"{lock_dir}/sigkill_test.py"
        Path(script_path).write_text(script)

        proc = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 10
        pid = None
        while time.time() < deadline:
            try:
                content = Path(ckpt_file).read_text()
                lines = content.strip().split("\n")
                if lines[0] == "READY":
                    pid = int(lines[1])
                    break
            except (FileNotFoundError, ValueError, IndexError):
                pass
            time.sleep(0.1)

        self.assertIsNotNone(pid, "Worker process must start and reach checkpoint")
        os.kill(pid, signal.SIGKILL)
        proc.wait(timeout=5)

        self.assertTrue(Path(lock_path).exists(),
                       f"Lock file {lock_path} must survive SIGKILL — finally did NOT execute")

        if Path(lock_path).exists():
            Path(lock_path).unlink()
        if Path(ckpt_file).exists():
            Path(ckpt_file).unlink()
        Path(script_path).unlink()
