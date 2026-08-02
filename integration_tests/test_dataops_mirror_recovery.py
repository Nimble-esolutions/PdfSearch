"""Real S3-compatible proof for guarded mirror deletion and recovery."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from dataops.job_executor import execute_backup_job
from dataops.mirror import create_deletion_preview
from dataops.models import BackupJob, DataOperation, MirrorDeletionPreview
from dataops.quarantine import cleanup_mirror_quarantines, recover_mirror_quarantine
from dataops.storage import client_for_profile
from dataops.config import profile_map, resolve_profiles


@override_settings(DATAOPS_MIRROR_QUARANTINE_RETENTION_DAYS=1, DATAOPS_MIRROR_QUARANTINE_CLEANUP_MAX_OBJECTS=20)
class DataOpsMirrorRecoveryIntegrationTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        profiles = profile_map(resolve_profiles())
        self.source = profiles["integration-source"]
        self.target = profiles["integration-target"]
        self.client = client_for_profile(self.source, __import__("os").environ, allow_http=True)
        self.bucket = self.source.bucket
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)
        self._clear("dataops-integration/")

    def tearDown(self):
        self._clear("dataops-integration/")

    def _clear(self, prefix):
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        values = [{"Key": item["Key"]} for item in response.get("Contents", [])]
        if values:
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": values, "Quiet": True})

    def _put(self, key, body):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    def test_preview_quarantine_recovery_conflict_and_retention_cleanup(self):
        self._put("dataops-integration/source/docs/current.pdf", b"current")
        self._put("dataops-integration/target/backups/current.pdf", b"stale")
        self._put("dataops-integration/target/backups/recover.pdf", b"recover")
        self._put("dataops-integration/target/backups/conflict.pdf", b"conflict-old")
        job = BackupJob.objects.using("control").create(
            name="Provider mirror proof", slug="provider-mirror-proof",
            source_profile_key=self.source.key, target_profile_key=self.target.key,
            source_prefix="docs", target_prefix="backups", mode=BackupJob.Mode.MIRROR,
            delete_orphans=True, mirror_delete_max_objects=10, mirror_delete_max_percent=100,
        )
        preview = create_deletion_preview(job)
        self.assertEqual(len(preview.object_keys), 2)
        operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.SYNC, source_profile_key=self.source.key,
            destination_profile_key=self.target.key,
            checkpoint={"job_slug": job.slug, "mirror_preview_id": str(preview.public_id)},
        )
        receipt = execute_backup_job(operation)
        self.assertEqual(receipt["deletion"]["deleted"], 2)
        with self.assertRaises(Exception):
            self.client.head_object(Bucket=self.bucket, Key="dataops-integration/target/backups/recover.pdf")

        self._put("dataops-integration/target/backups/conflict.pdf", b"conflict-new")
        operation.state = DataOperation.State.SUCCEEDED
        operation.result = receipt
        operation.finished_at = timezone.now()
        operation.save(update_fields=["state", "result", "finished_at", "updated_at"])
        recovery = recover_mirror_quarantine(operation)
        self.assertEqual(recovery["restored"], 1)
        self.assertEqual(recovery["conflicts"], 1)
        recovered = self.client.get_object(Bucket=self.bucket, Key="dataops-integration/target/backups/recover.pdf")["Body"].read()
        conflict = self.client.get_object(Bucket=self.bucket, Key="dataops-integration/target/backups/conflict.pdf")["Body"].read()
        self.assertEqual(recovered, b"recover")
        self.assertEqual(conflict, b"conflict-new")

        operation.finished_at = timezone.now() - timedelta(days=2)
        operation.save(update_fields=["finished_at", "updated_at"])
        cleanup = cleanup_mirror_quarantines(now=timezone.now())
        self.assertEqual(cleanup["removed"], 1)
        preview.refresh_from_db(using="control")
        self.assertEqual(preview.state, MirrorDeletionPreview.State.APPLIED)
