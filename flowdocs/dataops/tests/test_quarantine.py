from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from dataops.models import BackupJob, DataOperation, MirrorDeletionPreview
from dataops.quarantine import cleanup_mirror_quarantines
from dataops.tests.test_job_executor import FakeS3, resolved


@override_settings(DATAOPS_MIRROR_QUARANTINE_RETENTION_DAYS=7, DATAOPS_MIRROR_QUARANTINE_CLEANUP_MAX_OBJECTS=10)
class MirrorQuarantineCleanupTests(TestCase):
    databases = {"default", "control"}

    def test_expired_preview_and_mature_quarantine_are_cleaned_with_receipt(self):
        now = timezone.now()
        job = BackupJob.objects.using("control").create(
            name="Mirror", slug="mirror", source_profile_key="source", target_profile_key="target"
        )
        MirrorDeletionPreview.objects.using("control").create(
            job=job, digest="a" * 64, expires_at=now - timedelta(minutes=1)
        )
        operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.SYNC,
            state=DataOperation.State.SUCCEEDED,
            destination_profile_key="target",
            finished_at=now - timedelta(days=8),
        )
        prefix = f"target/.dataops-quarantine/{operation.public_id}/"
        operation.result = {"deletion": {"quarantine_prefix": prefix}}
        operation.save(update_fields=["result", "updated_at"])
        client = FakeS3({("target-bucket", prefix + "orphan.pdf"): b"recoverable"})
        profiles = (resolved("target", "backup", "target-bucket"),)
        with patch("dataops.quarantine.resolve_profiles", return_value=profiles):
            receipt = cleanup_mirror_quarantines(now=now, clients={"target": client})
        self.assertEqual(receipt, {"expired_previews": 1, "scanned": 1, "removed": 1})
        self.assertFalse(client.objects)
        operation.refresh_from_db(using="control")
        self.assertIn("cleanup_completed_at", operation.result["deletion"])
        self.assertEqual(MirrorDeletionPreview.objects.using("control").get().state, MirrorDeletionPreview.State.EXPIRED)

    def test_recent_quarantine_is_retained(self):
        now = timezone.now()
        operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.SYNC, state=DataOperation.State.SUCCEEDED,
            destination_profile_key="target", finished_at=now - timedelta(days=1),
            result={"deletion": {"quarantine_prefix": "target/.dataops-quarantine/recent/"}},
        )
        with patch("dataops.quarantine.resolve_profiles", return_value=()):
            receipt = cleanup_mirror_quarantines(now=now, clients={})
        self.assertEqual(receipt["removed"], 0)
