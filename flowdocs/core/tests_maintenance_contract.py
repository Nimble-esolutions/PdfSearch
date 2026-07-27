import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.maintenance import queue_job, run_job
from core.management.commands.run_maintenance_jobs import _execute_local_job
from core.maintenance_plans import (
    FORCE_CONFIRMATION,
    MaintenancePlanError,
    create_plan,
    queue_plan,
)
from core.models import CustomUser, Folder, MaintenanceJob, MaintenancePlan, PDFFile


LOCAL_GATES = override_settings(
    LOCAL_INDEX_MAINTENANCE_ENABLED=True,
    FORCE_REINDEX_ENABLED=True,
    EXTERNAL_EMBEDDINGS_ENABLED=True,
    ACTIVE_RUNTIME=None,
    RUNTIME_GENERATION_ID="",
    RUNTIME_MANIFEST_DIGEST="",
)


@LOCAL_GATES
class MaintenancePlanningTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username="root",
            password="password",
            role="superadmin",
            is_superuser=True,
            is_staff=True,
        )
        self.admin = CustomUser.objects.create_user(
            username="admin", password="password", role="admin"
        )
        self.folder = Folder.objects.create(
            name="Housing", created_by=self.superadmin
        )
        self.pdf = PDFFile.objects.create(
            title="Act",
            file=SimpleUploadedFile("act.pdf", b"%PDF-1.4"),
            folder=self.folder,
            uploaded_by=self.superadmin,
            category="acts",
            subject="housing",
            keywords=["cooperative"],
            indexed=False,
        )

    def _plan(self, operation="reindex_needed", key=None):
        return create_plan(
            operation=operation,
            data={
                "folder_ids": [str(self.folder.pk)],
                "filter_subject": "housing",
                "filter_keywords": "cooperative",
            },
            actor=self.superadmin,
            idempotency_key=key or f"test:{uuid.uuid4()}",
        )

    def test_preview_is_server_calculated_and_idempotent(self):
        key = f"test:{uuid.uuid4()}"
        first = self._plan(key=key)
        second = self._plan(key=key)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.preview["pdf_ids"], [self.pdf.pk])
        self.assertEqual(first.preview["folder_count"], 1)
        self.assertTrue(first.external_embeddings_required)
        self.assertLessEqual(
            first.expires_at, first.created_at + timedelta(minutes=15, seconds=1)
        )

    def test_stale_selection_is_rejected_before_queue(self):
        plan = self._plan()
        PDFFile.objects.create(
            title="New",
            file=SimpleUploadedFile("new.pdf", b"%PDF-1.4"),
            folder=self.folder,
            uploaded_by=self.superadmin,
            subject="housing",
            keywords=["cooperative"],
            indexed=False,
        )
        with self.assertRaisesRegex(MaintenancePlanError, "stale_plan"):
            queue_plan(plan=plan, actor=self.superadmin)
        plan.refresh_from_db()
        self.assertEqual(plan.state, "rejected")

    def test_force_reindex_requires_single_use_typed_confirmation(self):
        plan = self._plan("reindex_selected")
        with self.assertRaisesRegex(
            MaintenancePlanError, "typed_confirmation_required"
        ):
            queue_plan(plan=plan, actor=self.superadmin, confirmation="wrong")
        with patch(
            "core.maintenance_plans.create_set",
            return_value={"set_id": "rs-test"},
        ):
            job = queue_plan(
                plan=plan,
                actor=self.superadmin,
                confirmation=FORCE_CONFIRMATION,
            )
            duplicate = queue_plan(
                plan=plan,
                actor=self.superadmin,
                confirmation=FORCE_CONFIRMATION,
            )
        self.assertEqual(job.pk, duplicate.pk)
        self.assertEqual(job.kind, "reindex_selected")
        self.assertEqual(job.options["recovery_set_id"], "rs-test")

    def test_expired_plan_is_rejected(self):
        plan = self._plan()
        MaintenancePlan.objects.filter(pk=plan.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        plan.refresh_from_db()
        with self.assertRaisesRegex(MaintenancePlanError, "plan_expired"):
            queue_plan(plan=plan, actor=self.superadmin)

    def test_non_superadmin_cannot_preview_or_queue(self):
        self.client.force_login(self.admin)
        preview = self.client.post(
            reverse("vaultops:maintenance_plan_create"),
            {
                "operation": "validate",
                "folder_ids": [self.folder.pk],
                "idempotency_key": f"test:{uuid.uuid4()}",
            },
        )
        self.assertIn(preview.status_code, {302, 403})
        self.assertEqual(MaintenancePlan.objects.count(), 0)

        plan = self._plan("validate")
        queued = self.client.post(
            reverse(
                "vaultops:maintenance_plan_queue",
                kwargs={"plan_id": plan.public_id},
            ),
            {"state_version": plan.state_version},
        )
        self.assertIn(queued.status_code, {302, 403})
        self.assertFalse(MaintenanceJob.objects.exists())

    def test_workbench_renders_full_capability_contract_and_disabled_reasons(self):
        self.client.force_login(self.superadmin)
        response = self.client.get(
            f"{reverse('operations_panel')}?section=maintenance"
        )
        self.assertEqual(response.status_code, 200)
        for text in (
            "Documents & Indexes",
            "Preview Validate Files",
            "Preview Repair Stored Indexes",
            "Preview Reindex Needed",
            "Preview Reindex Selected",
            "Indexed state",
            "Category",
            "Subject",
            "Keywords",
            "Uploaded after",
            "Uploaded before",
            "Local maintenance jobs",
        ):
            self.assertContains(response, text)

        with override_settings(LOCAL_INDEX_MAINTENANCE_ENABLED=False):
            disabled = self.client.get(
                f"{reverse('operations_panel')}?section=maintenance"
            )
            self.assertContains(disabled, "bulk_reindex_disabled")
            self.assertContains(disabled, "disabled")

    def test_workbench_renders_guided_authority_scope_and_preview_contract(self):
        self.client.force_login(self.superadmin)
        plan = self._plan("validate")
        response = self.client.get(
            reverse("operations_panel"),
            {"section": "maintenance", "plan": plan.public_id},
        )
        self.assertContains(response, "Choose the outcome you need")
        self.assertContains(response, "Local maintenance")
        self.assertContains(response, "Unchanged during processing")
        self.assertContains(response, "Unchanged until explicit publication")
        self.assertContains(response, "Document filters")
        self.assertContains(response, "Selected preview")
        self.assertContains(response, str(plan.public_id))
        self.assertEqual(
            response.context["state"]["maintenance"]["selected_plan"]["public_id"],
            plan.public_id,
        )
        self.assertTrue(response.context["state"]["maintenance_state_version"])
        self.assertTrue(response.context["state"]["combined_state_version"])

    def test_vault_workbench_owns_local_job_cancel_and_retry_actions(self):
        self.client.force_login(self.superadmin)
        running = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            requested_by=self.superadmin,
        )
        response = self.client.get(
            reverse("operations_panel"), {"section": "maintenance"}
        )
        job_state = next(
            job for job in response.context["state"]["maintenance"]["jobs"]
            if job["public_id"] == running.public_id
        )
        cancelled = self.client.post(
            reverse(
                "vaultops:maintenance_job_cancel",
                kwargs={"job_id": running.public_id},
            ),
            {"state_version": job_state["state_version"]},
        )
        self.assertEqual(cancelled.status_code, 303)
        self.assertEqual(
            cancelled["Location"],
            f"{reverse('operations_panel')}?section=maintenance",
        )
        running.refresh_from_db()
        self.assertEqual(running.status, "cancel_requested")

        failed = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="failed",
            failed_items=1,
            error_summary="typed_failure",
            requested_by=self.superadmin,
        )
        response = self.client.get(
            reverse("operations_panel"), {"section": "maintenance"}
        )
        job_state = next(
            job for job in response.context["state"]["maintenance"]["jobs"]
            if job["public_id"] == failed.public_id
        )
        retried = self.client.post(
            reverse(
                "vaultops:maintenance_job_retry",
                kwargs={"job_id": failed.public_id},
            ),
            {"state_version": job_state["state_version"]},
        )
        self.assertEqual(retried.status_code, 303)
        failed.refresh_from_db()
        self.assertEqual(failed.status, "queued")
        self.assertEqual(failed.failed_items, 0)

    def test_legacy_generation_operations_are_rejected(self):
        self.client.force_login(self.superadmin)
        for operation in ("sync_generation", "restore_generation"):
            response = self.client.post(
                reverse("bulk_maintenance"), {"operation": operation},
                follow=True,
            )
            self.assertContains(response, "operation_replaced")
        self.assertFalse(
            MaintenanceJob.objects.filter(
                kind__in=("sync_generation", "restore_generation")
            ).exists()
        )


