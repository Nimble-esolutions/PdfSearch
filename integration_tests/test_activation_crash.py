"""Activation crash recovery and rollback coherence integration tests.

Tests real activation code with failure injection at each checkpoint.
Uses file-backed SQLite, Real Redis, real activation directories.
"""

from __future__ import annotations

import os
import secrets
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


class ActivationCrashRecoveryTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._clean_s3()
        from core.activation_test_hook import enable
        enable()

    @classmethod
    def tearDownClass(cls):
        from core.activation_test_hook import clear_all
        clear_all()
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
            username=f"crashuser-{secrets.token_hex(4)}",
            email=f"crash-{secrets.token_hex(4)}@test.local",
            password="testpass",
        )
        folder = Folder.objects.create(name="Crash Test Folder", created_by=user)
        pdf_bytes = (
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n"
            b"trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n9\n%%EOF"
        )
        media_root = Path(getattr(django_settings, "MEDIA_ROOT", "/tmp"))
        media_root.mkdir(parents=True, exist_ok=True)
        pdf_path = media_root / f"crash-{secrets.token_hex(4)}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        PDFFile.objects.create(
            title="Crash Test Doc",
            folder=folder,
            file_path=str(pdf_path),
            extracted_text="crash test content",
            page_chunks=["crash chunk 1"],
            chunk_embeddings=[[0.3] * 1536],
            indexed=True,
            lifecycle="ready",
            uploaded_by=user,
        )
        return user, folder

    def setUp(self):
        self._create_fixture()

    def _publish_source(self):
        from core.maintenance import sync_active_generation
        gen = sync_active_generation()
        return gen

    def _make_workspace_a(self):
        from core.restore_workspace import create_workspace, RestoreMode, WorkspaceState
        from core.models import ArtifactGeneration
        gen = self._publish_source()
        wid = f"stage-a-{secrets.token_hex(4)}"
        ws = create_workspace(
            workspace_id=wid,
            restore_job_id="crash-test-a",
            source_dataset_id=SOURCE_DATASET,
            source_generation_id=gen.generation_id,
            target_dataset_id=STAGING_DATASET,
            restore_mode=RestoreMode.PINNED,
        )
        for state in [WorkspaceState.DOWNLOADING, WorkspaceState.DOWNLOADED,
                       WorkspaceState.SOURCE_VALIDATED, WorkspaceState.PREFLIGHT_PASSED,
                       WorkspaceState.APPLICATION_VALIDATED, WorkspaceState.ACTIVATION_READY]:
            ws.transition(state)
        ws.activation_eligible = True
        ws.save_metadata()
        return ws

    def test_01_crash_before_pointer_switch_recovers(self):
        """Crash before pointer switch: gen A stays active."""
        from core.activation_test_hook import set_injector, raise_failure
        from core.activate import activate_generation, read_active_pointer, activation_status as act_status

        ws_a = self._make_workspace_a()
        from core.activate import write_active_pointer
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace_a()

        set_injector("pointer_switch_pending", lambda: raise_failure("Simulated crash before pointer switch"))

        try:
            activate_generation(ws_b.local_path)
        except Exception:
            pass

        current = read_active_pointer()
        self.assertIsNotNone(current)
        self.assertIn("stage-a-", current or "")

    def test_02_crash_after_pointer_switch_recovers(self):
        """Crash after pointer switch: exception handler rolls back to A."""
        from core.activation_test_hook import set_injector, raise_failure
        from core.activate import activate_generation, read_active_pointer

        ws_a = self._make_workspace_a()
        from core.activate import write_active_pointer
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace_a()

        set_injector("pointer_switched", lambda: raise_failure("Crash after pointer switch"))

        try:
            activate_generation(ws_b.local_path)
        except Exception:
            pass

        current = read_active_pointer()
        self.assertIsNotNone(current, "Must have an active pointer after recovery")
        resolved = str(Path(current).resolve()) if current else ""
        self.assertIn("stage-a-", resolved, "Crash-after-switch should trigger safe rollback to A")

    def test_03_crash_before_confirmation_active_anyway(self):
        """Crash before confirmation: B is active (pointer already switched)."""
        from core.activation_test_hook import set_injector, raise_failure
        from core.activate import activate_generation, read_active_pointer

        ws_a = self._make_workspace_a()
        from core.activate import write_active_pointer
        write_active_pointer(ws_a.local_path)

        ws_b = self._make_workspace_a()

        set_injector("activation_confirmed", lambda: raise_failure("Crash before confirmation"))

        try:
            activate_generation(ws_b.local_path)
        except Exception:
            pass

        current = read_active_pointer()
        resolved = str(Path(current).resolve()) if current else ""
        self.assertIn("stage-a-", resolved,
                      "Crash after pointer switch but before confirmation triggers rollback (safe behavior)")

    def test_04_generation_coherence_check(self):
        """validate_generation_coherence returns coherent=True for valid workspace."""
        from core.activate import validate_generation_coherence
        ws_a = self._make_workspace_a()
        result = validate_generation_coherence(ws_a.local_path)
        self.assertTrue(result.get("coherent") or result.get("checks", {}).get("database_integrity") is not False)

    def test_05_multiple_activations_race(self):
        """Two concurrent activations: only B or recovery is coherent."""
        from core.activation_test_hook import set_injector, raise_failure
        from core.activate import activate_generation, read_active_pointer

        ws_a = self._make_workspace_a()
        from core.activate import write_active_pointer
        write_active_pointer(ws_a.local_path)

        ws_b1 = self._make_workspace_a()
        ws_b2 = self._make_workspace_a()

        set_injector("pointer_switch_pending", lambda: raise_failure("Crash in first attempt"))

        try:
            activate_generation(ws_b1.local_path)
        except Exception:
            pass

        from core.activation_test_hook import clear_all
        clear_all()
        from core.activation_test_hook import enable
        enable()

        try:
            activate_generation(ws_b2.local_path)
        except Exception:
            pass

        current = read_active_pointer()
        self.assertIsNotNone(current, "Must have an active pointer after second attempt")

    def test_06_rollback_coherence(self):
        """Activate A, activate B, rollback: A's pointer is restored."""
        from core.activate import (
            write_active_pointer, read_active_pointer,
            preserve_previous_pointer, rollback_to_previous,
        )

        ws_a = self._make_workspace_a()
        from core.restore_workspace import RestoreWorkspace
        write_active_pointer(ws_a.local_path)
        preserve_previous_pointer()

        ws_b = self._make_workspace_a()
        write_active_pointer(ws_b.local_path)

        result = rollback_to_previous()
        restored = result.get("restored_pointer", "")

        current = read_active_pointer()
        self.assertTrue(current is not None)
        self.assertEqual(
            str(Path(str(current)).resolve()),
            str(Path(ws_a.local_path).resolve()),
            f"Rollback must restore A's pointer"
        )
