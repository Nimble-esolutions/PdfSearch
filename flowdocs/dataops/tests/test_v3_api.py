"""API tests for the selector-free DataOps v3 plan preview."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dataops.models import DataConnection, DataOperation, RecoveryPoint
from dataops.v3_connection import V3ConnectionError


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
            capabilities={
                "probed": True,
                "read": True,
                "write": True,
                "conditional_write": True,
            },
            last_probed_at=timezone.now(),
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

    def post_operation(self, payload):
        return self.client.post(
            reverse("dataops:v3_operation_start"),
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

    def test_backup_start_persists_exact_plan_and_is_idempotent(self):
        payload = {"action": "backup", "idempotency_key": "backup-once"}
        first = self.post_operation(payload)
        second = self.post_operation(payload)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["operation_id"], second.json()["operation_id"])
        operation = DataOperation.objects.using("control").get(
            public_id=first.json()["operation_id"]
        )
        self.assertEqual(operation.lifecycle_route, "backup")
        self.assertEqual(
            operation.lifecycle_plan["plan_digest"],
            operation.lifecycle_plan_digest,
        )
        self.assertEqual(operation.connection, self.connection)

    def test_backup_start_automatically_refreshes_unproven_connection(self):
        self.connection.capabilities = {"probed": False}
        self.connection.last_probed_at = None
        self.connection.save(
            using="control",
            update_fields=["capabilities", "last_probed_at", "updated_at"],
        )

        def mark_ready(connection, **_kwargs):
            connection.capabilities = {
                "probed": True,
                "read": True,
                "write": True,
                "conditional_write": True,
            }
            connection.last_probed_at = timezone.now()
            connection.save(
                using="control",
                update_fields=["capabilities", "last_probed_at", "updated_at"],
            )
            return connection

        with patch(
            "dataops.views.ensure_owned_connection_ready",
            side_effect=mark_ready,
        ) as probe:
            response = self.post_operation(
                {"action": "backup", "idempotency_key": "auto-probe"}
            )
        self.assertEqual(response.status_code, 202)
        probe.assert_called_once()

    def test_failed_automatic_connection_check_creates_no_operation(self):
        self.connection.capabilities = {"probed": False}
        self.connection.last_probed_at = None
        self.connection.save(
            using="control",
            update_fields=["capabilities", "last_probed_at", "updated_at"],
        )
        with patch(
            "dataops.views.ensure_owned_connection_ready",
            side_effect=V3ConnectionError("bucket_access_failed"),
        ):
            response = self.post_operation({"action": "backup"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "bucket_access_failed")
        self.assertFalse(DataOperation.objects.using("control").exists())

    def test_activation_requires_exact_preview_confirmation_and_is_queued(self):
        point = self.recovery_point(
            dataset_id="ai-sahakar-stage-2026",
            release_id="stage-backup-activation",
            digest="e" * 64,
        )
        refused = self.post_operation(
            {
                "action": "restore",
                "recovery_point_id": str(point.public_id),
                "activate": True,
            }
        )
        self.assertEqual(refused.status_code, 409)
        self.assertIn(
            "operator_confirmation_required",
            refused.json()["plan"]["refusal_codes"],
        )
        self.assertFalse(DataOperation.objects.using("control").exists())

        preview = self.post_preview(
            {
                "action": "restore",
                "recovery_point_id": str(point.public_id),
                "activate": True,
                "confirmation": "present",
            }
        )
        self.assertEqual(preview.status_code, 200)
        confirmation = preview.json()["confirmation"]["token"]
        response = self.post_operation(
            {
                "action": "restore",
                "recovery_point_id": str(point.public_id),
                "activate": True,
                "confirmation": confirmation,
                "idempotency_key": "activate-once",
            }
        )
        self.assertEqual(response.status_code, 202)
        operation = DataOperation.objects.using("control").get(
            public_id=response.json()["operation_id"]
        )
        self.assertEqual(operation.lifecycle_plan["activation"], "signed_atomic")

    def test_restore_start_queues_exact_source_without_profile_selectors(self):
        point = self.recovery_point(
            dataset_id="ai-sahakar-stage-2026",
            release_id="stage-backup-restore",
            digest="f" * 64,
        )
        response = self.post_operation(
            {
                "action": "restore",
                "recovery_point_id": str(point.public_id),
                "idempotency_key": "restore-once",
            }
        )
        self.assertEqual(response.status_code, 202)
        operation = DataOperation.objects.using("control").get(
            public_id=response.json()["operation_id"]
        )
        self.assertEqual(operation.kind, DataOperation.Kind.RESTORE)
        self.assertEqual(
            operation.checkpoint["recovery_point_id"],
            str(point.public_id),
        )

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

    def test_legacy_object_store_import_binds_preview_confirmation_and_queue(self):
        source = DataConnection.objects.using("control").create(
            name="Legacy production source",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="legacy-production-v2",
            dataset_id="ai-sahakar-prod-v2",
            credential_ref="secret://dataops/legacy-source",
            capabilities={"read": True},
        )
        generation = SimpleNamespace(
            dataset_id=source.dataset_id,
            generation_id="legacy-20260802T085639Z",
            manifest_sha256="d" * 64,
        )
        request = {
            "action": "import",
            "source_connection_id": str(source.public_id),
            "source_generation_id": generation.generation_id,
        }
        with patch("dataops.views.client_for_connection"), patch(
            "dataops.views.load_legacy_generation",
            return_value=generation,
        ):
            preview = self.post_preview(request)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["plan"]["route"], "legacy_import")
        token = preview.json()["confirmation"]["token"]
        with patch("dataops.views.client_for_connection"), patch(
            "dataops.views.load_legacy_generation",
            return_value=generation,
        ):
            started = self.post_operation(
                {
                    **request,
                    "confirmation": token,
                    "idempotency_key": "legacy-import-once",
                }
            )
        self.assertEqual(started.status_code, 202)
        operation = DataOperation.objects.using("control").get(
            public_id=started.json()["operation_id"]
        )
        self.assertEqual(operation.kind, DataOperation.Kind.IMPORT)
        self.assertEqual(operation.lifecycle_route, "legacy_import")
        self.assertEqual(
            operation.checkpoint["source_connection_id"],
            str(source.public_id),
        )
        self.assertEqual(
            operation.checkpoint["source_manifest_sha256"],
            generation.manifest_sha256,
        )

    def test_legacy_object_store_import_rejects_unbound_confirmation(self):
        source = DataConnection.objects.using("control").create(
            name="Legacy source",
            endpoint="https://rustfs.example.invalid",
            bucket="legacy-source",
            dataset_id="legacy-source-v2",
            credential_ref="secret://dataops/legacy-source",
            capabilities={"read": True},
        )
        generation = SimpleNamespace(
            dataset_id=source.dataset_id,
            generation_id="legacy-explicit",
            manifest_sha256="e" * 64,
        )
        with patch("dataops.views.client_for_connection"), patch(
            "dataops.views.load_legacy_generation",
            return_value=generation,
        ):
            response = self.post_operation(
                {
                    "action": "import",
                    "source_connection_id": str(source.public_id),
                    "source_generation_id": generation.generation_id,
                    "confirmation": "stale-or-unbound",
                }
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "confirmation_mismatch")
        self.assertFalse(
            DataOperation.objects.using("control")
            .filter(kind=DataOperation.Kind.IMPORT)
            .exists()
        )

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
