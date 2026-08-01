import os
import uuid
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.maintenance import queue_job, run_job
from core.candidate_maintenance import (
    CandidateMaintenanceError,
    _copy_tree,
    maintenance_source_capability_reason,
)
from core.management.commands.run_maintenance_jobs import (
    _execute_local_job,
    _fail_unhandled_local_job,
    _requires_source_mutation_scope,
)
from core.maintenance_plans import (
    FORCE_CONFIRMATION,
    MaintenancePlanError,
    MAX_SELECTION_IDS,
    capability_reasons,
    calculate_preview,
    create_plan,
    normalize_selection,
    queue_plan,
)
from core.models import CustomUser, Folder, MaintenanceJob, MaintenancePlan, PDFFile


LOCAL_GATES = override_settings(
    LOCAL_INDEX_MAINTENANCE_ENABLED=True,
    FORCE_REINDEX_ENABLED=True,
    EXTERNAL_EMBEDDINGS_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    MAINTENANCE_WORKER_READINESS_REQUIRED=False,
    ACTIVE_RUNTIME=None,
    RUNTIME_GENERATION_ID="",
    RUNTIME_MANIFEST_DIGEST="",
)


@override_settings(
    LOCAL_INDEX_MAINTENANCE_ENABLED=True,
    FORCE_REINDEX_ENABLED=True,
    EXTERNAL_EMBEDDINGS_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    MAINTENANCE_WORKER_READINESS_REQUIRED=False,
    ACTIVE_RUNTIME=object(),
    MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=False,
)
class ActiveRuntimeMaintenanceConfigurationTests(TestCase):
    databases = {"default", "control"}

    def test_active_runtime_without_candidate_preparation_fails_closed(self):
        actor = CustomUser.objects.create_user(
            username="config-root",
            password="password",
            role="superadmin",
            is_superuser=True,
            is_staff=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve() / "maintenance-workspaces"
            with override_settings(MAINTENANCE_WORKSPACE_ROOT=workspace):
                reasons = capability_reasons()
                for operation in (
                    "repair_indexes",
                    "reindex_needed",
                    "reindex_selected",
                ):
                    self.assertEqual(
                        reasons[operation],
                        "maintenance_candidate_preparation_disabled",
                    )
                with self.assertRaisesRegex(
                    MaintenancePlanError,
                    "^maintenance_candidate_preparation_disabled$",
                ):
                    create_plan(
                        operation="reindex_needed",
                        data={},
                        actor=actor,
                        idempotency_key="active-runtime-disabled",
                    )
                self.assertFalse(workspace.exists())

        self.assertFalse(MaintenancePlan.objects.exists())
        self.assertFalse(MaintenanceJob.objects.exists())


@LOCAL_GATES
class MaintenancePlanningTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        source_capability = patch(
            "core.maintenance_plans.maintenance_source_capability_reason",
            return_value="",
        )
        source_capability.start()
        self.addCleanup(source_capability.stop)
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

    def test_idempotency_key_is_operation_scoped(self):
        key = f"test:{uuid.uuid4()}"
        validate_plan = self._plan("validate", key=key)
        repair_plan = self._plan("repair_indexes", key=key)
        self.assertNotEqual(validate_plan.pk, repair_plan.pk)
        self.assertEqual(validate_plan.operation, "validate")
        self.assertEqual(repair_plan.operation, "repair_indexes")

    def test_idempotency_key_rejects_changed_selection(self):
        key = f"test:{uuid.uuid4()}"
        original = self._plan("validate", key=key)

        with self.assertRaisesRegex(MaintenancePlanError, "idempotency_conflict"):
            create_plan(
                operation="validate",
                data={
                    "folder_ids": [str(self.folder.pk)],
                    "filter_subject": "different-subject",
                    "filter_keywords": "cooperative",
                },
                actor=self.superadmin,
                idempotency_key=key,
            )

        original.refresh_from_db()
        self.assertEqual(original.selection["filters"]["subject"], "housing")
        self.assertEqual(MaintenancePlan.objects.count(), 1)

    def test_capability_matrix_applies_independent_prerequisites(self):
        cases = (
            # local, force, embeddings, expected-needed, expected-selected
            (True, True, True, "", ""),
            (False, True, True, "bulk_reindex_disabled", "bulk_reindex_disabled"),
            (True, False, True, "", "bulk_reindex_disabled"),
            (True, True, False, "external_embeddings_disabled", "external_embeddings_disabled"),
            (True, False, False, "external_embeddings_disabled", "bulk_reindex_disabled"),
        )
        for local, force, embeddings, needed, selected in cases:
            with self.subTest(local=local, force=force, embeddings=embeddings):
                with override_settings(
                    LOCAL_INDEX_MAINTENANCE_ENABLED=local,
                    FORCE_REINDEX_ENABLED=force,
                    EXTERNAL_EMBEDDINGS_ENABLED=embeddings,
                    ACTIVE_RUNTIME=None,
                ):
                    reasons = capability_reasons()
                self.assertEqual(reasons["validate"], reasons["repair_indexes"])
                self.assertEqual(reasons["reindex_needed"], needed)
                self.assertEqual(reasons["reindex_selected"], selected)

    def test_queue_rechecks_worker_availability_after_preview(self):
        plan = self._plan(operation="validate")
        with tempfile.TemporaryDirectory() as temporary:
            with override_settings(
                MAINTENANCE_WORKER_READINESS_REQUIRED=True,
                MAINTENANCE_WORKER_HEARTBEAT_PATH=(
                    f"{temporary}/missing-worker.heartbeat"
                ),
            ):
                with self.assertRaisesRegex(
                    MaintenancePlanError,
                    "^maintenance_worker_unavailable$",
                ):
                    queue_plan(plan=plan, actor=self.superadmin)

        plan.refresh_from_db()
        self.assertEqual(plan.state, "previewed")
        self.assertIsNone(plan.job_id)

    @override_settings(VAULT_MUTATION_TRACKING_ENABLED=False)
    def test_candidate_operations_require_mutation_tracking(self):
        reasons = capability_reasons()
        self.assertEqual(reasons["validate"], "")
        for operation in (
            "repair_indexes",
            "reindex_needed",
            "reindex_selected",
        ):
            self.assertEqual(
                reasons[operation],
                "mutation_tracking_disabled",
            )

    def test_candidate_operations_require_verified_source_authority(self):
        with patch(
            "core.maintenance_plans.maintenance_source_capability_reason",
            return_value="maintenance_source_pointer_unverified",
        ):
            reasons = capability_reasons()

        self.assertEqual(reasons["validate"], "")
        for operation in (
            "repair_indexes",
            "reindex_needed",
            "reindex_selected",
        ):
            self.assertEqual(
                reasons[operation],
                "maintenance_source_pointer_unverified",
            )

    def test_queue_rechecks_source_authority_before_recovery_or_job(self):
        plan = self._plan(operation="repair_indexes")
        with (
            patch(
                "core.maintenance_plans.maintenance_source_capability_reason",
                return_value="maintenance_source_pointer_unverified",
            ),
            patch("core.maintenance_plans.create_set") as create_recovery_set,
        ):
            with self.assertRaisesRegex(
                MaintenancePlanError,
                "^maintenance_source_pointer_unverified$",
            ):
                queue_plan(plan=plan, actor=self.superadmin)

        plan.refresh_from_db()
        self.assertEqual(plan.state, "previewed")
        self.assertIsNone(plan.job_id)
        self.assertFalse(MaintenanceJob.objects.exists())
        create_recovery_set.assert_not_called()

    def test_source_capability_preserves_typed_candidate_reason(self):
        with patch(
            "core.candidate_maintenance._maintenance_source_parent",
            side_effect=CandidateMaintenanceError(
                "maintenance_source_observation_stale"
            ),
        ):
            self.assertEqual(
                maintenance_source_capability_reason(),
                "maintenance_source_observation_stale",
            )

    def test_source_capability_fails_closed_on_unexpected_error(self):
        with patch(
            "core.candidate_maintenance._maintenance_source_parent",
            side_effect=RuntimeError("unsafe internal detail"),
        ):
            self.assertEqual(
                maintenance_source_capability_reason(),
                "maintenance_source_pointer_unverified",
            )

    def test_source_capability_rejects_unwritable_workspace_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            lexical_parent = Path(temporary).resolve()
            workspace = lexical_parent / "maintenance-workspaces"
            os.chmod(lexical_parent, 0o555)
            try:
                with (
                    patch(
                        "core.candidate_maintenance._maintenance_source_parent",
                        return_value=object(),
                    ),
                    override_settings(MAINTENANCE_WORKSPACE_ROOT=workspace),
                ):
                    self.assertEqual(
                        maintenance_source_capability_reason(),
                        "maintenance_workspace_unwritable",
                    )
            finally:
                os.chmod(lexical_parent, 0o700)

    def test_source_capability_rejects_final_workspace_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            lexical_parent = Path(temporary).resolve()
            target = lexical_parent / "target"
            workspace = lexical_parent / "maintenance-workspaces"
            os.mkdir(target)
            os.symlink(target, workspace)
            with (
                patch(
                    "core.candidate_maintenance._maintenance_source_parent",
                    return_value=object(),
                ),
                override_settings(MAINTENANCE_WORKSPACE_ROOT=workspace),
            ):
                self.assertEqual(
                    maintenance_source_capability_reason(),
                    "maintenance_workspace_unsafe",
                )

    def test_source_capability_rejects_intermediate_workspace_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            lexical_parent = Path(temporary).resolve()
            target = lexical_parent / "target"
            linked_parent = lexical_parent / "linked-parent"
            os.mkdir(target)
            os.symlink(target, linked_parent)
            with (
                patch(
                    "core.candidate_maintenance._maintenance_source_parent",
                    return_value=object(),
                ),
                override_settings(
                    MAINTENANCE_WORKSPACE_ROOT=linked_parent / "workspaces"
                ),
            ):
                self.assertEqual(
                    maintenance_source_capability_reason(),
                    "maintenance_workspace_unsafe",
                )

    def test_tree_copy_preserves_and_rejects_racing_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = f"{temporary}/source"
            target = f"{temporary}/target"
            outside = f"{temporary}/outside"
            os.mkdir(source)
            os.mkdir(outside)

            def racing_copytree(_source, destination, *, symlinks):
                self.assertTrue(symlinks)
                os.mkdir(destination)
                os.symlink(outside, f"{destination}/raced-link")

            with patch(
                "core.candidate_maintenance.shutil.copytree",
                side_effect=racing_copytree,
            ):
                with self.assertRaisesRegex(
                    CandidateMaintenanceError,
                    "maintenance_source_runtime_unsafe",
                ):
                    _copy_tree(Path(source), Path(target))

    def test_normalize_selection_rejects_inverted_date_range(self):
        with self.assertRaisesRegex(MaintenancePlanError, "malformed_filters"):
            normalize_selection(
                {
                    "folder_ids": [str(self.folder.pk)],
                    "filter_uploaded_after": "2026-02-01",
                    "filter_uploaded_before": "2026-01-31",
                }
            )

    def test_normalize_selection_rejects_malformed_date(self):
        with self.assertRaisesRegex(MaintenancePlanError, "malformed_filters"):
            normalize_selection(
                {
                    "folder_ids": [str(self.folder.pk)],
                    "filter_uploaded_after": "31-01-2026",
                }
            )

    def test_normalize_selection_rejects_empty_or_non_numeric_scope(self):
        with self.assertRaisesRegex(MaintenancePlanError, "empty_scope"):
            normalize_selection({"folder_ids": ["not-a-number"]})

    def test_normalize_selection_accepts_equal_date_boundary(self):
        selection = normalize_selection(
            {
                "folder_ids": [str(self.folder.pk), str(self.folder.pk)],
                "pdf_ids": ["not-a-number", str(self.pdf.pk), str(self.pdf.pk)],
                "filter_uploaded_after": "2026-01-31",
                "filter_uploaded_before": "2026-01-31",
            }
        )
        self.assertEqual(selection["folder_ids"], [self.folder.pk])
        self.assertEqual(selection["pdf_ids"], [self.pdf.pk])

    def test_normalize_selection_rejects_oversized_id_lists(self):
        with self.assertRaisesRegex(MaintenancePlanError, "selection_too_large"):
            normalize_selection(
                {
                    "folder_ids": [str(index) for index in range(MAX_SELECTION_IDS + 1)],
                }
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

    def test_reindex_needed_includes_indexed_pdf_with_invalid_artifacts(self):
        self.pdf.indexed = True
        self.pdf.lifecycle = "ready"
        self.pdf.page_chunks = ["first", "second"]
        self.pdf.chunk_embeddings = [[0.1, 0.2]]
        self.pdf.save(
            update_fields=[
                "indexed", "lifecycle", "page_chunks", "chunk_embeddings"
            ]
        )

        preview = calculate_preview(
            "reindex_needed",
            normalize_selection({"folder_ids": [str(self.folder.pk)]}),
        )

        self.assertEqual(preview["pdf_ids"], [self.pdf.pk])
        self.assertEqual(
            preview["artifact_reason_counts"]["chunk_embedding_count_mismatch"],
            1,
        )

    def test_reindex_needed_keeps_valid_stored_artifacts_repair_only(self):
        self.pdf.indexed = False
        self.pdf.lifecycle = "ready"
        self.pdf.page_chunks = ["first"]
        self.pdf.chunk_embeddings = [[0.1, 0.2]]
        self.pdf.save(
            update_fields=[
                "indexed", "lifecycle", "page_chunks", "chunk_embeddings"
            ]
        )

        preview = calculate_preview(
            "reindex_needed",
            normalize_selection({"folder_ids": [str(self.folder.pk)]}),
        )

        self.assertEqual(preview["pdf_ids"], [])
        self.assertEqual(preview["repair_only_count"], 1)
        self.assertEqual(
            preview["artifact_reason_counts"]["stored_index_repair_required"],
            1,
        )

    def test_plan_stales_when_chunk_content_changes_at_same_count(self):
        self.pdf.indexed = True
        self.pdf.lifecycle = "ready"
        self.pdf.page_chunks = ["original"]
        self.pdf.chunk_embeddings = [[0.1, 0.2]]
        self.pdf.save(
            update_fields=[
                "indexed", "lifecycle", "page_chunks", "chunk_embeddings"
            ]
        )
        plan = self._plan("validate")
        PDFFile.objects.filter(pk=self.pdf.pk).update(page_chunks=["changed"])

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
            "Documents & Search",
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

    def test_workbench_explains_unverified_source_before_mutation_preview(self):
        self.client.force_login(self.superadmin)
        with patch(
            "core.maintenance_plans.maintenance_source_capability_reason",
            return_value="maintenance_source_pointer_unverified",
        ):
            response = self.client.get(
                f"{reverse('operations_panel')}?section=maintenance"
            )

        self.assertContains(response, "Active search source is not verified")
        self.assertContains(
            response,
            "Repair and reindex work cannot prepare a safe candidate yet.",
        )
        self.assertContains(
            response,
            'name="operation" value="validate"',
            html=False,
        )
        self.assertNotContains(
            response,
            'name="operation" value="validate" disabled',
            html=False,
        )
        for operation in (
            "repair_indexes",
            "reindex_needed",
            "reindex_selected",
        ):
            self.assertContains(
                response,
                f'name="operation" value="{operation}" disabled',
                html=False,
            )

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
            if job["public_id"] == str(running.public_id)
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
            if job["public_id"] == str(failed.public_id)
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
        self.assertEqual(
            finished.options["folder_build_attempts"],
            {str(self.folder.pk): 1},
        )

        finished.status = "queued"
        finished.failed_items = 0
        finished.finished_at = None
        finished.save()
        run_job(finished)
        self.assertEqual(precompute.call_count, 2)
        repair.assert_called_once()
        finished.refresh_from_db()
        self.assertEqual(
            finished.options["folder_build_attempts"],
            {str(self.folder.pk): 1},
        )

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
        self.assertFalse(_requires_source_mutation_scope(job))

    def test_unhandled_job_error_is_persisted_without_raw_exception(self):
        job = queue_job(
            kind="validate",
            requested_by=self.user,
            pdfs=self.pdfs,
        )
        job.status = "running"
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])

        finished = _fail_unhandled_local_job(
            job,
            RuntimeError("sensitive provider output"),
        )

        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.failed_items, finished.total_items)
        self.assertEqual(finished.error_summary, "maintenance_job_failed")
        self.assertNotIn("sensitive", finished.error_summary)
        event = finished.audit_events.get(event_type="failed")
        self.assertEqual(
            event.payload["reason_code"],
            "maintenance_job_failed",
        )

    @override_settings(MAINTENANCE_CANDIDATE_EXECUTION=True)
    def test_candidate_child_execution_tracks_workspace_mutations(self):
        job = MaintenanceJob(
            options={"candidate_required": True}
        )
        self.assertTrue(_requires_source_mutation_scope(job))


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
