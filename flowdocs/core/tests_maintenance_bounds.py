import uuid

from django.test import TestCase, override_settings

from .maintenance_plans import (
    MAX_PREVIEW_PDFS,
    MaintenancePlanError,
    calculate_preview,
    create_plan,
    normalize_selection,
    _selection_query,
    _source_digest,
)
from .models import CustomUser, Folder, PDFFile


@override_settings(
    LOCAL_INDEX_MAINTENANCE_ENABLED=True,
    FORCE_REINDEX_ENABLED=True,
    EXTERNAL_EMBEDDINGS_ENABLED=True,
    ACTIVE_RUNTIME=None,
    RUNTIME_GENERATION_ID="",
    RUNTIME_MANIFEST_DIGEST="",
)
class MaintenancePreviewBoundsTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.actor = CustomUser.objects.create_user(
            username="bounds-admin", password="password", role="superadmin"
        )
        self.folder = Folder.objects.create(name="Bounded", created_by=self.actor)

    def test_pdf_scoped_preview_rejects_oversized_match_before_materialization(self):
        PDFFile.objects.bulk_create(
            [
                PDFFile(
                    title=f"Document {index}",
                    file="pdfs/bounded.pdf",
                    folder=self.folder,
                    uploaded_by=self.actor,
                    indexed=False,
                )
                for index in range(MAX_PREVIEW_PDFS + 1)
            ],
            batch_size=500,
        )
        selection = normalize_selection({"folder_ids": [str(self.folder.pk)]})
        with self.assertRaisesRegex(MaintenancePlanError, "selection_too_large"):
            calculate_preview("reindex_needed", selection)

    def test_repair_preview_is_bounded_and_folder_scoped(self):
        PDFFile.objects.bulk_create(
            [
                PDFFile(
                    title=f"Document {index}",
                    file="pdfs/bounded.pdf",
                    folder=self.folder,
                    uploaded_by=self.actor,
                    indexed=False,
                )
                for index in range(MAX_PREVIEW_PDFS + 1)
            ],
            batch_size=500,
        )
        selection = normalize_selection({"folder_ids": [str(self.folder.pk)]})
        preview = calculate_preview("repair_indexes", selection)
        self.assertEqual(preview["pdf_count"], MAX_PREVIEW_PDFS + 1)
        self.assertEqual(preview["pdf_ids"], [])
        self.assertEqual(preview["folder_ids"], [self.folder.pk])

    def test_source_digest_changes_when_index_state_changes(self):
        pdf = PDFFile.objects.create(
            title="Stateful",
            file="pdfs/stateful.pdf",
            folder=self.folder,
            uploaded_by=self.actor,
            indexed=False,
        )
        selection = normalize_selection({"folder_ids": [str(self.folder.pk)]})
        query = _selection_query(selection)
        before = _source_digest(query)
        PDFFile.objects.filter(pk=pdf.pk).update(indexed=True)
        after = _source_digest(_selection_query(selection))
        self.assertNotEqual(before, after)

    def test_plan_preview_does_not_duplicate_large_id_payload(self):
        pdf = PDFFile.objects.create(
            title="Single",
            file="pdfs/single.pdf",
            folder=self.folder,
            uploaded_by=self.actor,
            indexed=False,
        )
        plan = create_plan(
            operation="reindex_needed",
            data={"folder_ids": [str(self.folder.pk)]},
            actor=self.actor,
            idempotency_key=f"bounds:{uuid.uuid4()}",
        )
        self.assertEqual(plan.preview["pdf_ids"], [pdf.pk])
        self.assertLessEqual(len(plan.preview["pdf_ids"]), MAX_PREVIEW_PDFS)
