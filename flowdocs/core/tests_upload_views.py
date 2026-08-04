import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Folder, MaintenanceJob, PDFFile, UploadBatch


class UploadBatchViewTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="batch-view-admin",
            password="test-password",
            role="admin",
        )
        self.other_admin = user_model.objects.create_user(
            username="batch-view-other",
            password="test-password",
            role="admin",
        )
        self.viewer = user_model.objects.create_user(
            username="batch-view-viewer",
            password="test-password",
            role="user",
        )
        self.folder = Folder.objects.create(
            name="Batch view folder",
            created_by=self.other_admin,
        )

    def _pdf(self, name="document.pdf", body=b"%PDF-1.7\nfixture"):
        return SimpleUploadedFile(name, body, content_type="application/pdf")

    def _create_batch(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("upload_batch_create", args=[self.folder.pk])
        )
        self.assertIn(response.status_code, {200, 201})
        return response.json()["batch"]

    def _receive(self, batch, key="file-1", **kwargs):
        return self.client.post(
            batch["endpoints"]["item"],
            {
                "idempotency_key": key,
                "title": kwargs.get("title", "Document title"),
                "file": kwargs.get("file", self._pdf()),
            },
        )

    def test_admin_can_create_or_resume_a_folder_batch(self):
        first = self._create_batch()
        second = self._create_batch()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(UploadBatch.objects.count(), 1)
        self.assertEqual(first["counts"]["limit"], 50)

    def test_ordinary_user_cannot_create_batch(self):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("upload_batch_create", args=[self.folder.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "operator_permission_required")

    def test_django_superuser_remains_authorized_when_custom_role_is_stale(self):
        superuser = get_user_model().objects.create_superuser(
            username="django-superuser",
            password="test-password",
            role="superadmin",
        )
        get_user_model().objects.filter(pk=superuser.pk).update(role="user")
        superuser.refresh_from_db()
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("upload_batch_create", args=[self.folder.pk])
        )

        self.assertEqual(response.status_code, 201)

    def test_individual_upload_returns_safe_manifest_projection(self):
        batch = self._create_batch()

        response = self._receive(batch)

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["item"]["display_state"], "received")
        self.assertEqual(payload["batch"]["counts"]["received"], 1)
        self.assertTrue(payload["batch"]["can_finalize"])
        self.assertNotIn("file_path", payload["item"])
        pdf = PDFFile.objects.get()
        self.assertEqual(pdf.lifecycle, "intake")

    def test_invalid_upload_is_persisted_without_pdf_bytes(self):
        batch = self._create_batch()

        response = self._receive(
            batch,
            key="invalid",
            file=self._pdf("invalid.pdf", body=b"not a pdf"),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["item"]["error_code"], "invalid_pdf_header")
        self.assertEqual(PDFFile.objects.count(), 0)

    def test_other_admin_cannot_read_or_mutate_owned_batch(self):
        batch = self._create_batch()
        self.client.force_login(self.other_admin)

        response = self.client.get(batch["endpoints"]["detail"])

        self.assertEqual(response.status_code, 404)

    def test_remove_item_deletes_only_that_draft_pdf(self):
        batch = self._create_batch()
        first = self._receive(batch, key="first").json()["item"]
        self._receive(
            batch,
            key="second",
            file=self._pdf("second.pdf", body=b"%PDF-1.7\nsecond"),
        )

        response = self.client.post(first["remove_url"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["batch"]["counts"]["received"], 1)
        self.assertEqual(PDFFile.objects.count(), 1)

    def test_finalize_is_idempotent_and_queues_one_grouped_job(self):
        batch = self._create_batch()
        self._receive(batch, key="first")
        self._receive(
            batch,
            key="second",
            file=self._pdf("second.pdf", body=b"%PDF-1.7\nsecond"),
        )

        first = self.client.post(batch["endpoints"]["finalize"])
        second = self.client.post(batch["endpoints"]["finalize"])

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(MaintenanceJob.objects.count(), 1)
        self.assertEqual(first.json()["batch"]["job"]["total_items"], 2)
        self.assertEqual(
            first.json()["batch"]["job"]["id"],
            second.json()["batch"]["job"]["id"],
        )

    def test_finalized_ready_but_unindexed_item_needs_attention(self):
        batch = self._create_batch()
        item = self._receive(batch).json()["item"]
        self.client.post(batch["endpoints"]["finalize"])
        PDFFile.objects.filter(pk=item["pdf_id"]).update(
            lifecycle="ready",
            processing_status="ready",
            indexed=False,
        )

        response = self.client.get(batch["endpoints"]["detail"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["batch"]["items"][0]["display_state"],
            "needs_attention",
        )
