"""Application-level publication integration tests.

Exercises sync_active_generation(), artifact vault, global writer,
registration, and pointer CAS against real MinIO and Redis.
Uses flowdocs.settings_integration for file-backed SQLite.

Run as:
  DJANGO_SETTINGS_MODULE=flowdocs.settings_integration \
  ARTIFACT_VAULT_ENABLED=1 \
  ARTIFACT_VAULT_ENDPOINT=http://pdfsearch-minio:9000 \
  ARTIFACT_VAULT_BUCKET=pdfsearch-test \
  ARTIFACT_VAULT_REGION=us-east-1 \
  ARTIFACT_VAULT_ACCESS_KEY=minioadmin \
  ARTIFACT_VAULT_SECRET_KEY=minioadmin \
  REDIS_URL=redis://redis:6379/1 \
  PYTHONPATH=/app:/app/flowdocs \
  python manage.py test integration_tests.test_app_publication --no-input -v2
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings_integration")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "integration-test-key")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")

# Must import after settings module is configured
import django
django.setup()

from django.conf import settings as django_settings
from django.test import TransactionTestCase


BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"
TEST_DATASET = "app-pub-test"


class ApplicationPublicationTests(TransactionTestCase):
    """Test sync_active_generation() against real MinIO with file-backed SQLite."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from core.artifact_vault import ArtifactVault
        cls.vault = ArtifactVault()
        cls._clean_s3()

    def setUp(self):
        self._create_test_data()

    @classmethod
    def tearDownClass(cls):
        cls._clean_s3()
        super().tearDownClass()

    @staticmethod
    def _set_env(name, value):
        orig = os.environ.get(name)
        os.environ[name] = value
        if not hasattr(ApplicationPublicationTests, "int_env"):
            ApplicationPublicationTests.int_env = {}
        ApplicationPublicationTests.int_env[name] = orig

    @classmethod
    def _clean_s3(cls):
        import boto3
        c = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                         aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin")
        prefix = f"datasets/{TEST_DATASET}/"
        try:
            resp = c.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
            objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if objs:
                c.delete_objects(Bucket=BUCKET, Delete={"Objects": objs, "Quiet": True})
        except Exception:
            pass

    @staticmethod
    def _create_test_data():
        from core.models import CustomUser, Folder, PDFFile

        user = CustomUser.objects.create_user(
            username=f"testuser-{secrets.token_hex(4)}",
            email=f"test-{secrets.token_hex(4)}@test.local",
            password="testpass123",
        )
        folder = Folder.objects.create(name="Test Folder", created_by=user)

        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"

        media_root = Path(getattr(django_settings, "MEDIA_ROOT", "/app/data/media"))
        media_root.mkdir(parents=True, exist_ok=True)
        pdf_path = media_root / f"test-{secrets.token_hex(4)}.pdf"
        pdf_path.write_bytes(pdf_content)

        PDFFile.objects.create(
            title="Test Document",
            folder=folder,
            indexed=True,
            lifecycle="ready",
            uploaded_by=user,
            file_path=str(pdf_path),
            extracted_text="Test extracted text for search indexing.",
            page_chunks=["Test chunk 1", "Test chunk 2"],
            chunk_embeddings=[[0.0] * 1536, [0.1] * 1536],
        )

    def _reload_settings(self):
        import importlib
        from django.conf import settings as s
        importlib.reload(s)
        importlib.reload(django_settings)
        from core.environment import EnvironmentIdentity
        return EnvironmentIdentity.from_env()

    def test_01_register_dataset_via_application(self):
        from core.registration import register_dataset, validate_registration
        self._clean_s3()
        reg = register_dataset(
            self.vault, TEST_DATASET,
            production_source_id="prod-primary",
            instance_id="test-instance",
        )
        self.assertEqual(reg["dataset_id"], TEST_DATASET)
        self.assertEqual(reg["production_source_id"], "prod-primary")
        loaded = validate_registration(
            self.vault, TEST_DATASET,
            production_source_id="prod-primary",
        )
        self.assertEqual(loaded["dataset_id"], TEST_DATASET)

    def test_02_sync_active_generation_creates_authoritative_snapshot(self):
        from core.maintenance import sync_active_generation
        from core.registration import get_authoritative_pointer

        self.test_01_register_dataset_via_application()

        gen = sync_active_generation()
        self.assertIsNotNone(gen)
        self.assertIn("gen-", gen.generation_id)
        self.assertEqual(gen.status, "validated")

        pointer = get_authoritative_pointer(self.vault, TEST_DATASET)
        self.assertIsNotNone(pointer, "Authoritative pointer must exist after sync")
        self.assertEqual(pointer["generation_id"], gen.generation_id)

        manifest = gen.manifest
        files = manifest.get("files", [])
        self.assertGreater(len(files), 0, "Manifest must contain artifact files")

        db_files = [f for f in files if f.get("artifact_type") == "database"]
        self.assertGreater(len(db_files), 0, "Database artifact must exist in manifest")

        self.assertTrue(
            any(f["object_key"].startswith(f"datasets/{TEST_DATASET}/") for f in files),
            "Must use dataset-scoped object keys",
        )

    def test_03_non_writer_cannot_publish(self):
        from core.environment import EnvironmentIdentity
        env_non = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "staging",
            "DATASET_ID": TEST_DATASET,
            "BACKUP_ROLE": "reader",
            "ARTIFACT_VAULT_ENABLED": "1",
            "ARTIFACT_VAULT_ENDPOINT": ENDPOINT,
            "ARTIFACT_VAULT_BUCKET": BUCKET,
            "ARTIFACT_VAULT_REGION": "us-east-1",
            "ARTIFACT_VAULT_ACCESS_KEY": "minioadmin",
            "ARTIFACT_VAULT_SECRET_KEY": "minioadmin",
            "ALLOW_INSECURE_DEFAULTS": "1",
            "REDIS_URL": "redis://redis:6379/1",
            "SECRET_KEY": "test",
            "DATA_BOOTSTRAP_MODE": "empty",
        })
        self.assertFalse(env_non.is_authoritative_writer)

    def test_04_registration_blocks_wrong_source(self):
        from core.registration import register_dataset, validate_registration, RegistrationError
        self._clean_s3()
        register_dataset(
            self.vault, TEST_DATASET,
            production_source_id="prod-primary",
            instance_id="test-instance",
        )
        with self.assertRaises(RegistrationError):
            validate_registration(
                self.vault, TEST_DATASET,
                production_source_id="wrong-source",
            )

    def test_05_idempotent_sync_produces_new_generation(self):
        from core.maintenance import sync_active_generation
        from core.models import ArtifactGeneration
        from core.registration import get_authoritative_pointer

        self.test_02_sync_active_generation_creates_authoritative_snapshot()
        gen1 = ArtifactGeneration.objects.order_by("-created_at").first()
        gen1_id = gen1.generation_id

        gen2 = sync_active_generation()
        self.assertNotEqual(gen2.generation_id, gen1_id)

        pointer = get_authoritative_pointer(self.vault, TEST_DATASET)
        self.assertEqual(pointer["generation_id"], gen2.generation_id)
