import base64
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from dataops.config import resolve_profiles
from dataops.models import BackupJob, DataCredential, DataProfile


@override_settings(DATAOPS_UI_CONFIG_ENABLED=True)
class DataOpsControlPlaneUITests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("operator", "operator@example.invalid", "test-password")
        self.client.force_login(self.user)
        self.environment = {
            "DATAOPS_CONFIG_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
            "DATAOPS_PROFILE_MANIFEST": '[{"name":"env-source","role":"restore","provider":"rustfs","endpoint":"https://rustfs.example.invalid","bucket":"source","dataset_id":"dataset","namespace":"source","credential_ref":"ENV_SOURCE"}]',
            "DATAOPS_RESTORE_PROFILE": "env-source",
        }

    def test_four_control_plane_pages_are_separate_and_reachable(self):
        with patch.dict(os.environ, self.environment, clear=False):
            for name in ("dataops:workbench", "dataops:configuration", "dataops:jobs", "dataops:advanced"):
                with self.subTest(name=name):
                    response = self.client.get(reverse(name))
                    self.assertEqual(response.status_code, 200)
            overview = self.client.get(reverse("dataops:workbench"))
            self.assertNotContains(overview, "Choose the outcome you need")
            advanced = self.client.get(reverse("dataops:advanced"))
            self.assertContains(advanced, "Choose the outcome you need")

    def test_stored_profile_and_encrypted_credentials_coexist_with_environment_profile(self):
        payload = {
            "action": "save_profile",
            "key": "archive-target",
            "display_name": "Archive target",
            "provider": "cloudflare_r2",
            "role": "backup",
            "endpoint": "https://account.r2.cloudflarestorage.com",
            "region": "auto",
            "addressing_style": "auto",
            "bucket": "archive",
            "namespace": "snapshots/archive",
            "dataset_id": "dataset",
            "source_id": "archive",
            "credential_ref": "R2_ARCHIVE",
            "access_key": "test-access",
            "secret_key": "test-secret",
            "verify_tls": "1",
            "enabled": "1",
        }
        with patch.dict(os.environ, self.environment, clear=False):
            response = self.client.post(reverse("dataops:configuration"), payload)
            self.assertRedirects(response, reverse("dataops:configuration"))
            profiles = resolve_profiles()

        self.assertEqual([profile.key for profile in profiles], ["archive-target", "env-source"])
        row = DataProfile.objects.using("control").get(key="archive-target")
        credential = DataCredential.objects.using("control").get(profile=row)
        self.assertTrue(credential.enabled)
        self.assertNotEqual(credential.access_key_nonce, credential.secret_nonce)
        self.assertNotIn("test-secret", credential.secret_ciphertext)

    def test_job_records_route_but_refuses_destructive_mirror_submission(self):
        safe = {"slug": "archive", "name": "Archive", "source_profile": "env-source", "target_profile": "archive-target", "mode": "archive", "timezone": "UTC"}
        response = self.client.post(reverse("dataops:jobs"), safe)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BackupJob.objects.using("control").filter(slug="archive").exists())
        destructive = {**safe, "slug": "mirror", "mode": "mirror", "delete_orphans": "1"}
        response = self.client.post(reverse("dataops:jobs"), destructive)
        self.assertEqual(response.status_code, 409)
