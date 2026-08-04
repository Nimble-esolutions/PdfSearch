import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from core.maintenance import queue_job as real_queue_job
from core.models import (
    SEARCHABLE_PDF_LIFECYCLES,
    Folder,
    MaintenanceJob,
    PDFFile,
    UploadBatchItem,
)
from core.services.upload_intake import (
    UploadBatchLimitError,
    UploadBatchStateError,
    UploadIntakePermissionError,
    create_upload_batch,
    cleanup_expired_upload_batches,
    discard_upload_batch,
    finalize_upload_batch,
    remove_upload_item,
    receive_upload,
)


class UploadIntakeTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="intake-owner",
            password="test-password",
            role="admin",
        )
        self.other = user_model.objects.create_user(
            username="intake-other",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(name="Intake", created_by=self.owner)

    def pdf(self, name="document.pdf", body=b"%PDF-1.7\nfixture", content_type="application/pdf"):
        return SimpleUploadedFile(name, body, content_type=content_type)

    def receive(self, batch, key, **kwargs):
        return receive_upload(
            batch=batch,
            actor=self.owner,
            idempotency_key=key,
            uploaded_file=kwargs.pop(
                "uploaded_file",
                self.pdf(f"{key}.pdf", body=f"%PDF-1.7\n{key}".encode()),
            ),
            title=kwargs.pop("title", f"Document {key}"),
            **kwargs,
        )

    def test_batch_accepts_50_files_and_rejects_51st(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)

        for index in range(50):
            item = self.receive(batch, f"file-{index:02d}")
            self.assertEqual(item.state, "received")

        with self.assertRaisesRegex(UploadBatchLimitError, "more than 50"):
            self.receive(batch, "file-50")

        self.assertEqual(batch.items.count(), 50)
        self.assertEqual(PDFFile.objects.count(), 50)

    def test_existing_pdf_validation_rules_are_persisted_as_safe_rejections(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        cases = (
            (
                "bad-extension",
                self.pdf("document.txt"),
                "invalid_file_extension",
            ),
            (
                "bad-content-type",
                self.pdf("document.pdf", content_type="text/plain"),
                "invalid_content_type",
            ),
            (
                "bad-header",
                self.pdf("document.pdf", body=b"not a pdf"),
                "invalid_pdf_header",
            ),
        )

        for key, uploaded_file, expected_code in cases:
            item = self.receive(batch, key, uploaded_file=uploaded_file)
            self.assertEqual(item.state, "rejected")
            self.assertEqual(item.error_code, expected_code)
            self.assertIsNone(item.pdf_file)

        self.assertEqual(PDFFile.objects.count(), 0)
        self.assertFalse(any(Path(self.media_dir.name).rglob("*.pdf")))

    def test_size_validation_reuses_max_file_size(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        uploaded_file = self.pdf("large.pdf", body=b"%PDF-" + b"x" * 20)

        with override_settings(MAX_FILE_SIZE=10, MAX_FILE_SIZE_MB=10 / (1024 * 1024)):
            item = self.receive(batch, "too-large", uploaded_file=uploaded_file)

        self.assertEqual(item.state, "rejected")
        self.assertEqual(item.error_code, "file_too_large")
        self.assertIsNone(item.pdf_file)
        self.assertEqual(PDFFile.objects.count(), 0)

    def test_individual_upload_is_retry_idempotent(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        first = self.receive(
            batch,
            "stable-key",
            uploaded_file=self.pdf("first.pdf", body=b"%PDF-1.7\nfirst"),
        )
        retried = self.receive(
            batch,
            "stable-key",
            uploaded_file=self.pdf("second.pdf", body=b"%PDF-1.7\nsecond"),
        )

        self.assertEqual(retried.pk, first.pk)
        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(PDFFile.objects.count(), 1)
        self.assertEqual(retried.original_filename, "first.pdf")

    def test_duplicate_content_is_rejected_without_storing_another_pdf(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        duplicate_bytes = b"%PDF-1.7\nduplicate"
        first = self.receive(
            batch,
            "first-copy",
            uploaded_file=self.pdf("first.pdf", body=duplicate_bytes),
        )
        duplicate = self.receive(
            batch,
            "second-copy",
            uploaded_file=self.pdf("second.pdf", body=duplicate_bytes),
        )

        self.assertEqual(first.state, "received")
        self.assertEqual(duplicate.state, "rejected")
        self.assertEqual(duplicate.error_code, "duplicate_in_batch")
        self.assertEqual(PDFFile.objects.count(), 1)

    def test_received_pdf_is_queued_but_not_searchable(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)

        item = self.receive(batch, "private-intake")

        self.assertEqual(item.pdf_file.lifecycle, "intake")
        self.assertEqual(item.pdf_file.processing_status, "queued")
        self.assertFalse(item.pdf_file.indexed)
        self.assertNotIn(item.pdf_file.lifecycle, SEARCHABLE_PDF_LIFECYCLES)

    def test_partial_invalid_batch_preserves_valid_pdf_without_bad_bytes(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        accepted = self.receive(batch, "accepted")
        rejected = self.receive(
            batch,
            "rejected",
            uploaded_file=self.pdf("broken.pdf", body=b"broken"),
        )

        self.assertEqual(accepted.state, "received")
        self.assertEqual(rejected.state, "rejected")
        self.assertEqual(PDFFile.objects.count(), 1)
        stored_files = [path for path in Path(self.media_dir.name).rglob("*") if path.is_file()]
        self.assertEqual(len(stored_files), 1)
        self.assertEqual(stored_files[0].read_bytes(), b"%PDF-1.7\naccepted")

    def test_receipt_failure_rolls_back_row_and_cleans_stored_file(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)

        with patch.object(
            UploadBatchItem.objects,
            "create",
            side_effect=RuntimeError("receipt unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "receipt unavailable"):
                self.receive(batch, "failed-receipt")

        self.assertEqual(PDFFile.objects.count(), 0)
        self.assertFalse(
            any(path.is_file() for path in Path(self.media_dir.name).rglob("*"))
        )

    def test_due_batch_is_persistently_expired_before_refusing_upload(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        batch.expires_at = timezone.now() - timedelta(seconds=1)
        batch.save(update_fields=["expires_at"])

        with self.assertRaisesRegex(UploadBatchStateError, "expired"):
            self.receive(batch, "expired")

        batch.refresh_from_db()
        self.assertEqual(batch.status, "expired")
        self.assertEqual(batch.items.count(), 0)
        self.assertEqual(PDFFile.objects.count(), 0)

    def test_finalize_creates_one_job_with_all_received_pdfs(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        accepted = [self.receive(batch, f"accepted-{index}") for index in range(3)]
        self.receive(
            batch,
            "rejected",
            uploaded_file=self.pdf("broken.pdf", body=b"broken"),
        )

        job = finalize_upload_batch(batch=batch, actor=self.owner)
        batch.refresh_from_db()

        self.assertEqual(MaintenanceJob.objects.count(), 1)
        self.assertEqual(job.kind, "process_pdf")
        self.assertEqual(job.total_items, 3)
        self.assertEqual(
            set(job.items.values_list("pdf_id", flat=True)),
            {item.pdf_file_id for item in accepted},
        )
        self.assertEqual(batch.status, "finalized")
        self.assertEqual(batch.maintenance_job, job)
        self.assertIsNotNone(batch.finalized_at)
        self.assertEqual(len(batch.manifest_sha256), 64)
        self.assertEqual(job.options["manifest_sha256"], batch.manifest_sha256)

    def test_repeat_finalize_returns_existing_job_without_queueing_again(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        self.receive(batch, "accepted")

        with patch(
            "core.services.upload_intake.queue_job",
            wraps=real_queue_job,
        ) as queue:
            first = finalize_upload_batch(batch=batch, actor=self.owner)
            second = finalize_upload_batch(batch=batch, actor=self.owner)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MaintenanceJob.objects.count(), 1)
        queue.assert_called_once()

    def test_services_enforce_folder_and_batch_ownership(self):
        other_batch = create_upload_batch(folder=self.folder, uploader=self.other)
        self.assertEqual(other_batch.uploader, self.other)

        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        with self.assertRaises(UploadIntakePermissionError):
            receive_upload(
                batch=batch,
                actor=self.other,
                idempotency_key="foreign",
                uploaded_file=self.pdf(),
                title="Foreign",
            )
        with self.assertRaises(UploadIntakePermissionError):
            finalize_upload_batch(batch=batch, actor=self.other)
        with self.assertRaises(UploadIntakePermissionError):
            discard_upload_batch(batch=batch, actor=self.other)

    def test_discard_deletes_only_unfinalized_batch_pdfs_and_storage(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        item = self.receive(batch, "discarded")
        retained = PDFFile.objects.create(
            title="Unrelated",
            file=self.pdf("unrelated.pdf"),
            folder=self.folder,
            uploaded_by=self.owner,
        )
        discarded_path = Path(item.pdf_file.file.path)
        retained_path = Path(retained.file.path)
        self.assertTrue(discarded_path.exists())
        self.assertTrue(retained_path.exists())

        with self.captureOnCommitCallbacks(execute=True):
            discarded = discard_upload_batch(batch=batch, actor=self.owner)

        item.refresh_from_db()
        self.assertEqual(discarded.status, "discarded")
        self.assertEqual(item.state, "removed")
        self.assertIsNone(item.pdf_file)
        self.assertFalse(discarded_path.exists())
        self.assertTrue(retained_path.exists())
        self.assertTrue(PDFFile.objects.filter(pk=retained.pk).exists())

        with self.assertRaises(UploadBatchStateError):
            finalize_upload_batch(batch=batch, actor=self.owner)

    def test_finalized_batch_cannot_be_discarded(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        self.receive(batch, "accepted")
        finalize_upload_batch(batch=batch, actor=self.owner)

        with self.assertRaises(UploadBatchStateError):
            discard_upload_batch(batch=batch, actor=self.owner)

        self.assertEqual(UploadBatchItem.objects.filter(batch=batch).count(), 1)
        self.assertEqual(PDFFile.objects.count(), 1)

    def test_remove_item_preserves_other_batch_items(self):
        batch = create_upload_batch(folder=self.folder, uploader=self.owner)
        removed = self.receive(batch, "removed")
        retained = self.receive(
            batch,
            "retained",
            uploaded_file=self.pdf("retained.pdf", body=b"%PDF-1.7\nretained"),
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = remove_upload_item(batch=batch, item=removed, actor=self.owner)

        self.assertEqual(result.state, "removed")
        self.assertFalse(PDFFile.objects.filter(pk=removed.pdf_file_id).exists())
        self.assertTrue(PDFFile.objects.filter(pk=retained.pdf_file_id).exists())

    def test_cleanup_expired_batches_is_bounded_and_preserves_live_batches(self):
        expired = create_upload_batch(folder=self.folder, uploader=self.owner)
        expired_item = self.receive(expired, "expired-cleanup")
        live = create_upload_batch(folder=self.folder, uploader=self.owner)
        live_item = self.receive(
            live,
            "live-cleanup",
            uploaded_file=self.pdf("live.pdf", body=b"%PDF-1.7\nlive"),
        )
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at"])

        with self.captureOnCommitCallbacks(execute=True):
            cleaned = cleanup_expired_upload_batches(limit=1)

        expired.refresh_from_db()
        live.refresh_from_db()
        self.assertEqual(cleaned, 1)
        self.assertEqual(expired.status, "expired")
        self.assertEqual(live.status, "draft")
        self.assertFalse(PDFFile.objects.filter(pk=expired_item.pdf_file_id).exists())
        self.assertTrue(PDFFile.objects.filter(pk=live_item.pdf_file_id).exists())
