from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core.models import Folder, PDFFile
from core.views import folder_cockpit_context, normalize_document_filters


class DocumentFilterNormalizationTests(SimpleTestCase):
    def test_unknown_values_fall_back_to_safe_defaults(self):
        filters = normalize_document_filters(
            {
                "document_query": "  circular  ",
                "document_state": "not-a-state",
                "document_owner": "1 OR 1=1",
                "document_sort": "random",
            }
        )

        self.assertEqual(
            filters,
            {"query": "circular", "state": "", "owner": "", "sort": "newest"},
        )

    def test_supported_values_are_preserved(self):
        filters = normalize_document_filters(
            {
                "document_state": "needs_attention",
                "document_owner": "42",
                "document_sort": "title",
            }
        )

        self.assertEqual(filters["state"], "needs_attention")
        self.assertEqual(filters["owner"], "42")
        self.assertEqual(filters["sort"], "title")


class FolderDocumentFilterTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="intake-admin",
            password="test-password",
            role="admin",
        )
        self.owner = user_model.objects.create_user(
            username="document-owner",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(name="Circulars", created_by=self.admin)
        PDFFile.objects.create(
            title="Searchable circular",
            file="pdfs/searchable.pdf",
            folder=self.folder,
            uploaded_by=self.owner,
            lifecycle="ready",
            processing_status="ready",
            indexed=True,
        )
        PDFFile.objects.create(
            title="Failed circular",
            file="pdfs/failed.pdf",
            folder=self.folder,
            uploaded_by=None,
            lifecycle="uploaded",
            processing_status="failed",
            indexed=False,
        )
        PDFFile.objects.create(
            title="Historical register",
            file="pdfs/historical.pdf",
            folder=self.folder,
            uploaded_by=self.owner,
            lifecycle="archived",
            processing_status="ready",
            indexed=False,
        )

    def test_filters_do_not_change_category_manifest_totals(self):
        page, stats = folder_cockpit_context(
            self.admin,
            self.folder,
            document_filters={
                "query": "circular",
                "state": "needs_attention",
                "owner": "unknown",
                "sort": "title",
            },
        )

        self.assertEqual(stats["total_pdfs"], 3)
        self.assertEqual(stats["filtered_pdfs"], 1)
        self.assertEqual([pdf.title for pdf in page], ["Failed circular"])

    def test_hidden_filter_returns_archived_documents(self):
        page, stats = folder_cockpit_context(
            self.admin,
            self.folder,
            document_filters={
                "query": "",
                "state": "hidden",
                "owner": "",
                "sort": "newest",
            },
        )

        self.assertEqual(stats["filtered_pdfs"], 1)
        self.assertEqual([pdf.title for pdf in page], ["Historical register"])

    def test_searchable_filter_rejects_stale_failed_index_flags(self):
        PDFFile.objects.create(
            title="Stale indexed failure",
            file="pdfs/stale-failure.pdf",
            folder=self.folder,
            uploaded_by=self.owner,
            lifecycle="ready",
            processing_status="failed",
            indexed=True,
        )

        page, stats = folder_cockpit_context(
            self.admin,
            self.folder,
            document_filters={
                "query": "",
                "state": "searchable",
                "owner": "",
                "sort": "newest",
            },
        )

        self.assertEqual(stats["total_pdfs"], 4)
        self.assertEqual(stats["filtered_pdfs"], 1)
        self.assertEqual([pdf.title for pdf in page], ["Searchable circular"])
