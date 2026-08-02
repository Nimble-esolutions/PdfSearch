import io
from unittest.mock import patch

from django.test import TestCase

from dataops.config import ResolvedProfile
from dataops.job_executor import execute_backup_job
from dataops.models import BackupJob, DataOperation


class FakeS3:
    def __init__(self, objects=None, fail_uploads=0):
        self.objects = dict(objects or {})
        self.metadata = {}
        self.fail_uploads = fail_uploads

    def head_bucket(self, **_kwargs):
        return {}

    def list_objects_v2(self, *, Bucket, Prefix, **_kwargs):
        contents = [
            {"Key": key, "Size": len(value), "ETag": f'"etag-{key}"'}
            for (bucket, key), value in sorted(self.objects.items())
            if bucket == Bucket and key.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, *, Bucket, Key, Body, **_kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)
        return {}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {"ContentLength": len(value), "Metadata": self.metadata.get((Bucket, Key), {})}

    def upload_fileobj(self, body, bucket, key, *, ExtraArgs, Config):
        if self.fail_uploads:
            self.fail_uploads -= 1
            raise RuntimeError("temporary failure")
        self.objects[(bucket, key)] = body.read()
        self.metadata[(bucket, key)] = ExtraArgs["Metadata"]


def resolved(key, role, bucket):
    return ResolvedProfile(
        key,
        key.title(),
        role,
        "https://objects.example.invalid",
        bucket,
        "us-east-1",
        "dataset",
        key,
        "TEST",
        key,
    )


class BackupJobExecutorTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.job = BackupJob.objects.using("control").create(
            name="Documents archive",
            slug="documents-archive",
            source_profile_key="source",
            target_profile_key="target",
            source_prefix="documents",
            target_prefix="backups",
            retry_limit=2,
        )
        self.operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.SYNC,
            checkpoint={"job_slug": self.job.slug},
        )
        self.profiles = (resolved("source", "restore", "source-bucket"), resolved("target", "backup", "target-bucket"))

    def test_incremental_transfer_retries_verifies_and_checkpoints(self):
        source = FakeS3({("source-bucket", "source/documents/a.pdf"): b"pdf-data"})
        target = FakeS3(fail_uploads=1)
        sleeps = []
        with patch("dataops.job_executor.resolve_profiles", return_value=self.profiles):
            receipt = execute_backup_job(
                self.operation,
                source_client=source,
                target_client=target,
                sleeper=sleeps.append,
            )

        self.assertEqual(target.objects[("target-bucket", "target/backups/a.pdf")], b"pdf-data")
        self.assertEqual(receipt["copied"], 1)
        self.assertEqual(receipt["verified"], 1)
        self.assertEqual(len(sleeps), 1)
        self.operation.refresh_from_db(using="control")
        self.assertEqual(self.operation.checkpoint["last_completed_key"], "source/documents/a.pdf")

    def test_incremental_transfer_skips_matching_target(self):
        source = FakeS3({("source-bucket", "source/documents/a.pdf"): b"pdf-data"})
        target = FakeS3({("target-bucket", "target/backups/a.pdf"): b"pdf-data"})
        target.metadata[("target-bucket", "target/backups/a.pdf")] = {"dataops-source-etag": "etag-source/documents/a.pdf"}
        with patch("dataops.job_executor.resolve_profiles", return_value=self.profiles):
            receipt = execute_backup_job(self.operation, source_client=source, target_client=target)
        self.assertEqual(receipt["copied"], 0)
        self.assertEqual(receipt["skipped"], 1)
