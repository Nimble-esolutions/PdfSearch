"""Application-level publication integration tests.

Exercises the real publication code path: sync_active_generation(),
artifact vault, global writer, registration, and pointer CAS.
Runs against real MinIO and Redis. Requires Django setup.
"""

from __future__ import annotations

import json
import os
import secrets
import unittest

BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"


def _setup_django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
    os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
    os.environ.setdefault("APP_ENV", "production")
    os.environ.setdefault("DATA_MODE", "local")
    os.environ.setdefault("DATASET_ID", "app-pub-test")
    os.environ.setdefault("AUTHORITATIVE_DATASET_ID", "app-pub-test")
    os.environ.setdefault("PRODUCTION_SOURCE_ID", "prod-primary")
    os.environ.setdefault("DEPLOYMENT_ID", "app-test-deployment")
    os.environ.setdefault("BACKUP_ROLE", "writer")
    os.environ.setdefault("ARTIFACT_VAULT_ENABLED", "1")
    os.environ.setdefault("ARTIFACT_VAULT_ENDPOINT", ENDPOINT)
    os.environ.setdefault("ARTIFACT_VAULT_BUCKET", BUCKET)
    os.environ.setdefault("ARTIFACT_VAULT_REGION", "us-east-1")
    os.environ.setdefault("ARTIFACT_VAULT_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("ARTIFACT_VAULT_SECRET_KEY", "minioadmin")
    os.environ.setdefault("SECRET_KEY", "integration-test-key-not-for-production")
    os.environ.setdefault("DEBUG", "True")
    os.environ.setdefault("CREATE_SUPERUSER", "1")
    os.environ.setdefault("DJANGO_SUPERUSER_USERNAME", "admin")
    os.environ.setdefault("DJANGO_SUPERUSER_EMAIL", "admin@test.local")
    os.environ.setdefault("DJANGO_SUPERUSER_PASSWORD", "admin123")
    os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")
    os.environ.setdefault("REDIS_URL", "redis://redis:6379/1")
    import django
    django.setup()


_setup_django()

from django.test import TestCase
from django.conf import settings as django_settings

class ApplicationPublicationTests(TestCase):
    """Test the real sync_active_generation() code path against MinIO."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from core.artifact_vault import ArtifactVault
        from core.namespace import KeyBuilder
        cls.vault = ArtifactVault()
        cls.keys = KeyBuilder("app-pub-test")
        cls._clean_s3()

    @classmethod
    def _clean_s3(cls):
        from core.artifact_vault import ArtifactVault
        vault = ArtifactVault()
        import boto3
        c = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                         aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin")
        prefix = "datasets/app-pub-test/"
        try:
            resp = c.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
            objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if objs:
                c.delete_objects(Bucket=BUCKET, Delete={"Objects": objs, "Quiet": True})
        except Exception:
            pass

    def _create_test_data(self):
        from core.models import CustomUser, Folder, PDFFile
        user = CustomUser.objects.create_user(
            username=f"testuser-{secrets.token_hex(4)}",
            email=f"test-{secrets.token_hex(4)}@test.local",
            password="testpass123",
        )
        folder = Folder.objects.create(name="Test Folder", created_by=user)
        pdf = PDFFile.objects.create(
            title="Test Document",
            folder=folder,
            indexed=True,
            lifecycle="ready",
            uploaded_by=user,
        )
        return user, folder, pdf

    def test_01_register_dataset_via_application(self):
        """Register the dataset through the application registration module."""
        from core.registration import register_dataset, validate_registration
        self._clean_s3()
        reg = register_dataset(
            self.vault, "app-pub-test",
            production_source_id="prod-primary",
            instance_id="test-instance",
        )
        self.assertEqual(reg["dataset_id"], "app-pub-test")
        self.assertEqual(reg["production_source_id"], "prod-primary")

        loaded = validate_registration(
            self.vault, "app-pub-test",
            production_source_id="prod-primary",
        )
        self.assertEqual(loaded["dataset_id"], "app-pub-test")

    def test_02_sync_active_generation_creates_authoritative_snapshot(self):
        """Execute the real sync_active_generation() pipeline."""
        from core.maintenance import sync_active_generation
        from core.registration import get_authoritative_pointer
        self.test_01_register_dataset_via_application()
        self._create_test_data()

        gen = sync_active_generation()

        self.assertIsNotNone(gen)
        self.assertIn("gen-", gen.generation_id)
        self.assertEqual(gen.status, "validated")

        pointer = get_authoritative_pointer(self.vault, "app-pub-test")
        self.assertIsNotNone(pointer, "Authoritative pointer must exist after sync")
        self.assertEqual(pointer["generation_id"], gen.generation_id)

        manifest = gen.manifest
        files = manifest.get("files", [])
        self.assertGreater(len(files), 0, "Manifest must contain artifact files")

        db_files = [f for f in files if f.get("artifact_type") == "database"]
        self.assertGreater(len(db_files), 0, "Database artifact must exist in manifest")

        self.assertTrue(
            any(f["object_key"].startswith("datasets/app-pub-test/") for f in files),
            "Must use dataset-scoped object keys",
        )

    def test_03_non_writer_cannot_publish(self):
        """A non-writer environment identity cannot call sync_active_generation."""
        from core.environment import EnvironmentIdentity
        env_non = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "staging",
            "DATASET_ID": "app-pub-test",
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
        """Registration for wrong source fails."""
        from core.registration import register_dataset, validate_registration, RegistrationError
        self._clean_s3()
        register_dataset(
            self.vault, "app-pub-test",
            production_source_id="prod-primary",
            instance_id="test-instance",
        )
        with self.assertRaises(RegistrationError):
            validate_registration(
                self.vault, "app-pub-test",
                production_source_id="wrong-source",
            )

    def test_05_idempotent_sync_produces_new_generation(self):
        """Two sync calls produce two distinct generations."""
        from core.maintenance import sync_active_generation
        from core.models import ArtifactGeneration
        from core.registration import get_authoritative_pointer
        self.test_02_sync_active_generation_creates_authoritative_snapshot()
        gen1 = ArtifactGeneration.objects.order_by("-created_at").first()
        gen1_id = gen1.generation_id

        gen2 = sync_active_generation()
        self.assertNotEqual(gen2.generation_id, gen1_id)

        pointer = get_authoritative_pointer(self.vault, "app-pub-test")
        self.assertEqual(pointer["generation_id"], gen2.generation_id)
