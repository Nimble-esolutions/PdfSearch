from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Folder, MaintenanceJob, PDFFile


class DocumentLifecycleViewTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="lifecycle-view-admin",
            password="test-password",
            role="admin",
        )
        self.viewer = get_user_model().objects.create_user(
            username="lifecycle-viewer",
            password="test-password",
            role="user",
        )
        self.folder = Folder.objects.create(name="Lifecycle views", created_by=self.admin)
        self.pdf = PDFFile.objects.create(
            title="Current circular",
            file="pdfs/current-circular.pdf",
            folder=self.folder,
            uploaded_by=self.admin,
            lifecycle="ready",
            processing_status="ready",
            indexed=True,
        )

    def test_admin_can_remove_superseded_document_without_deleting_pdf(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("remove_pdf_from_search", args=[self.pdf.pk]),
            {"reason_code": "superseded"},
        )

        self.assertEqual(response.status_code, 302)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "deprecated")
        self.assertFalse(self.pdf.indexed)
        self.assertEqual(self.pdf.file.name, "pdfs/current-circular.pdf")

    def test_unknown_reason_does_not_mutate_document(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("remove_pdf_from_search", args=[self.pdf.pk]),
            {"reason_code": "delete_forever"},
        )

        self.assertEqual(response.status_code, 302)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "ready")
        self.assertTrue(self.pdf.indexed)

    def test_ordinary_user_cannot_change_document_visibility(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("remove_pdf_from_search", args=[self.pdf.pk]),
            {"reason_code": "historical_record"},
        )

        self.assertEqual(response.status_code, 403)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "ready")

    def test_admin_can_retry_a_failed_document_once(self):
        self.pdf.lifecycle = "uploaded"
        self.pdf.processing_status = "failed"
        self.pdf.processing_error_code = "ocr_timeout"
        self.pdf.processing_error_message = "OCR timed out"
        self.pdf.indexed = False
        self.pdf.save()
        self.client.force_login(self.admin)

        first = self.client.post(reverse("retry_pdf_processing", args=[self.pdf.pk]))
        second = self.client.post(reverse("retry_pdf_processing", args=[self.pdf.pk]))

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.processing_status, "queued")
        self.assertEqual(self.pdf.lifecycle, "processing")
        self.assertEqual(self.pdf.processing_error_code, "")
        self.assertEqual(MaintenanceJob.objects.filter(kind="process_pdf").count(), 1)
