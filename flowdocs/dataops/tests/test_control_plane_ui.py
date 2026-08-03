import base64
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import MaintenanceJob, MaintenancePlan
from dataops.config import resolve_profiles
from dataops.models import (
    BackupJob,
    DataConnection,
    DataCredential,
    DataOperation,
    DataOpsAuditEvent,
    DataProfile,
    RecoveryPoint,
)


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
            self.assertNotContains(overview, "Choose one outcome")
            advanced = self.client.get(reverse("dataops:advanced"))
            self.assertContains(advanced, "Choose one outcome")

    def test_compatibility_refresh_is_read_only_and_does_not_regress_to_500(self):
        response = self.client.post(reverse("dataops:refresh"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This check was read-only")
        self.assertFalse(MaintenanceJob.objects.exists())
        self.assertFalse(DataOperation.objects.using("control").exists())
        self.assertFalse(DataOpsAuditEvent.objects.using("control").exists())

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

    def test_job_records_route_and_requires_preview_for_destructive_mirror(self):
        safe = {"slug": "archive", "name": "Archive", "source_profile": "env-source", "target_profile": "archive-target", "mode": "archive", "timezone": "UTC"}
        response = self.client.post(reverse("dataops:jobs"), safe)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BackupJob.objects.using("control").filter(slug="archive").exists())
        destructive = {**safe, "slug": "mirror", "mode": "mirror", "delete_orphans": "1"}
        response = self.client.post(reverse("dataops:jobs"), destructive)
        self.assertEqual(response.status_code, 302)
        mirror = BackupJob.objects.using("control").get(slug="mirror")
        self.assertTrue(mirror.delete_orphans)
        response = self.client.post(reverse("dataops:run_job", args=[mirror.slug]))
        self.assertEqual(response.status_code, 409)

    def test_run_now_queues_checkpointed_sync_operation(self):
        job = BackupJob.objects.using("control").create(
            slug="archive",
            name="Archive",
            source_profile_key="env-source",
            target_profile_key="archive-target",
        )
        response = self.client.post(reverse("dataops:run_job", args=[job.slug]))
        self.assertRedirects(response, reverse("dataops:jobs"))
        operation = DataOperation.objects.using("control").get(kind=DataOperation.Kind.SYNC)
        self.assertEqual(operation.checkpoint["job_slug"], "archive")
        self.assertEqual(operation.source_profile_key, "env-source")

    def test_invalid_job_schedule_is_rejected(self):
        response = self.client.post(
            reverse("dataops:jobs"),
            {"slug": "bad", "name": "Bad", "source_profile": "env-source", "target_profile": "archive-target", "mode": "incremental", "schedule": "75 2 * * *", "timezone": "UTC"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BackupJob.objects.using("control").filter(slug="bad").exists())


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
class DataOpsV3RenderedActionTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings_override = override_settings(
            DATA_ROOT=root / "data",
            DATA_CONTROL_ROOT=root / "control",
            LEGACY_DATA_ROOT=root / "legacy",
        )
        for path in (root / "data", root / "control", root / "legacy"):
            path.mkdir()
        self.settings_override.enable()
        self.user = get_user_model().objects.create_superuser(
            "v3-operator",
            "v3-operator@example.invalid",
            "test-password",
        )
        self.client.force_login(self.user)
        self.connection = DataConnection.objects.using("control").create(
            name="Owned recovery store",
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

    def recovery_point(self):
        return RecoveryPoint.objects.using("control").create(
            connection=self.connection,
            dataset_id="ai-sahakar-stage-2026",
            release_id="stage-recovery-1",
            format_version=3,
            prefix="v3/recovery-points/stage-recovery-1.json",
            manifest_digest="b" * 64,
            signature_key_id="manifest-key-1",
            data_complete=True,
            state=RecoveryPoint.State.VERIFIED,
            evidence={"signature_valid": True},
            counts={"pdfs": 242, "objects": 300},
        )

    def test_workbench_exposes_v3_forms_without_profile_or_clone_selectors(self):
        self.recovery_point()

        response = self.client.get(reverse("dataops:workbench"))

        self.assertEqual(response.status_code, 200)
        for action in ("health_check", "backup", "test_recovery", "restore"):
            self.assertContains(response, f'name="action" value="{action}"')
        self.assertContains(response, reverse("dataops:workbench_action"))
        self.assertContains(response, "csrfmiddlewaretoken")
        for obsolete in (
            "source_profile",
            "destination_profile",
            "Clone / rebind",
            "Storage profiles",
            "Backup & sync jobs",
            "Advanced manual controls",
        ):
            self.assertNotContains(response, obsolete)

    def test_rendered_action_route_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(
            reverse("dataops:workbench_action"),
            {"action": "health_check"},
        )

        self.assertEqual(response.status_code, 403)

    def test_health_check_is_read_only(self):
        response = self.client.post(
            reverse("dataops:workbench_action"),
            {"action": "health_check"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This check was read-only")
        self.assertFalse(MaintenanceJob.objects.exists())
        self.assertFalse(DataOperation.objects.using("control").exists())
        self.assertFalse(DataOpsAuditEvent.objects.using("control").exists())

    @patch("dataops.views.discover_latest_recovery_point")
    @patch("dataops.views.manifest_signing_material")
    @patch("dataops.views.ensure_connection_readable")
    def test_fresh_control_store_can_discover_latest_signed_point(
        self,
        ensure_readable,
        signing_material,
        discover_latest,
    ):
        point = SimpleNamespace(
            release_id="stage-recovery-1",
            manifest_digest="b" * 64,
        )
        signing_material.return_value = (b"manifest-signing-key", "key-id")
        discover_latest.return_value = point

        self.assertFalse(RecoveryPoint.objects.using("control").exists())

        response = self.client.post(
            reverse("dataops:workbench_action"),
            {"action": "discover_latest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recovery point discovered")
        self.assertContains(response, point.release_id)
        self.assertContains(response, point.manifest_digest)
        self.assertContains(response, "No data was restored or activated")
        ensure_readable.assert_called_once_with(self.connection)
        discover_latest.assert_called_once_with(
            self.connection,
            signing_key=b"manifest-signing-key",
        )
        self.assertFalse(DataOperation.objects.using("control").exists())
        self.assertFalse(DataOpsAuditEvent.objects.using("control").exists())

    @patch("dataops.views.ensure_connection_readable")
    def test_discovery_failure_renders_reason_without_a_500_or_operation(
        self,
        ensure_readable,
    ):
        from dataops.v3_connection import V3ConnectionError

        ensure_readable.side_effect = V3ConnectionError(
            "owned_connection_not_readable"
        )

        response = self.client.post(
            reverse("dataops:workbench_action"),
            {"action": "discover_latest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "owned_connection_not_readable")
        self.assertFalse(DataOperation.objects.using("control").exists())
        self.assertFalse(DataOpsAuditEvent.objects.using("control").exists())

    def test_backup_preview_and_start_use_the_v3_plan_contract(self):
        preview = self.client.post(
            reverse("dataops:workbench_action"),
            {
                "phase": "preview",
                "action": "backup",
                "idempotency_key": "ui-backup-once",
            },
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.context["ui_preview"]["plan"]["route"], "backup")
        self.assertContains(preview, "Start reviewed operation")
        self.assertContains(preview, 'name="expected_plan_digest"')
        self.assertFalse(DataOperation.objects.using("control").exists())

        changed = self.client.post(
            reverse("dataops:workbench_action"),
            {
                "phase": "start",
                "action": "backup",
                "activate": "0",
                "idempotency_key": "ui-backup-once",
                "expected_plan_digest": "0" * 64,
            },
        )
        self.assertContains(changed, "plan_changed")
        self.assertFalse(DataOperation.objects.using("control").exists())

        started = self.client.post(
            reverse("dataops:workbench_action"),
            {
                "phase": "start",
                "action": "backup",
                "activate": "0",
                "idempotency_key": "ui-backup-once",
                "expected_plan_digest": preview.context["ui_preview"]["plan"]["plan_digest"],
            },
        )

        self.assertEqual(started.status_code, 200)
        self.assertContains(started, "Operation queued")
        operation = DataOperation.objects.using("control").get()
        status_url = reverse(
            "dataops:v3_operation_status",
            args=[operation.public_id],
        )
        self.assertContains(started, status_url)
        self.assertEqual(operation.lifecycle_route, "backup")
        self.assertEqual(
            operation.lifecycle_plan_digest,
            operation.lifecycle_plan["plan_digest"],
        )

        status = self.client.get(status_url)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["operation_id"], str(operation.public_id))
        self.assertEqual(status.json()["state"], DataOperation.State.QUEUED)
        self.assertEqual(DataOperation.objects.using("control").count(), 1)

    def test_activation_start_is_bound_to_the_exact_preview_confirmation(self):
        point = self.recovery_point()
        request_data = {
            "action": "restore",
            "activate": "1",
            "recovery_point_id": str(point.public_id),
            "idempotency_key": "ui-activation-once",
        }
        preview = self.client.post(
            reverse("dataops:workbench_action"),
            {**request_data, "phase": "preview"},
        )
        token = preview.context["ui_preview"]["confirmation"]["token"]
        request_data["expected_plan_digest"] = preview.context["ui_preview"]["plan"]["plan_digest"]

        refused = self.client.post(
            reverse("dataops:workbench_action"),
            {**request_data, "phase": "start", "confirmation": "wrong"},
        )
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "confirmation_mismatch")
        self.assertFalse(DataOperation.objects.using("control").exists())

        started = self.client.post(
            reverse("dataops:workbench_action"),
            {**request_data, "phase": "start", "confirmation": token},
        )
        self.assertEqual(started.status_code, 200)
        operation = DataOperation.objects.using("control").get()
        self.assertEqual(operation.lifecycle_plan["activation"], "signed_atomic")

    def test_invalid_boolean_renders_the_v3_error_without_a_500(self):
        response = self.client.post(
            reverse("dataops:workbench_action"),
            {"phase": "preview", "action": "backup", "activate": "perhaps"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "activate_invalid")
        self.assertFalse(DataOperation.objects.using("control").exists())

    def test_search_maintenance_shows_one_reason_and_no_recovery_machinery(self):
        maintenance = {
            "state_version": "state-v1",
            "capabilities": {
                "validate": {"enabled": True, "reason_code": ""},
                "repair_indexes": {
                    "enabled": False,
                    "reason_code": "mutation_tracking_disabled",
                },
                "reindex_needed": {"enabled": True, "reason_code": ""},
                "reindex_selected": {"enabled": True, "reason_code": ""},
            },
            "folders": [],
            "plans": [],
            "jobs": [],
            "selected_plan": None,
            "selected_job": None,
            "confirmation_phrase": "REINDEX SELECTED",
        }
        with patch(
            "dataops.views.workbench_maintenance_state",
            return_value=maintenance,
        ):
            response = self.client.get(reverse("dataops:advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mutation_tracking_disabled", count=1)
        self.assertContains(response, "Search maintenance")
        for obsolete in (
            "Clone / rebind",
            "Runtime activation and recovery",
            "Prepare for activation",
            "Review signed rollback",
            "source_profile",
            "destination_profile",
        ):
            self.assertNotContains(response, obsolete)

    def test_search_maintenance_collapses_shared_blocker_and_scales_scope(self):
        blocker = "mutation_tracking_disabled"
        maintenance = {
            "state_version": "state-v1",
            "capabilities": {
                "validate": {"enabled": True, "reason_code": ""},
                **{
                    operation: {
                        "enabled": False,
                        "reason_code": blocker,
                        "shared_blocker": True,
                        "shared_blocker_id": "maintenance-capability-blocker-1",
                    }
                    for operation in (
                        "repair_indexes",
                        "reindex_needed",
                        "reindex_selected",
                    )
                },
            },
            "capability_blockers": [
                {
                    "dom_id": "maintenance-capability-blocker-1",
                    "reason_code": blocker,
                    "affected_operations": [
                        "repair_indexes",
                        "reindex_needed",
                        "reindex_selected",
                    ],
                }
            ],
            "folders": [
                {"id": number, "name": f"Category {number:02d}", "pdf_count": number}
                for number in range(1, 47)
            ],
            "plans": [],
            "jobs": [],
            "active_jobs": [],
            "job_history": [],
            "selected_plan": None,
            "selected_job": None,
            "confirmation_phrase": "REINDEX SELECTED",
        }
        with patch(
            "dataops.views.workbench_maintenance_state",
            return_value=maintenance,
        ):
            response = self.client.get(reverse("dataops:advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, blocker, count=1)
        self.assertContains(response, "Shared requirement")
        self.assertContains(response, "data-maintenance-scope")
        self.assertContains(response, 'name="folder_ids"', count=46)
        self.assertContains(response, "maintenance-scope.js")
        self.assertContains(
            response,
            "Unavailable until the shared requirement is resolved.",
            count=3,
        )

    def test_search_maintenance_is_read_only_for_non_superadmins(self):
        admin = get_user_model().objects.create_user(
            "read-only-admin",
            "read-only-admin@example.invalid",
            "test-password",
            role="admin",
        )
        self.client.force_login(admin)
        maintenance = {
            "state_version": "state-v1",
            "capabilities": {
                operation: {
                    "enabled": True,
                    "reason_code": "",
                    "shared_blocker": False,
                    "shared_blocker_id": "",
                }
                for operation in (
                    "validate",
                    "repair_indexes",
                    "reindex_needed",
                    "reindex_selected",
                )
            },
            "capability_blockers": [],
            "folders": [],
            "plans": [],
            "jobs": [],
            "active_jobs": [],
            "attention_jobs": [],
            "job_history": [],
            "selected_plan": None,
            "selected_job": None,
            "confirmation_phrase": "REINDEX SELECTED",
        }
        with patch(
            "dataops.views.workbench_maintenance_state",
            return_value=maintenance,
        ):
            response = self.client.get(reverse("dataops:advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Superadmin approval required.", count=4)
        self.assertNotContains(response, 'type="submit" name="operation"')
        self.assertContains(
            response,
            '<h2 id="dataops-search-maintenance-heading">Search maintenance</h2>',
            html=True,
        )

    @override_settings(
        LOCAL_INDEX_MAINTENANCE_ENABLED=True,
        FORCE_REINDEX_ENABLED=True,
        EXTERNAL_EMBEDDINGS_ENABLED=True,
        VAULT_MUTATION_TRACKING_ENABLED=True,
        MAINTENANCE_WORKER_READINESS_REQUIRED=False,
        ACTIVE_RUNTIME=None,
        RUNTIME_GENERATION_ID="",
        RUNTIME_MANIFEST_DIGEST="",
    )
    def test_zero_item_repair_cannot_create_a_plan_or_queue_a_job(self):
        with patch(
            "core.maintenance_plans.maintenance_source_capability_reason",
            return_value="",
        ):
            response = self.client.post(
                reverse("vaultops:maintenance_plan_create"),
                {
                    "operation": "repair_indexes",
                    "idempotency_key": "ui-empty-repair",
                },
            )

        self.assertEqual(response.status_code, 303)
        self.assertFalse(MaintenancePlan.objects.exists())
        self.assertFalse(MaintenanceJob.objects.exists())
