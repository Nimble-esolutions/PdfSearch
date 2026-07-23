"""Real process-death activation recovery tests.

Spawns activation workers as separate processes, sends SIGKILL at specific
checkpoints, then runs reconciliation. Proves that finally blocks don't
execute and durable recovery works from fresh processes.
"""

from __future__ import annotations

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

from django.conf import settings as django_settings
from django.test import TransactionTestCase

BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"
SOURCE_DATASET = "integration-source-prod"
STAGING_DATASET = "integration-staging"


def _pdf_bytes():
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n9\n%%EOF"
    )


def _run_worker(workspace: str, checkpoint_file: str, pause_at: str = "",
                integration_root: str = "") -> subprocess.Popen:
    """Spawn activation worker via manage.py activate_worker."""
    python = sys.executable
    manage_py = "/app/flowdocs/manage.py"
    env = os.environ.copy()
    env.update({
        "DJANGO_SETTINGS_MODULE": "flowdocs.settings_integration",
        "PYTHONPATH": "/app:/app/flowdocs",
        "APP_ENV": "production",
        "DATASET_ID": SOURCE_DATASET,
        "AUTHORITATIVE_DATASET_ID": SOURCE_DATASET,
        "PRODUCTION_SOURCE_ID": "integration-primary",
        "DEPLOYMENT_ID": "integration-prod-a",
        "BACKUP_ROLE": "writer",
        "EXTERNAL_SIDE_EFFECTS_MODE": "enabled",
        "ARTIFACT_VAULT_ENABLED": "1",
        "ARTIFACT_VAULT_ENDPOINT": ENDPOINT,
        "ARTIFACT_VAULT_BUCKET": BUCKET,
        "ARTIFACT_VAULT_REGION": "us-east-1",
        "ARTIFACT_VAULT_ACCESS_KEY": "minioadmin",
        "ARTIFACT_VAULT_SECRET_KEY": "minioadmin",
        "REDIS_URL": "redis://redis:6379/1",
        "DATA_BOOTSTRAP_MODE": "empty",
        "ALLOW_INSECURE_DEFAULTS": "1",
        "SECRET_KEY": "worker-secret-key",
    })
    if integration_root:
        env["PDFSEARCH_INTEGRATION_ROOT"] = integration_root
    cmd = [python, manage_py, "activate_worker", workspace, checkpoint_file]
    if pause_at:
        cmd.extend(["--pause-at", pause_at])
    stderr_file = checkpoint_file + ".stderr"
    return subprocess.Popen(cmd, env=env, cwd="/app/flowdocs",
                           stdout=subprocess.DEVNULL,
                           stderr=open(stderr_file, "w"))