@LOCAL_GATES
class MaintenanceWorkerGroupingTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="root",
            password="password",
            role="superadmin",
            is_superuser=True,
        )
        self.folder = Folder.objects.create(name="Audit", created_by=self.user)
        self.pdfs = [
            PDFFile.objects.create(
                title=f"PDF {index}",
                file=SimpleUploadedFile(f"{index}.pdf", b"%PDF-1.4"),
                folder=self.folder,
                uploaded_by=self.user,
            )
            for index in range(2)
        ]

    @patch("core.maintenance._repair_folder")
    @patch("core.maintenance.precompute_pdf_embeddings")
    def test_two_pdfs_build_one_final_folder_index(self, precompute, repair):
        job = queue_job(
            kind="reindex_selected",
            requested_by=self.user,
            pdfs=self.pdfs,
        )
        finished = run_job(job)
        self.assertEqual(finished.status, "completed")
        self.assertEqual(precompute.call_count, 2)
        for call in precompute.call_args_list:
            self.assertFalse(call.kwargs["rebuild_index"])
        repair.assert_called_once()
        self.assertEqual(
            finished.options["completed_folder_ids"], [self.folder.pk]
        )

        finished.status = "queued"
        finished.failed_items = 0
        finished.finished_at = None
        finished.save()
        run_job(finished)
        self.assertEqual(precompute.call_count, 2)
        repair.assert_called_once()

    @patch(
        "core.maintenance.precompute_pdf_embeddings",
        side_effect=RuntimeError("embedding unavailable"),
    )
    def test_item_failure_restores_document_lifecycle(self, precompute):
        job = queue_job(
            kind="reindex_selected",
            requested_by=self.user,
            pdfs=[self.pdfs[0]],
        )
        finished = run_job(job)
        self.pdfs[0].refresh_from_db()
        self.assertEqual(finished.status, "failed")
        self.assertEqual(self.pdfs[0].lifecycle, "uploaded")

    @patch("core.candidate_maintenance.execute_candidate_job")
    def test_candidate_jobs_route_to_isolated_executor(self, execute):
        job = queue_job(
            kind="reindex_selected",
            requested_by=self.user,
            pdfs=[self.pdfs[0]],
            options={
                "candidate_required": True,
                "recovery_set_id": "rs-test",
            },
        )
        execute.return_value = job
        self.assertIs(_execute_local_job(job), job)
        execute.assert_called_once_with(job)


class MaintenanceDeploymentPreflightTests(TestCase):
    def test_queued_obsolete_jobs_are_cancelled_but_history_is_preserved(self):
        queued = MaintenanceJob.objects.create(
            kind="sync_generation", status="queued"
        )
        completed = MaintenanceJob.objects.create(
            kind="restore_generation", status="completed"
        )
        call_command("maintenance_preflight")
        queued.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(queued.status, "cancelled")
        self.assertEqual(queued.error_summary, "operation_replaced")
        self.assertEqual(completed.status, "completed")

    def test_running_obsolete_job_blocks_preflight(self):
        MaintenanceJob.objects.create(
            kind="restore_generation", status="running"
        )
        with self.assertRaisesRegex(
            CommandError, "obsolete_generation_job_running"
        ):
            call_command("maintenance_preflight")
