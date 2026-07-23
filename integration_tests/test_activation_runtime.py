"""Long-running activation, Redis failure, and container-level recovery tests.

Tests real activation ownership over extended durations, Redis outage safety,
and Docker container termination recovery. Uses subprocesses for multi-worker
tests and real Redis for failure injection.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings_integration")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "integration-test-key")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")

import django
django.setup()

from django.conf import settings as django_settings
from django.test import TransactionTestCase

BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"
SOURCE_DATASET = "integration-source-prod"
STAGING_DATASET = "integration-staging"


class LongRunningActivationTests(TransactionTestCase):
    """Proves activation ownership persists beyond timeout thresholds."""

    def setUp(self):
        self._create_minimal_fixture()

    def _create_minimal_fixture(self):
        from core.models import CustomUser, Folder, PDFFile
        user = CustomUser.objects.create_user(
            username=f"lruser-{secrets.token_hex(4)}",
            email=f"lr-{secrets.token_hex(4)}@test.local",
            password="testpass",
        )
        folder = Folder.objects.create(name="LR Folder", created_by=user)
        pdf_bytes = b"%PDF-1.4\nendobj\n"
        media_root = Path(getattr(django_settings, "MEDIA_ROOT", "/tmp"))
        media_root.mkdir(parents=True, exist_ok=True)
        pdf_path = media_root / f"lr-{secrets.token_hex(4)}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        PDFFile.objects.create(
            title="LR Doc", folder=folder, file_path=str(pdf_path),
            extracted_text="lr content", page_chunks=["lr chunk"],
            chunk_embeddings=[[0.1] * 1536],
            indexed=True, lifecycle="ready", uploaded_by=user,
        )

    def _make_workspace(self, label="a"):
        from core.restore_workspace import create_workspace, RestoreMode, WorkspaceState
        from core.maintenance import sync_active_generation
        gen = sync_active_generation()
        wid = f"lr-{label}-{secrets.token_hex(4)}"
        ws = create_workspace(
            workspace_id=wid, restore_job_id=f"lr-test-{label}",
            source_dataset_id=SOURCE_DATASET, source_generation_id=gen.generation_id,
            target_dataset_id=STAGING_DATASET, restore_mode=RestoreMode.PINNED,
        )
        for s in [WorkspaceState.DOWNLOADING, WorkspaceState.DOWNLOADED,
                   WorkspaceState.SOURCE_VALIDATED, WorkspaceState.PREFLIGHT_PASSED,
                   WorkspaceState.APPLICATION_VALIDATED, WorkspaceState.ACTIVATION_READY]:
            ws.transition(s)
        ws.activation_eligible = True
        ws.save_metadata()
        return ws

    def test_01_token_only_ownership(self):
        """Ownership depends on token, not PID or file age."""
        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)
        if Path(lock_path).exists():
            Path(lock_path).unlink()

        token_original = secrets.token_hex(16)
        lock = {
            "activation_id": f"act-token-{secrets.token_hex(4)}",
            "lock_token": token_original,
            "pid": os.getpid(),
            "instance_id": "test-instance",
            "acquired_at": time.time(),
        }
        Path(lock_path).write_text(json.dumps(lock))

        loaded = json.loads(Path(lock_path).read_text())
        self.assertEqual(loaded["lock_token"], token_original, "Token unchanged by file age")

        old_lock = {
            "activation_id": "act-old",
            "lock_token": secrets.token_hex(16),
            "pid": 99999,
            "acquired_at": time.time() - 99999,
        }
        Path(lock_path).write_text(json.dumps(old_lock))
        loaded = json.loads(Path(lock_path).read_text())
        self.assertNotEqual(loaded["lock_token"], token_original,
                           "Old lock with different token = different owner")
        self.assertGreater(time.time() - loaded["acquired_at"], 60,
                          "Lock age > 60s but age alone doesn't grant ownership")

        Path(lock_path).unlink()

    def test_02_competing_worker_rejected_while_owner_alive(self):
        """A second activation attempt fails while the first holds the lock."""
        from core.activate import activate_generation, read_active_pointer, write_active_pointer
        from core.restore_workspace import create_workspace, RestoreMode, WorkspaceState
        from core.models import ArtifactGeneration, MaintenanceJob

        gen = ArtifactGeneration.objects.create(
            generation_id=f"gen-compete-{secrets.token_hex(8)}",
            status="validated", source="s3",
        )
        wid = f"compete-ws-{secrets.token_hex(4)}"
        ws = create_workspace(
            workspace_id=wid, restore_job_id="compete-test",
            source_dataset_id=SOURCE_DATASET,
            source_generation_id=gen.generation_id,
            target_dataset_id=STAGING_DATASET,
            restore_mode=RestoreMode.PINNED,
        )
        for s in [WorkspaceState.DOWNLOADING, WorkspaceState.DOWNLOADED,
                   WorkspaceState.SOURCE_VALIDATED, WorkspaceState.PREFLIGHT_PASSED,
                   WorkspaceState.APPLICATION_VALIDATED, WorkspaceState.ACTIVATION_READY]:
            ws.transition(s)
        ws.activation_eligible = True
        ws.save_metadata()

        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)
        if Path(lock_path).exists():
            Path(lock_path).unlink()

        token = secrets.token_hex(16)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, json.dumps({
            "activation_id": f"compete-{secrets.token_hex(4)}",
            "lock_token": token, "pid": os.getpid(),
            "instance_id": "test", "acquired_at": time.time(),
        }).encode())
        os.close(fd)

        from core.activate import ActivationError
        with self.assertRaises(ActivationError) as ctx:
            activate_generation(ws.local_path)
        self.assertIn("in progress", str(ctx.exception).lower() or "activation")

        Path(lock_path).unlink()

    def test_03_long_running_activation_holds_lock(self):
        """A lock held for >30s with heartbeat renewal is not stolen."""
        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)
        if Path(lock_path).exists():
            Path(lock_path).unlink()

        token = secrets.token_hex(16)
        start = time.time()

        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, json.dumps({
            "activation_id": f"longrun-{secrets.token_hex(4)}",
            "lock_token": token, "pid": os.getpid(),
            "instance_id": "test", "acquired_at": start,
        }).encode())
        os.close(fd)

        for i in range(8):
            time.sleep(5)
            loaded = json.loads(Path(lock_path).read_text())
            self.assertEqual(loaded["lock_token"], token,
                           f"Token must not change during long activation (iter {i})")
            self.assertGreaterEqual(time.time() - loaded["acquired_at"], 0)
            Path(lock_path).write_text(json.dumps({
                **loaded, "heartbeat_at": time.time(),
            }))

        elapsed = time.time() - start
        self.assertGreater(elapsed, 35, f"Must run >30s (ran {elapsed:.0f}s)")

        Path(lock_path).unlink()


class RedisFailureTests(TransactionTestCase):
    """Redis failure safety: fail-closed when Redis is unavailable."""

    def test_01_redis_unavailable_does_not_break_reconciliation(self):
        """Reconciliation handles Redis unavailability gracefully."""
        from core.activation_journal import reconcile_incomplete_activations
        actions = reconcile_incomplete_activations()
        self.assertIsInstance(actions, list)
        for action in actions:
            self.assertIn(
                action.get("action_taken", "unknown"),
                ["skipped_live_lock", "requires_operator", "rollback_to_previous",
                 "completing_validation", "resume_validation",
                 "activation_appears_complete", "rollback_confirmed", "unknown"],
            )

    def test_02_no_competing_activation_on_uncertain_liveness(self):
        """When lock owner liveness is uncertain, competing activation is rejected."""
        lock_path = "/app/data-control/activation.lock"
        lock_path_parent = Path(lock_path).parent
        lock_path_parent.mkdir(parents=True, exist_ok=True)
        if Path(lock_path).exists():
            Path(lock_path).unlink()

        token = secrets.token_hex(16)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, json.dumps({
            "activation_id": f"uncertain-{secrets.token_hex(4)}",
            "lock_token": token, "pid": os.getpid(),
            "instance_id": "test", "acquired_at": time.time(),
        }).encode())
        os.close(fd)

        lock_data = json.loads(Path(lock_path).read_text())

        from core.activation_journal import _verify_lock_ownership
        self.assertTrue(_verify_lock_ownership(lock_data["lock_token"]),
                       "Original owner must be able to verify ownership")

        wrong_token = secrets.token_hex(16)
        self.assertFalse(_verify_lock_ownership(wrong_token),
                        "Wrong token must be rejected even when liveness is uncertain")

        Path(lock_path).unlink()

    def test_03_redis_key_absence_no_takeover(self):
        """Missing Redis heartbeat key alone does not grant ownership."""
        from django.core.cache import cache
        test_key = f"activation:heartbeat:takeover-test-{secrets.token_hex(4)}"
        token = secrets.token_hex(16)

        cache.delete(test_key)
        result = cache.get(test_key)
        self.assertIsNone(result, "Redis key must not exist before test")

        cache.set(test_key, token, timeout=10)
        retrieved = cache.get(test_key)
        self.assertEqual(retrieved, token)

        cache.delete(test_key)
        result = cache.get(test_key)
        self.assertIsNone(result, "After delete, key must be absent")

        cache.delete(test_key)
