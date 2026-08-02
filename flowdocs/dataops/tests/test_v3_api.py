"""API tests for the selector-free DataOps v3 plan preview."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from dataops.models import DataConnection, RecoveryPoint


@override_settings(
    DATAOPS_ENABLED=True,
    APP_ENV="staging",
    DEPLOYMENT_ID="stage-2026",
    DATASET_ID="ai-sahakar-stage-2026",
    ACTIVATION_INTENT_SIGNING_KEY="test-signing-key",
    RUNTIME_GENERATION_ID="stage-active",
    RUNTIME_MANIFEST_DIGEST="a" * 64,
    ENV_IDENTITY=SimpleNamespace(
        app_env=SimpleNamespace(value="staging"),
        deployment_id="stage-2026",
        dataset_id="ai-sahakar-stage-2026",
    ),
)
class DataOpsV3APITests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_root = self.root / "data"
        self.control_root = self.root / "control"
        self.legacy_root = self.root / "legacy"
        for path in (self.data_root, self.control_root, self.legacy_root):
            path.mkdir()
        self.settings_override = override_settings(
            DATA_ROOT=self.data_root,
            DATA_CONTROL_ROOT=self.control_root,
            LEGACY_DATA_ROOT=self.legacy_root,
        )
        self.settings_override.enable()
        self.user = get_user_model().objects.create_superuser(
            "operator",
            "operator@example.invalid",
            "test-password",
        )
        self.client.force_login(self.user)
        self.connection = DataConnection.objects.using("control").create(
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
            capabilities={"read": True, "write": True, "conditional_write": True},
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temporary.cleanup()

    def post_preview(self, payload):
        return self.client.post(
            reverse("dataops:v3_operation_preview"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def recovery_point(self, *, dataset_id, release_id, digest):
        connection = self.connection if dataset_id == self.connection.dataset_id else DataConnection.objects.using("control").create(
            name="Read-only source",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="foreign-source",
            dataset_id=dataset_id,
            credential_ref="secret://dataops/source",
            capabilities={"read": True, "write": False},
        )
        return RecoveryPoint.objects.using("control").create(
            connection=connection,
            dataset_id=dataset_id,
            release_id=release_id,
            format_version=3,
            prefix=f"v3/recovery-points/{release_id}.json",
            manifest_digest=digest,
            signature_key_id="manifest-key-1",
            data_complete=True,
            state=RecoveryPoint.State.VERIFIED,
            evidence={"signature_valid": True},
        )

    def test_status_has_one_connection_and_no_profile_selectors_or_secrets(self):
        response = self.client.get(reverse("dataops:v3_status"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["actions"]["backup"], "ready")
        serialized = json.dumps(payload).lower()
        self.assertNotIn("source_profile", serialized)
        self.assertNotIn("destination_profile", serialized)
        self.assertNotIn("secret_key", serialized)

    def test_backup_preview_needs_no_profile_or_backup_mode(self):
        response = self.post_preview({"action": "backup"})
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["route"], "backup")
        self.assertEqual(plan["transfer_mode"], "full_upload")

    def test_same_dataset_point_routes_to_normal_restore(self):
        point = self.recovery_point(
            dataset_id="ai-sahakar-stage-2026",
            release_id="stage-backup-1",
            digest="b" * 64,
        )
        response = self.post_preview(
            {
                "action": "restore",
                "recovery_point_id": str(point.public_id),
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["route"], "same_dataset_restore")

    def test_foreign_point_routes_to_import_rebind_without_operator_selector(self):
        point = self.recovery_point(
            dataset_id="ai-sahakar-prod-v2",
            release_id="legacy-prod-1",
            digest="c" * 64,
        )
        response = self.post_preview(
            {
                "action": "restore",
                "recovery_point_id": point.release_id,
                "activate": True,
                "confirmation": "approved",
            }
        )
        self.assertEqual(response.status_code, 200)
        plan = response.json()["plan"]
        self.assertEqual(plan["route"], "import_rebind_restore")
        self.assertIn("automatic_clone_rebind", plan["reason_codes"])

    def test_legacy_mount_import_is_discovered_from_configured_read_only_source(self):
        response = self.post_preview(
            {"action": "import", "source_kind": "legacy_mount"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["route"], "legacy_import")

    def test_secret_fields_are_rejected_without_echoing_values(self):
        response = self.post_preview(
            {"action": "backup", "secret_key": "do-not-echo"}
        )
        self.assertEqual(response.status_code, 400)
        serialized = response.content.decode("utf-8")
        self.assertIn("secret_fields_forbidden", serialized)
        self.assertNotIn("do-not-echo", serialized)

    def test_non_superadmin_cannot_preview_operations(self):
        user = get_user_model().objects.create_user(
            "viewer",
            "viewer@example.invalid",
            "test-password",
        )
        self.client.force_login(user)
        response = self.post_preview({"action": "backup"})
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse("dataops:v3_status"))
        self.assertEqual(response.status_code, 403)