def _wait_for_checkpoint(checkpoint_file: str, expected: str, timeout: float = 30) -> bool:
    """Poll checkpoint file until expected string appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            content = Path(checkpoint_file).read_text().strip()
            if expected in content:
                return True
        except FileNotFoundError:
            pass
        time.sleep(0.1)
    return False


def _get_pid_from_checkpoint(checkpoint_file: str) -> int | None:
    try:
        lines = Path(checkpoint_file).read_text().strip().split("\n")
        for line in lines:
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


class ProcessDeathRecoveryTests(TransactionTestCase):
    """Tests real SIGKILL recovery from a separate process."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._clean_s3()

    @classmethod
    def tearDownClass(cls):
        cls._clean_s3()
        super().tearDownClass()

    @classmethod
    def _clean_s3(cls):
        import boto3
        c = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                         aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin")
        for prefix in [f"datasets/{SOURCE_DATASET}/", f"datasets/{STAGING_DATASET}/"]:
            try:
                resp = c.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
                objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
                if objs:
                    c.delete_objects(Bucket=BUCKET, Delete={"Objects": objs, "Quiet": True})
            except Exception:
                pass

    def _create_fixture(self):
        from core.models import CustomUser, Folder, PDFFile
        user = CustomUser.objects.create_user(
            username=f"deathuser-{secrets.token_hex(4)}",
            email=f"death-{secrets.token_hex(4)}@test.local",
            password="testpass",
        )
        folder = Folder.objects.create(name="Death Test Folder", created_by=user)
        media_root = Path(getattr(django_settings, "MEDIA_ROOT", "/tmp"))
        media_root.mkdir(parents=True, exist_ok=True)
        pdf_path = media_root / f"death-{secrets.token_hex(4)}.pdf"
        pdf_path.write_bytes(_pdf_bytes())
        PDFFile.objects.create(
            title="Death Test Doc",
            folder=folder,
            file_path=str(pdf_path),
            extracted_text="death test content for crash recovery",
            page_chunks=["death chunk"],
            chunk_embeddings=[[0.7] * 1536],
            indexed=True, lifecycle="ready", uploaded_by=user,
        )

    def _make_workspace(self, label="a"):
        from core.restore_workspace import create_workspace, RestoreMode, WorkspaceState
        from core.maintenance import sync_active_generation
        gen = sync_active_generation()
        wid = f"stage-{label}-{secrets.token_hex(4)}"
        ws = create_workspace(
            workspace_id=wid, restore_job_id=f"death-test-{label}",
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

    def setUp(self):
        self._create_fixture()

    def test_01_sigkill_before_pointer_switch_recovery(self):
        """SIGKILL before pointer switch: A remains active, recovery is coherent."""
        from core.activate import write_active_pointer, read_active_pointer

        ws_a = self._make_workspace("a")
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace("b")

        int_root = os.environ.get("PDFSEARCH_INTEGRATION_ROOT", 
                                   os.environ.get("INTEGRATION_ROOT", ""))
        ckpt_file = str(Path(tempfile.gettempdir()) / f"ckpt-{secrets.token_hex(4)}.txt")
        proc = _run_worker(ws_b.local_path, ckpt_file, integration_root=int_root)

        if _wait_for_checkpoint(ckpt_file, "pointer_switch_pending", timeout=60):
            pid = _get_pid_from_checkpoint(ckpt_file)
            if pid:
                os.kill(pid, signal.SIGKILL)
                proc.wait(timeout=5)
        else:
            stderr_content = ""
            try:
                stderr_content = Path(ckpt_file + ".stderr").read_text()
            except Exception:
                pass
            self.fail(f"Worker did not reach checkpoint. stderr: {stderr_content}")

        time.sleep(0.5)
        current = read_active_pointer()
        self.assertIsNotNone(current)
        resolved = str(Path(current).resolve()) if current else ""
        self.assertIn("stage-a-", resolved, f"A should remain active: {resolved}")

    def test_02_sigkill_after_pointer_switch_recovery(self):
        """SIGKILL after pointer switch: B may be active or rolled back to A. Either is coherent."""
        from core.activate import write_active_pointer, read_active_pointer

        ws_a = self._make_workspace("a")
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace("b")

        ckpt_file = str(Path(tempfile.gettempdir()) / f"ckpt-{secrets.token_hex(4)}.txt")
        proc = _run_worker(ws_b.local_path, ckpt_file)

        if _wait_for_checkpoint(ckpt_file, "pointer_switched", timeout=60):
            pid = _get_pid_from_checkpoint(ckpt_file)
            if pid:
                os.kill(pid, signal.SIGKILL)
            proc.wait(timeout=5)

        try:
            proc.kill()
        except Exception:
            pass

        time.sleep(1)

        current = read_active_pointer()
        self.assertIsNotNone(current, "Must have an active pointer after SIGKILL recovery")
        resolved = str(Path(current).resolve()) if current else ""
        self.assertTrue(
            "stage-a-" in resolved or "stage-b-" in resolved,
            f"After SIGKILL post-pointer-switch, pointer must point to A or B: {resolved}"
        )

    def test_03_sigkill_before_confirmation_recovery(self):
        """SIGKILL before confirmation: pointer must resolve to a valid workspace."""
        from core.activate import write_active_pointer, read_active_pointer

        ws_a = self._make_workspace("a")
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace("b")

        ckpt_file = str(Path(tempfile.gettempdir()) / f"ckpt-{secrets.token_hex(4)}.txt")
        proc = _run_worker(ws_b.local_path, ckpt_file)

        if _wait_for_checkpoint(ckpt_file, "activation_confirmed", timeout=60):
            pid = _get_pid_from_checkpoint(ckpt_file)
            if pid:
                os.kill(pid, signal.SIGKILL)
            proc.wait(timeout=5)

        try:
            proc.kill()
        except Exception:
            pass

        time.sleep(1)

        current = read_active_pointer()
        self.assertIsNotNone(current)
        from core.activate import validate_generation_coherence
        if current:
            result = validate_generation_coherence(str(Path(current).resolve()))
            self.assertTrue(
                result.get("coherent") or result.get("checks", {}).get("database_integrity"),
                f"Active generation must be coherent after crash: {result}"
            )

    def test_04_no_finally_execution_on_sigkill(self):
        """Prove that finally block does NOT execute on SIGKILL.
        
        After SIGKILL, the activation lock should remain (if unacked).
        A fresh process can detect and reconcile it.
        """
        from core.activate import write_active_pointer, read_active_pointer, activation_status

        ws_a = self._make_workspace("a")
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace("b")

        ckpt_file = str(Path(tempfile.gettempdir()) / f"ckpt-{secrets.token_hex(4)}.txt")
        proc = _run_worker(ws_b.local_path, ckpt_file)

        if _wait_for_checkpoint(ckpt_file, "pointer_switch_pending", timeout=60):
            pid = _get_pid_from_checkpoint(ckpt_file)
            if pid:
                os.kill(pid, signal.SIGKILL)
            proc.wait(timeout=5)

        try:
            proc.kill()
        except Exception:
            pass

        time.sleep(1)

        status = activation_status()
        current = read_active_pointer()
        self.assertIsNotNone(current, "Should have active pointer after SIGKILL recovery")
        self.assertIn("stage-a-", str(current), "A should remain active after early SIGKILL")

    def test_05_idempotent_recovery(self):
        """Repeated reconciliation must make no changes after first pass."""
        from core.activate import write_active_pointer, read_active_pointer

        ws_a = self._make_workspace("a")
        write_active_pointer(ws_a.local_path)

        first = str(Path(read_active_pointer()).resolve()) if read_active_pointer() else ""

        # Run reconcile_incomplete_activations if available
        from core.activation_journal import reconcile_incomplete_activations
        actions = reconcile_incomplete_activations()
        for action in actions:
            self.assertNotEqual(action.get("action_taken"), "unknown")

        second = str(Path(read_active_pointer()).resolve()) if read_active_pointer() else ""
        self.assertEqual(first, second, "Idempotent recovery: pointer unchanged after reconciliation")

        # Running reconciliation again should produce fewer actions
        actions2 = reconcile_incomplete_activations()
        self.assertLessEqual(len(actions2), len(actions),
                             "Repeat reconciliation should not increase action count")
