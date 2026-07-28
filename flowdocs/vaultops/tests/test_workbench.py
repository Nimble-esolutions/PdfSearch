import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.lease import acquire_lease, release_lease
from core.models import ArtifactGeneration as LegacyGeneration
from core.models import CustomUser, Folder, MaintenanceAuditEvent, MaintenanceJob
from core.models import PDFFile
from core.maintenance_plans import (
    _prepared_workspace_ids,
    _serialize_local_job_payload,
    create_plan,
)
from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    ArtifactValidation,
    ConfirmationChallenge,
    GarbageCollectionPlan,
    RetentionHold,
    RestoreWorkspace,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultAuditEvent,
    VaultJob,
)
from vaultops.services.read_model import (
    build_workbench_state,
    enrich_workbench_readiness,
    workspace_state_digest,
)
from vaultops.services.activation import ActivationCoordinatorError


@override_settings(
    VAULT_ADMIN_MUTATIONS_ENABLED=True,
    VAULT_SYNC_ENABLED=True,
    VAULT_RESTORE_ENABLED=True,
)
class VaultWorkbenchTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username="vault-superadmin",
            password="test-password",
            role="superadmin",
            is_staff=True,
            is_superuser=True,
        )
        self.admin = CustomUser.objects.create_user(
            username="vault-admin",
            password="test-password",
            role="admin",
            is_staff=True,
        )
        identity = settings.ENV_IDENTITY
        self.profile = VaultConnectionProfile.objects.create(
            key=settings.VAULT_DEFAULT_PROFILE,
            display_name="Locked environment vault",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            enabled=True,
            read_only=False,
            environment_locked=True,
            endpoint_origin="https://vault.example",
            bucket="artifacts",
            region="test",
            dataset_id=identity.dataset_id,
            production_source_id=identity.production_source_id,
            credential_alias="environment:ARTIFACT_VAULT",
            fingerprint="f" * 64,
        )
        VaultDatasetProjection.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            inventory_state="verified",
            authoritative_generation_id="generation-authoritative",
            pointer_digest="a" * 64,
        )
        self.candidate = ArtifactGeneration.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            generation_id="generation-candidate",
            manifest_digest="b" * 64,
            vault_state=ArtifactGeneration.VaultState.CANDIDATE,
            runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
            local_presence=ArtifactGeneration.LocalPresence.ABSENT,
            manifest={"files": []},
        )
        self.client.force_login(self.superadmin)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_superadmin_workbench_is_server_rendered_and_no_js_required(self):
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vault Operations Workbench")
        self.assertContains(response, "Authority comparison")
        self.assertContains(response, "vault-workbench.css")
        self.assertContains(response, "<noscript>", html=False)
        self.assertNotContains(response, "cdn.jsdelivr.net")

    def test_restore_section_keeps_signed_rollback_visible_with_reason(self):
        response = self.client.get(
            reverse("operations_panel"), {"section": "restore"}
        )

        self.assertContains(
            response, "Roll back the latest maintenance activation"
        )
        self.assertContains(response, "Review signed rollback")
        self.assertContains(response, "staging_activation_disabled")
        self.assertContains(
            response, reverse("vaultops:rollback_confirmation_issue")
        )

    @override_settings(STAGING_RUNTIME_ACTIVATION_ENABLED=True)
    @patch(
        "vaultops.services.read_model._rollback_capability",
        return_value={
            "enabled": True,
            "reason_code": "",
            "target_generation_id": "verified-previous",
        },
    )
    def test_signed_rollback_control_is_enabled_for_verified_parent(
        self, capability
    ):
        response = self.client.get(
            reverse("operations_panel"), {"section": "restore"}
        )

        self.assertContains(
            response,
            '<button class="vault-button vault-button--danger" '
            'type="submit">Review signed rollback</button>',
            html=True,
        )
        self.assertNotContains(response, "staging_activation_disabled")
        self.assertContains(response, "verified-previous")
        capability.assert_called_once()

    @override_settings(STAGING_RUNTIME_ACTIVATION_ENABLED=True)
    @patch(
        "vaultops.services.read_model._rollback_capability",
        return_value={
            "enabled": False,
            "reason_code": "rollback_lineage_invalid",
            "target_generation_id": "",
        },
    )
    def test_signed_rollback_control_displays_typed_eligibility_reason(
        self, capability
    ):
        response = self.client.get(
            reverse("operations_panel"), {"section": "restore"}
        )

        self.assertContains(response, "rollback_lineage_invalid")
        self.assertContains(
            response,
            'disabled aria-describedby="signed-rollback-reason"',
            html=False,
        )
        capability.assert_called_once()

    @patch(
        "vaultops.services.read_model._rollback_capability",
        return_value={
            "enabled": True,
            "reason_code": "",
            "target_generation_id": "verified-previous",
        },
    )
    def test_committed_activation_is_not_projected_as_pending(
        self, capability
    ):
        identity = settings.ENV_IDENTITY
        ActivationIntent.objects.using("control").create(
            deployment_id=identity.deployment_id,
            target_generation_id="committed-target",
            previous_generation_id="committed-parent",
            manifest_digest="c" * 64,
            intent_digest="d" * 64,
            state=ActivationIntent.State.COMMITTED,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        state = build_workbench_state(profile_key=self.profile.key)

        self.assertIsNone(state["pending_activation"])
        capability.assert_called_once_with(pending_activation=None)

    @override_settings(STAGING_RUNTIME_ACTIVATION_ENABLED=True)
    def test_non_superadmin_cannot_issue_or_schedule_signed_rollback(self):
        self.client.force_login(self.admin)
        issue = self.client.post(
            reverse("vaultops:rollback_confirmation_issue"),
            {"idempotency_key": str(uuid.uuid4())},
        )
        schedule = self.client.post(
            reverse(
                "vaultops:schedule_rollback",
                kwargs={"workspace_id": uuid.uuid4()},
            ),
            {"idempotency_key": str(uuid.uuid4())},
        )

        self.assertEqual(issue.status_code, 403)
        self.assertEqual(schedule.status_code, 403)

    @override_settings(STAGING_RUNTIME_ACTIVATION_ENABLED=True)
    @patch("vaultops.views.create_recovery_set")
    @patch("vaultops.views.validate_previous_runtime_rollback")
    def test_stale_rollback_is_rejected_before_recovery_or_confirmation_use(
        self, revalidate, create_recovery
    ):
        revalidate.side_effect = ActivationCoordinatorError(
            "rollback_authority_changed"
        )
        workspace = RestoreWorkspace.objects.create(
            generation=self.candidate,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=self.candidate.manifest_digest,
            runtime_path="/isolated/runtime",
            validation_evidence={"purpose": "one_step_runtime_rollback"},
            rehearsal_evidence={"success": True},
            prepared_at=timezone.now(),
        )

        response = self.client.post(
            reverse(
                "vaultops:schedule_rollback",
                kwargs={"workspace_id": workspace.public_id},
            ),
            {"idempotency_key": str(uuid.uuid4())},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"], "rollback_authority_changed"
        )
        create_recovery.assert_not_called()
        self.assertFalse(ConfirmationChallenge.objects.exists())

    def test_maintenance_health_and_candidate_publication_are_evidenced(self):
        ArtifactValidation.objects.create(
            generation=self.candidate,
            validation_type="full",
            status=ArtifactValidation.Status.PASSED,
            manifest_digest=self.candidate.manifest_digest,
        )
        workspace = RestoreWorkspace.objects.create(
            generation=self.candidate,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=self.candidate.manifest_digest,
            rehearsal_evidence={"success": True},
            prepared_at=timezone.now(),
        )
        MaintenanceJob.objects.create(
            kind="reindex_selected",
            status="completed",
            options={
                "candidate_workspace_id": "mw-evidence",
                "candidate_state": "activation_ready",
            },
        )
        response = self.client.get(
            f"{reverse('operations_panel')}?section=maintenance"
        )
        self.assertContains(response, "Verified Vault generations")
        self.assertContains(response, "Free-space reserve")
        self.assertContains(response, "Passed")
        self.assertContains(response, str(workspace.public_id))
        self.assertContains(response, "mw-evidence")
        self.assertContains(response, "Vault publication required")
        self.assertContains(response, "vendor/bootstrap/5.3.0")
        sync_response = self.client.get(
            reverse("operations_panel"), {"section": "sync"}
        )
        self.assertContains(sync_response, "Queue publish-only sync")

    def test_prepared_workspace_lookup_is_one_control_query_for_many_jobs(self):
        jobs = []
        expected = {}
        for index in range(3):
            job = MaintenanceJob.objects.create(
                kind="reindex_selected",
                status="completed",
                options={"candidate_state": "activation_ready"},
            )
            generation = ArtifactGeneration.objects.create(
                profile=self.profile,
                origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
                dataset_id=self.profile.dataset_id,
                generation_id=f"local-candidate-{index}",
                manifest_digest=f"{index}" * 64,
                vault_state=ArtifactGeneration.VaultState.UNKNOWN,
                runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
                local_presence=ArtifactGeneration.LocalPresence.PREPARED,
                lineage_job_public_id=job.public_id,
                parent_generation_id="parent-generation",
                parent_manifest_digest="p" * 64,
            )
            workspace = RestoreWorkspace.objects.create(
                generation=generation,
                state=RestoreWorkspace.State.ACTIVATION_READY,
                manifest_digest=generation.manifest_digest,
                prepared_at=timezone.now(),
            )
            jobs.append(job)
            expected[str(job.public_id)] = str(workspace.public_id)

        with CaptureQueriesContext(connections["control"]) as queries:
            prepared = _prepared_workspace_ids(jobs)
        self.assertEqual(len(queries), 1)
        self.assertEqual(prepared, expected)

        with CaptureQueriesContext(connections["control"]) as queries:
            payloads = [
                _serialize_local_job_payload(
                    job,
                    prepared_workspace_by_job=prepared,
                )
                for job in jobs
            ]
        self.assertEqual(len(queries), 0)
        self.assertEqual(
            {payload["prepared_workspace_id"] for payload in payloads},
            set(expected.values()),
        )

    def test_prepared_workspace_lookup_skips_query_without_eligible_jobs(self):
        jobs = [
            MaintenanceJob.objects.create(
                kind="validate",
                status="running",
            ),
            MaintenanceJob.objects.create(
                kind="reindex_selected",
                status="completed",
                options={"candidate_state": "building"},
            ),
        ]

        with CaptureQueriesContext(connections["control"]) as queries:
            prepared = _prepared_workspace_ids(jobs)

        self.assertEqual(prepared, {})
        self.assertEqual(len(queries), 0)

    def test_ready_candidate_keeps_prepare_control_visible_with_typed_reason(self):
        MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="completed",
            options={
                "candidate_workspace_id": "mw-disabled",
                "candidate_state": "activation_ready",
            },
        )

        response = self.client.get(
            reverse("operations_panel"), {"section": "maintenance"}
        )

        self.assertContains(response, "Prepare for activation")
        self.assertContains(response, "candidate_preparation_disabled")

    @override_settings(MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=True)
    @patch("vaultops.views.import_maintenance_candidate")
    def test_superadmin_can_prepare_ready_candidate(self, importer):
        job = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="completed",
            options={
                "candidate_workspace_id": "mw-ready",
                "candidate_state": "activation_ready",
            },
        )
        generation = SimpleNamespace(
            generation_id="lm-prepared",
            origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
        )
        importer.return_value = SimpleNamespace(
            public_id=uuid.uuid4(),
            generation=generation,
        )
        page = self.client.get(
            reverse("operations_panel"), {"section": "maintenance"}
        )
        rendered_job = next(
            item
            for item in page.context["state"]["maintenance"]["jobs"]
            if item["public_id"] == str(job.public_id)
        )

        response = self.client.post(
            reverse(
                "vaultops:maintenance_candidate_prepare",
                kwargs={"job_id": job.public_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": rendered_job["state_version"],
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(
            response.json()["data"]["origin"], "local_maintenance"
        )
        self.assertFalse(
            response.json()["data"]["vault_authority_changed"]
        )
        importer.assert_called_once()

    @override_settings(MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=True)
    def test_non_superadmin_cannot_prepare_candidate(self):
        job = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="completed",
            options={
                "candidate_workspace_id": "mw-ready",
                "candidate_state": "activation_ready",
            },
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                "vaultops:maintenance_candidate_prepare",
                kwargs={"job_id": job.public_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": "invalid",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_non_superadmin_is_forbidden(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 403)

    def test_readiness_exposes_typed_remediation_destinations(self):
        state = {
            "authority": {
                "blocking_reasons": ["profile_unavailable", "inventory_unverified"],
            },
            "maintenance": {
                "health": {
                    "free_space_reserve": {"state": "degraded"},
                },
                "capabilities": {
                    "reindex_selected": {
                        "enabled": False,
                        "reason_code": "external_embeddings_disabled",
                    },
                },
            },
            "environment": {"app_env": "development", "is_production": False},
        }
        enrich_workbench_readiness(state)
        issues = {item["reason_code"]: item for item in state["readiness"]["issues"]}
        self.assertEqual(issues["profile_unavailable"]["section"], "configuration")
        self.assertEqual(issues["inventory_unverified"]["section"], "configuration")
        self.assertEqual(issues["capacity_degraded"]["section"], "maintenance")
        self.assertEqual(
            state["readiness"]["disabled_capabilities"][0]["reason_code"],
            "external_embeddings_disabled",
        )
        self.assertTrue(state["readiness"]["local_development"]["enabled"])

    def test_workbench_renders_local_posture_and_remediation_link(self):
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local development posture")
        self.assertContains(response, "Remote publication and production activation")

    def test_maintenance_deep_link_renders_selected_plan_and_job(self):
        folder = Folder.objects.create(name="Law", created_by=self.superadmin)
        PDFFile.objects.create(
            title="Governance",
            file=SimpleUploadedFile("governance.pdf", b"%PDF-1.4"),
            folder=folder,
            uploaded_by=self.superadmin,
            indexed=False,
            category="acts",
            subject="governance",
            keywords=["act"],
        )
        plan = create_plan(
            operation="reindex_needed",
            data={
                "folder_ids": [str(folder.pk)],
                "filter_subject": "governance",
            },
            actor=self.superadmin,
            idempotency_key="selected-plan-link",
        )
        job = MaintenanceJob.objects.create(
            kind="reindex_needed",
            status="running",
            total_items=1,
            completed_items=0,
            requested_by=self.superadmin,
            options={
                "recovery_set_id": "rs-test",
                "candidate_workspace_id": "ws-test",
                "candidate_state": "activation_ready",
            },
        )
        MaintenanceAuditEvent.objects.create(
            job=job,
            actor=self.superadmin,
            event_type="queued",
            payload={"plan_id": str(plan.public_id)},
        )
        plan.job = job
        plan.save(update_fields=["job"])

        response = self.client.get(
            f"{reverse('operations_panel')}?section=maintenance&plan={plan.public_id}&job={job.public_id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Affected folders")
        self.assertContains(response, "Focused job")
        self.assertContains(response, "Audit history")
        self.assertContains(response, str(job.public_id))
        self.assertContains(response, "Recovery set")
        self.assertContains(response, "Law")

    def test_create_superuser_assigns_supported_vault_role(self):
        user = CustomUser.objects.create_superuser(
            username="role-invariant",
            password="test-password",
            role="admin",
        )
        self.assertEqual(user.role, "superadmin")
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse("vaultops:state")).status_code, 200
        )

    @override_settings(
        VAULT_UI_PROFILE_CONFIGURATION_ENABLED=True,
        VAULT_CREDENTIAL_ALIASES={
            "production-readonly": {
                "access_key_env": "TEST_ACCESS_KEY",
                "secret_key_env": "TEST_SECRET_KEY",
            }
        },
    )
    @patch("vaultops.views.upsert_restore_profile")
    def test_profile_configure_accepts_alias_contract_not_secrets(self, upsert):
        upsert.return_value = SimpleNamespace(key="production-readonly")
        response = self.client.post(
            reverse("vaultops:profile_configure"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "key": "production-readonly",
                "display_name": "Production read only",
                "endpoint_origin": "https://vault.example",
                "bucket": "artifacts",
                "region": "test",
                "dataset_id": "ai-sahakar-prod",
                "production_source_id": "ai-sahakar-prod",
                "credential_alias": "production-readonly",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("secret_key", upsert.call_args.kwargs)
        self.assertEqual(
            upsert.call_args.kwargs["credential_alias"],
            "production-readonly",
        )

        rejected = self.client.post(
            reverse("vaultops:profile_configure"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "key": "production-readonly",
                "secret_key": "must-be-rejected",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            rejected.json()["reason_code"], "browser_secret_entry_rejected"
        )
        self.assertEqual(upsert.call_count, 1)

    @patch("vaultops.views.project_verified_generation")
    @patch("vaultops.views.verify_generation")
    @patch("vaultops.views.vault_for_profile")
    def test_profile_inventory_verifies_and_projects(
        self, vault_for_profile, verify_generation, project_generation
    ):
        verified = SimpleNamespace(
            generation_id="generation-authoritative",
            authoritative=True,
            file_count=564,
            byte_count=1234,
        )
        verify_generation.return_value = verified
        projection = SimpleNamespace(inventory_state="verified")
        generation = SimpleNamespace(
            generation_id="generation-authoritative"
        )
        project_generation.return_value = (projection, generation)
        response = self.client.post(
            reverse(
                "vaultops:profile_inventory",
                kwargs={"profile_key": self.profile.key},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "generation_id": "generation-authoritative",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["reason_code"], "profile_inventory_verified")
        self.assertEqual(payload["data"]["file_count"], 564)
        verify_generation.assert_called_once()
        project_generation.assert_called_once_with(self.profile, verified)

    def test_marathi_workbench_uses_reviewed_operations_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "mr"
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "तिजोरी संचालन कार्यपटल")
        self.assertContains(response, "अधिकृत स्थिती तुलना")
        self.assertNotContains(response, "Vault Operations Workbench")
        retention = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(retention, "पिढी निवृत्ती")
        self.assertContains(retention, "कचरा संकलन योजना")

    def test_state_api_is_redacted_and_uses_contract_envelope(self):
        lease = acquire_lease(
            self.profile.dataset_id,
            "sensitive-instance-name",
            ttl_seconds=30,
        )
        try:
            response = self.client.get(reverse("vaultops:state"))
        finally:
            release_lease(lease)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("status", payload)
        self.assertIn("reason_code", payload)
        self.assertIn("observed_at", payload)
        self.assertIn("correlation_id", payload)
        self.assertIn("state_version", payload)
        serialized = json.dumps(payload)
        self.assertNotIn(lease.owner_token, serialized)
        self.assertNotIn("sensitive-instance-name", serialized)
        self.assertEqual(payload["data"]["lease"]["state"], "held")

    def test_diagnostics_export_is_redacted_and_aggregated(self):
        object_key = "datasets/private/objects/pdf/" + "7" * 64
        self.candidate.manifest = {
            "files": [{"object_key": object_key, "bytes": 12}]
        }
        self.candidate.save(update_fields=["manifest", "updated_at"])

        response = self.client.get(reverse("vaultops:diagnostics"))
        payload = response.json()
        serialized = json.dumps(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["counts"]["generations"], 1)
        self.assertFalse(
            payload["data"]["retention"]["gc_execution_enabled"]
        )
        self.assertNotIn(object_key, serialized)
        self.assertNotIn("credential_alias", serialized)

    def test_state_version_is_stable_without_authority_changes(self):
        first = self.client.get(reverse("vaultops:state")).json()
        second = self.client.get(reverse("vaultops:state")).json()
        self.assertEqual(first["state_version"], second["state_version"])

    def test_fresh_rendered_state_version_passes_mutation_guard(self):
        page = self.client.get(
            reverse("operations_panel"), {"section": "sync"}
        )
        response = self.client.post(
            reverse("vaultops:sync_run"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": page.context["state"]["state_version"],
            },
        )
        self.assertEqual(response.status_code, 303)

    def test_typed_promotion_confirmation_is_one_use(self):
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "promote_generation",
                "target": self.candidate.generation_id,
            },
        )
        self.assertEqual(issue.status_code, 200)
        challenge = issue.context["challenge"]
        phrase = issue.context["phrase"]
        submit = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(submit.status_code, 303)
        job = VaultJob.objects.get(operation="promote_generation")
        self.assertTrue(
            job.progress["confirmation"].startswith("operator:")
        )
        audit = VaultAuditEvent.objects.get(
            action="job_queued", job_public_id=job.public_id
        )
        self.assertEqual(
            audit.confirmation_digest,
            job.progress["confirmation"].removeprefix("operator:"),
        )
        replay = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(replay.status_code, 303)
        self.assertEqual(
            VaultJob.objects.filter(
                operation="promote_generation"
            ).count(),
            1,
        )

    def test_invalid_activation_phrase_does_not_create_recovery_set(self):
        workspace = RestoreWorkspace.objects.create(
            generation=self.candidate,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=self.candidate.manifest_digest,
            prepared_at=timezone.now(),
        )
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "activate_workspace",
                "target": str(workspace.public_id),
            },
        )

        with patch("vaultops.views.create_recovery_set") as recovery:
            response = self.client.post(
                reverse(
                    "vaultops:schedule_activation",
                    kwargs={"workspace_id": workspace.public_id},
                ),
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "challenge_id": str(issue.context["challenge"].public_id),
                    "confirmation_phrase": "wrong phrase",
                },
            )

        self.assertEqual(response.status_code, 303)
        recovery.assert_not_called()
        issue.context["challenge"].refresh_from_db()
        self.assertIsNone(issue.context["challenge"].used_at)

    def test_activation_recovery_failure_leaves_confirmation_reusable(self):
        workspace = RestoreWorkspace.objects.create(
            generation=self.candidate,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=self.candidate.manifest_digest,
            prepared_at=timezone.now(),
        )
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "activate_workspace",
                "target": str(workspace.public_id),
            },
        )

        with patch(
            "vaultops.views.create_recovery_set",
            side_effect=RuntimeError("recovery unavailable"),
        ):
            response = self.client.post(
                reverse(
                    "vaultops:schedule_activation",
                    kwargs={"workspace_id": workspace.public_id},
                ),
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "challenge_id": str(issue.context["challenge"].public_id),
                    "confirmation_phrase": issue.context["phrase"],
                },
            )

        self.assertEqual(response.status_code, 303)
        issue.context["challenge"].refresh_from_db()
        self.assertIsNone(issue.context["challenge"].used_at)

    def test_exact_activation_post_replay_skips_recovery_work(self):
        workspace = RestoreWorkspace.objects.create(
            generation=self.candidate,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=self.candidate.manifest_digest,
            prepared_at=timezone.now(),
        )
        state_digest = workspace_state_digest(workspace)
        idempotency_key = str(uuid.uuid4())
        intent = ActivationIntent.objects.create(
            workspace=workspace,
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            target_generation_id=self.candidate.generation_id,
            previous_generation_id="previous",
            manifest_digest=workspace.manifest_digest,
            intent_digest="i" * 64,
            idempotency_key=idempotency_key,
            request_state_digest=state_digest,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        with patch("vaultops.views.create_recovery_set") as recovery:
            response = self.client.post(
                reverse(
                    "vaultops:schedule_activation",
                    kwargs={"workspace_id": workspace.public_id},
                ),
                {"idempotency_key": idempotency_key},
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(
            response.json()["data"]["activation_intent_id"],
            str(intent.public_id),
        )
        recovery.assert_not_called()

    def test_typed_retirement_is_reversible_and_never_claims_deletion(self):
        page = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(page, "Retirement removes promotion eligibility")
        self.assertContains(page, "Dry-run only")
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "retire_generation",
                "target": self.candidate.generation_id,
            },
        )
        retirement = self.client.post(
            reverse(
                "vaultops:retire_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(issue.context["challenge"].public_id),
                "confirmation_phrase": issue.context["phrase"],
            },
        )
        self.assertEqual(retirement.status_code, 303)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.vault_state, "retired")

        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "unretire_generation",
                "target": self.candidate.generation_id,
            },
        )
        unretirement = self.client.post(
            reverse(
                "vaultops:unretire_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(issue.context["challenge"].public_id),
                "confirmation_phrase": issue.context["phrase"],
            },
        )
        self.assertEqual(unretirement.status_code, 303)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.vault_state, "candidate")

    def test_retention_hold_create_and_release_use_fresh_state(self):
        state = self.client.get(reverse("vaultops:state")).json()
        idempotency_key = str(uuid.uuid4())
        created = self.client.post(
            reverse(
                "vaultops:retention_hold_create",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": idempotency_key,
                "state_version": state["state_version"],
                "reason_code": "incident",
                "owner_reference": "INC-42",
            },
        )
        self.assertEqual(created.status_code, 303)
        hold = RetentionHold.objects.get()
        self.assertIsNone(hold.released_at)

        replayed = self.client.post(
            reverse(
                "vaultops:retention_hold_create",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": idempotency_key,
                "state_version": state["state_version"],
                "reason_code": "different-replay-value",
                "owner_reference": "INC-99",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(replayed.status_code, 202, replayed.content)
        self.assertEqual(RetentionHold.objects.count(), 1)
        self.assertEqual(
            replayed.json()["data"]["hold_id"],
            hold.pk,
        )
        hold.refresh_from_db()
        self.assertEqual(hold.owner_reference, "INC-42")

        state = self.client.get(reverse("vaultops:state")).json()
        released = self.client.post(
            reverse(
                "vaultops:retention_hold_release",
                kwargs={"hold_id": hold.pk},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": state["state_version"],
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(released.status_code, 202, released.content)
        hold.refresh_from_db()
        self.assertIsNotNone(hold.released_at)

    def test_gc_execute_endpoint_is_disabled_even_for_ready_plan(self):
        plan = GarbageCollectionPlan.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            inventory_version="i" * 64,
            pointer_version="p" * 64,
            candidates=[
                {
                    "generation_id": "generation-retired",
                    "exclusive_object_keys": ["private/object/key"],
                }
            ],
            estimates={
                "deletion_enabled": False,
                "exclusive_objects": 1,
                "exclusive_bytes": 10,
            },
            plan_digest="g" * 64,
            state=GarbageCollectionPlan.State.READY,
            expires_at=timezone.now(),
        )
        listing = self.client.get(reverse("vaultops:gc_plans"))
        self.assertNotContains(listing, "private/object/key")
        page = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(page, "GC execution disabled")
        state = self.client.get(reverse("vaultops:state")).json()
        response = self.client.post(
            reverse(
                "vaultops:gc_plan_execute",
                kwargs={"plan_id": plan.public_id},
            ),
            data=json.dumps(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "state_version": state["state_version"],
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"], "gc_execution_disabled"
        )

    def test_confirmation_is_rejected_after_observed_state_changes(self):
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "promote_generation",
                "target": self.candidate.generation_id,
            },
        )
        challenge = issue.context["challenge"]
        phrase = issue.context["phrase"]
        self.candidate.local_presence = (
            ArtifactGeneration.LocalPresence.QUARANTINED
        )
        self.candidate.save(update_fields=["local_presence", "updated_at"])
        response = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertFalse(
            VaultJob.objects.filter(
                operation="promote_generation"
            ).exists()
        )

    def test_legacy_generation_actions_do_not_relabel_or_delete(self):
        generation = LegacyGeneration.objects.create(
            generation_id="legacy-generation",
            status="validated",
        )
        for route in (
            "promote_generation",
            "rollback_generation",
            "purge_generation",
        ):
            response = self.client.post(
                reverse(route, kwargs={"generation_id": generation.generation_id})
            )
            self.assertEqual(response.status_code, 302)
            generation.refresh_from_db()
            self.assertEqual(generation.status, "validated")
        response = self.client.post(reverse("purge_expired_generations"))
        self.assertEqual(response.status_code, 302)
        generation.refresh_from_db()
        self.assertEqual(generation.status, "validated")

    def test_legacy_vault_page_redirects_to_maintenance(self):
        response = self.client.get(reverse("vault_operations"))
        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=maintenance",
            fetch_redirect_response=False,
        )

    def test_operations_navigation_marks_active_section(self):
        response = self.client.get(
            reverse("operations_panel"), {"section": "maintenance"}
        )
        nav = response.content.decode("utf-8")
        self.assertIn(
            'href="{}" aria-current="page"'.format(reverse("operations_panel")),
            nav,
        )
        self.assertNotIn(
            'href="{}?section=generations" aria-current="page"'.format(
                reverse("operations_panel")
            ),
            nav,
        )

        response = self.client.get(
            f"{reverse('operations_panel')}?section=generations"
        )
        nav = response.content.decode("utf-8")
        self.assertIn(
            'href="{}?section=generations" aria-current="page"'.format(
                reverse("operations_panel")
            ),
            nav,
        )

    def test_profile_scope_is_preserved_in_live_state_url(self):
        response = self.client.get(
            reverse("operations_panel"),
            {"section": "overview", "profile": self.profile.key},
        )
        self.assertContains(
            response,
            '{}?profile={}'.format(
                reverse("vaultops:state"),
                self.profile.key,
            ),
        )

    def test_mutation_rejects_missing_idempotency_key(self):
        response = self.client.post(
            reverse("vaultops:restore_start"),
            {
                "profile_key": self.profile.key,
                "state_version": "not-current",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertFalse(
            VaultJob.objects.filter(
                operation="restore_generation"
            ).exists()
        )

    def test_json_mutation_error_uses_contract_envelope(self):
        response = self.client.post(
            reverse("vaultops:restore_start"),
            data=json.dumps({"profile_key": self.profile.key}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(
            payload["reason_code"], "idempotency_key_required"
        )
        self.assertIn("correlation_id", payload)

    @override_settings(VAULT_ADMIN_MUTATIONS_ENABLED=False)
    def test_server_rejects_post_when_admin_mutations_are_disabled(self):
        response = self.client.post(
            reverse("vaultops:confirmation_issue"),
            data=json.dumps(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "action": "promote_generation",
                    "target": self.candidate.generation_id,
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"],
            "vault_admin_mutations_disabled",
        )
        self.assertFalse(ConfirmationChallenge.objects.exists())

    def test_mutation_rate_limit_is_actor_and_route_scoped(self):
        url = reverse("vaultops:confirmation_issue")
        for attempt in range(30):
            response = self.client.post(
                url,
                {
                    "idempotency_key": f"attempt-{attempt:03d}",
                    "action": "unsupported",
                    "target": "target",
                },
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 400)
        blocked = self.client.post(
            url,
            {
                "idempotency_key": "attempt-030",
                "action": "unsupported",
                "target": "target",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["reason_code"], "rate_limited")
