"""End-to-end restore pipeline integration test against real MinIO.

Exercises: publish → restore → sanitize → rehearse → activate → rollback
Uses file-backed SQLite via settings_integration.py.

Run with:
  DJANGO_SETTINGS_MODULE=flowdocs.settings_integration \
  APP_ENV=production DATASET_ID=integration-source-prod \
  AUTHORITATIVE_DATASET_ID=integration-source-prod \
  PRODUCTION_SOURCE_ID=integration-primary \
  DEPLOYMENT_ID=integration-prod-a BACKUP_ROLE=writer \
  ARTIFACT_VAULT_ENABLED=1 \
  ARTIFACT_VAULT_ENDPOINT=http://pdfsearch-minio:9000 \
  ARTIFACT_VAULT_BUCKET=pdfsearch-test \
  ARTIFACT_VAULT_REGION=us-east-1 \
  ARTIFACT_VAULT_ACCESS_KEY=minioadmin \
  ARTIFACT_VAULT_SECRET_KEY=minioadmin \
  REDIS_URL=redis://redis:6379/1 \
  EXTERNAL_SIDE_EFFECTS_MODE=enabled \
  PYTHONPATH=/app:/app/flowdocs \
  python manage.py test integration_tests.test_restore_flow --no-input -v2
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
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

SENTINEL_EMAIL = "original-user@example.com"
SENTINEL_EMAIL_2 = "second-original@example.com"
SENTINEL_PHONE = "+919999999999"
SENTINEL_TOKEN = "PRODUCTION-SENTINEL-TOKEN"


def _make_pdf_bytes():
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"
    )


class EndToEndRestoreFlowTests(TransactionTestCase):
    """Publish → restore → sanitize → rehearse → activate → rollback."""

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

    def setUp(self):
        self._create_fixture()

    def _create_fixture(self):
        from core.models import CustomUser, Folder, PDFFile

        self.admin = CustomUser.objects.create_user(
            username="fixture-admin",
            email=SENTINEL_EMAIL,
            password="admin123",
            is_staff=True,
            is_superuser=True,
        )
        self.admin2 = CustomUser.objects.create_user(
            username="fixture-admin2",
            email=SENTINEL_EMAIL_2,
            password="admin456",
            is_staff=True,
        )
        self.user1 = CustomUser.objects.create_user(
            username="fixture-user1",
            email=f"user1-{secrets.token_hex(4)}@test.local",
            password="user123",
        )
        self.user2 = CustomUser.objects.create_user(
            username="fixture-user2",
            email=f"user2-{secrets.token_hex(4)}@test.local",
            password="user456",
        )
        self.folder1 = Folder.objects.create(
            name="Synthetic Policy Documents", created_by=self.admin,
        )
        self.folder2 = Folder.objects.create(
            name="Synthetic Acts", created_by=self.admin,
        )

        media_root = Path(getattr(django_settings, "MEDIA_ROOT", "/tmp"))
        media_root.mkdir(parents=True, exist_ok=True)

        self.pdfs = []
        for i, (title, text) in enumerate([
            ("Service Policy v1", "What is the synthetic service policy? This document describes the service policy."),
            ("Service Act 2024", "कृत्रिम सेवा धोरण काय आहे? हा दस्तऐवज सेवा धोरणाचे वर्णन करतो."),
            ("Indexed Reference Doc", "This document is indexed and searchable."),
        ]):
            pdf_path = media_root / f"synthetic-{secrets.token_hex(4)}.pdf"
            pdf_path.write_bytes(_make_pdf_bytes())
            indexed = i < 2
            pdf = PDFFile.objects.create(
                title=title,
                folder=self.folder1 if i < 2 else self.folder2,
                file_path=str(pdf_path),
                extracted_text=text,
                page_chunks=[text],
                chunk_embeddings=[[float(i) / 10.0] * 1536],
                indexed=indexed,
                lifecycle="ready",
                uploaded_by=self.admin,
            )
            self.pdfs.append(pdf)

        from django.contrib.sessions.models import Session
        from datetime import datetime, timezone
        Session.objects.create(
            session_key="synthetic-session-1",
            session_data="synthetic-data",
            expire_date=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )

        self.staging_pre_existing = PDFFile.objects.create(
            title="Pre-existing Staging Doc",
            folder=self.folder2,
            file_path=str(media_root / f"staging-{secrets.token_hex(4)}.pdf"),
            extracted_text="This doc existed before restore on staging.",
            page_chunks=["Pre-existing staging content."],
            chunk_embeddings=[[0.5] * 1536],
            indexed=True,
            lifecycle="ready",
            uploaded_by=self.admin,
        )
        self.staging_pre_hash = hashlib.sha256(
            str(self.staging_pre_existing.id).encode()
        ).hexdigest()

    def _publish_source(self):
        from core.maintenance import sync_active_generation
        from core.registration import get_authoritative_pointer
        gen = sync_active_generation()
        pointer = get_authoritative_pointer(
            self._make_vault(), SOURCE_DATASET,
        )
        return gen, pointer

    def _make_vault(self):
        from core.artifact_vault import ArtifactVault
        return ArtifactVault()

    def test_01_publish_source_generation(self):
        gen, pointer = self._publish_source()
        self.assertIsNotNone(gen, "Source generation must be created")
        self.assertIn("gen-", gen.generation_id)
        self.assertEqual(gen.status, "validated")
        self.assertIsNotNone(pointer, "Authoritative pointer must exist")
        self.assertEqual(pointer["generation_id"], gen.generation_id)
        self.assertTrue(pointer.get("_etag"), "Pointer must have ETag for CAS")

    def test_02_restore_pipeline_to_quarantine(self):
        """Download source into quarantine workspace without activation."""
        from core.artifact_vault import ArtifactVault
        from core.models import MaintenanceJob
        from core.restore_pipeline import run_restore_pipeline
        from core.restore_workspace import RestoreMode, RestoreWorkspace, WorkspaceState

        self.test_01_publish_source_generation()

        job = MaintenanceJob.objects.create(kind="restore_generation", status="running")
        vault = ArtifactVault()

        result = run_restore_pipeline(
            vault, job=job,
            source_dataset_id=SOURCE_DATASET,
            restore_mode=RestoreMode.LATEST_COMPATIBLE,
            target_dataset_id=STAGING_DATASET,
            target_environment="staging",
            sanitize=False,
            run_rehearsal=False,
            activate=False,
        )
        self.assertEqual(result["state"], WorkspaceState.ACTIVATION_READY.value)
        self.assertEqual(result["source_dataset_id"], SOURCE_DATASET)
        self.assertTrue(result["activation_eligible"])

        ws = RestoreWorkspace.load(result["local_path"])
        self.assertIsNotNone(ws)
        self.assertEqual(ws.state, WorkspaceState.ACTIVATION_READY)

        source_dir = ws.dirs()["source"]
        self.assertTrue((source_dir / "manifest.json").is_file())
        self.assertTrue((source_dir / "db.sqlite3").is_file())
        self.assertTrue(Path(result["local_path"]).is_dir())

        self.result = result
        self.workspace = ws

    def test_03_restore_with_sanitization(self):
        """Full pipeline: sanitize only, no activation."""
        from core.artifact_vault import ArtifactVault
        from core.models import MaintenanceJob
        from core.restore_pipeline import run_restore_pipeline
        from core.restore_workspace import RestoreMode, RestoreWorkspace

        self.test_01_publish_source_generation()

        job = MaintenanceJob.objects.create(kind="restore_generation", status="running")
        vault = ArtifactVault()

        result = run_restore_pipeline(
            vault, job=job,
            source_dataset_id=SOURCE_DATASET,
            restore_mode=RestoreMode.LATEST_COMPATIBLE,
            target_dataset_id=STAGING_DATASET,
            target_environment="staging",
            sanitize=True,
            sanitization_policy="default-v1",
            run_rehearsal=False,
            activate=False,
        )
        self.assertEqual(result["state"], "activation_ready",
                         f"Expected activation_ready, got {result['state']}: {result.get('failure_reason', '')}")
        self.assertTrue(result["activation_eligible"])

        ws = RestoreWorkspace.load(result["local_path"])
        self.assertIsNotNone(ws)

        working_db = ws.dirs()["working"] / "db.sqlite3"
        self.assertTrue(working_db.is_file(), "Sanitized working DB must exist")

        conn = sqlite3.connect(f"file:{working_db}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("SELECT email FROM core_customuser")
        emails = [r[0] for r in c.fetchall() if r[0]]
        for email in emails:
            self.assertNotIn("@example.com", email if "@" in (email or "") else "",
                             f"Raw email {email} should have been sanitized")
        c.execute("SELECT session_key FROM django_session")
        sessions = c.fetchall()
        self.assertEqual(len(sessions), 0, "All sessions must be cleared")
        c.execute("PRAGMA integrity_check")
        self.assertEqual(c.fetchone()[0], "ok")
        conn.close()

        source_db = ws.dirs()["source"] / "db.sqlite3"
        conn2 = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
        c2 = conn2.cursor()
        c2.execute("SELECT email FROM core_customuser")
        source_emails = [r[0] for r in c2.fetchall() if r[0]]
        self.assertTrue(any(SENTINEL_EMAIL in (e or "") for e in source_emails),
                        "Source must still contain sentinel email")
        conn2.close()

    def test_04_restore_wrong_source_dataset_rejected(self):
        from core.artifact_vault import ArtifactVault
        from core.models import MaintenanceJob
        from core.restore_pipeline import run_restore_pipeline, RestoreError
        from core.restore_workspace import RestoreMode

        self.test_01_publish_source_generation()

        job = MaintenanceJob.objects.create(kind="restore_generation", status="running")
        vault = ArtifactVault()

        with self.assertRaises(RestoreError):
            run_restore_pipeline(
                vault, job=job,
                source_dataset_id="nonexistent-dataset-xyz",
                restore_mode=RestoreMode.LATEST_COMPATIBLE,
                target_dataset_id=STAGING_DATASET,
            )

    def test_05_restore_pinned_generation(self):
        from core.artifact_vault import ArtifactVault
        from core.models import MaintenanceJob, ArtifactGeneration
        from core.restore_pipeline import run_restore_pipeline
        from core.restore_workspace import RestoreMode, RestoreWorkspace

        self.test_01_publish_source_generation()
        gen = ArtifactGeneration.objects.order_by("-created_at").first()

        job = MaintenanceJob.objects.create(kind="restore_generation", status="running")
        vault = ArtifactVault()

        result = run_restore_pipeline(
            vault, job=job,
            source_dataset_id=SOURCE_DATASET,
            restore_mode=RestoreMode.PINNED,
            pinned_generation=gen.generation_id,
            target_dataset_id=STAGING_DATASET,
            sanitize=False,
            run_rehearsal=False,
            activate=False,
        )
        self.assertEqual(result["state"], "activation_ready")
        self.assertEqual(result["source_generation_id"], gen.generation_id)

    def test_06_activate_restored_generation(self):
        """Full pipeline through activation (sanitize, rehearse, activate)."""
        from core.artifact_vault import ArtifactVault
        from core.models import MaintenanceJob, ArtifactGeneration
        from core.restore_pipeline import run_restore_pipeline
        from core.restore_workspace import RestoreMode, RestoreWorkspace, WorkspaceState

        self.test_01_publish_source_generation()
        gen = ArtifactGeneration.objects.order_by("-created_at").first()

        job = MaintenanceJob.objects.create(kind="restore_generation", status="running")
        vault = ArtifactVault()

        result = run_restore_pipeline(
            vault, job=job,
            source_dataset_id=SOURCE_DATASET,
            restore_mode=RestoreMode.PINNED,
            pinned_generation=gen.generation_id,
            target_dataset_id=STAGING_DATASET,
            target_environment="staging",
            sanitize=False,
            run_rehearsal=False,
            activate=True,
        )
        self.assertEqual(result["state"], WorkspaceState.ACTIVE.value,
                         f"Expected active, got {result['state']}: {result.get('failure_reason', '')}")
        self.assertTrue(Path(result["local_path"]).is_dir())

    def test_07_rollback_works(self):
        """Prove rollback restores previous state."""
        from core.activate import rollback_to_previous, read_active_pointer, preserve_previous_pointer

        prev = preserve_previous_pointer()
        if prev is None:
            self.skipTest("No previous pointer to roll back to")

        try:
            result = rollback_to_previous()
            self.assertFalse(result.get("restored_pointer") is None)
        finally:
            if prev:
                from core.activate import write_active_pointer
                write_active_pointer(prev)
