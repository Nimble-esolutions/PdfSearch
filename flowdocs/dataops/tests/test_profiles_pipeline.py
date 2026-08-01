import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dataops.config import (
    ResolvedProfile,
    resolve_profiles,
    resolve_selectors,
    validate_operation_route,
    validate_profiles,
)
from dataops.pipeline import (
    DataOpsPipelineError,
    preflight_operation,
    publish_local_backup,
    run_operation_pipeline,
    transfer_generation,
)


class FakeS3:
    def __init__(self, *, fail_puts=0):
        self.objects = {}
        self.fail_puts = fail_puts

    def head_bucket(self, **_kwargs):
        return {}

    def put_object(self, *, Bucket, Key, Body, **_kwargs):
        if self.fail_puts:
            self.fail_puts -= 1
            raise RuntimeError("temporary object-store failure")
        self.objects[(Bucket, Key)] = bytes(Body)
        return {}

    def get_object(self, *, Bucket, Key):
        class Body:
            def __init__(self, value):
                self.value = value
                self.offset = 0

            def read(self, limit=-1):
                if self.offset >= len(self.value):
                    return b""
                if limit is None or limit < 0:
                    limit = len(self.value) - self.offset
                value = self.value[self.offset : self.offset + limit]
                self.offset += len(value)
                return value

        return {"Body": Body(self.objects[(Bucket, Key)])}

    def delete_object(self, **kwargs):
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)


def profile(name, role, bucket, namespace):
    return ResolvedProfile(
        name,
        name.replace("_", " ").title(),
        role,
        "https://objects.example.invalid",
        bucket,
        "ap-south-1",
        "flowdocs-prod",
        name,
        "OPS",
        namespace,
    )


class ProfileManifestTests(unittest.TestCase):
    def test_structured_manifest_and_effective_source_are_deterministic(self):
        profiles = resolve_profiles(
            {
                "DATAOPS_PROFILE_MANIFEST": json.dumps(
                    [
                        {
                            "name": "prod_restore",
                            "role": "restore",
                            "endpoint": "https://objects.example.invalid",
                            "bucket": "old",
                            "dataset_id": "flowdocs-prod",
                            "namespace": "prod",
                            "credential_ref": "DATAOPS_PROD",
                        }
                    ]
                )
            }
        )
        self.assertEqual(profiles[0].key, "prod_restore")
        self.assertEqual(profiles[0].effective_source, "environment")
        self.assertEqual(profiles[0].namespace, "prod")
        self.assertNotIn("secret_key", profiles[0].redacted())

    def test_stored_profile_is_used_when_environment_manifest_is_absent(self):
        profiles = resolve_profiles(
            {},
            stored_profiles=[
                {
                    "name": "stored_backup",
                    "role": "backup",
                    "endpoint": "https://objects.example.invalid",
                    "bucket": "new",
                    "dataset_id": "flowdocs-prod",
                    "namespace": "stored",
                    "credential_ref": "DATAOPS_STORED",
                }
            ],
        )
        self.assertEqual(profiles[0].effective_source, "stored")
        self.assertFalse(profiles[0].environment_locked)

    def test_role_and_selector_validation_is_actionable(self):
        profiles = (profile("restore_only", "restore", "old", "old"),)
        issues = validate_profiles(
            profiles,
            selectors={"backup": "restore_only", "restore": "restore_only"},
            operation="backup",
        )
        self.assertIn("profile_role_disallows_backup", {issue.code for issue in issues})

    def test_same_bucket_namespace_collision_is_rejected(self):
        source = profile("old", "both", "shared", "same")
        destination = profile("new", "both", "shared", "same")
        issues = validate_operation_route(source, destination, operation="copy")
        self.assertIn("same_bucket_namespace_collision", {issue.code for issue in issues})


class ProfileMatrixPipelineTests(unittest.TestCase):
    def test_old_to_old_old_to_new_new_to_old_and_new_to_new(self):
        cases = [("old", "old"), ("old", "new"), ("new", "old"), ("new", "new")]
        for source_bucket, destination_bucket in cases:
            with self.subTest(source_bucket=source_bucket, destination_bucket=destination_bucket):
                source = profile("source", "both", source_bucket, "source")
                destination = profile("destination", "both", destination_bucket, "destination")
                source_client = FakeS3()
                destination_client = source_client if source_bucket == destination_bucket else FakeS3()
                publish_local_backup(
                    source_client,
                    source,
                    source_root="/tmp",
                    release_id="matrix-release",
                    files=[("media/a.pdf", b"pdf")],
                )
                receipt = transfer_generation(source_client, source, destination_client, destination, "matrix-release")
                self.assertEqual(receipt["objects"], 1)
                self.assertEqual(receipt["destination_profile"], "destination")

    def test_legacy_layout_is_read_and_written_when_namespace_is_empty(self):
        source = ResolvedProfile("source", "Source", "both", "https://objects.example.invalid", "old", "ap-south-1", "flowdocs-prod", "source", "OPS")
        destination = ResolvedProfile("destination", "Destination", "both", "https://objects.example.invalid", "new", "ap-south-1", "flowdocs-prod", "destination", "OPS")
        source_client = FakeS3()
        destination_client = FakeS3()
        published = publish_local_backup(source_client, source, source_root="/tmp", release_id="legacy-release", files=[("payload.bin", b"legacy")])
        self.assertTrue(published["manifest_key"].startswith("datasets/flowdocs-prod/generations/"))
        transferred = transfer_generation(source_client, source, destination_client, destination, "legacy-release")
        self.assertTrue(transferred["manifest_key"].startswith("datasets/flowdocs-prod/generations/"))

    def test_pipeline_retries_failed_transfer_and_publishes_receipt(self):
        source = profile("source", "both", "old", "source")
        destination = profile("destination", "both", "new", "destination")
        source_client = FakeS3()
        destination_client = FakeS3(fail_puts=1)
        publish_local_backup(source_client, source, source_root="/tmp", release_id="retry-release", files=[("media/a.pdf", b"pdf")])
        result = run_operation_pipeline(
            "copy",
            (source, destination),
            source_profile="source",
            destination_profile="destination",
            source_client=source_client,
            destination_client=destination_client,
            release_id="retry-release",
            max_retries=2,
        )
        self.assertEqual(result["receipt"]["manifest_digest"], result["manifest_digest"])
        self.assertGreaterEqual(result["attempts"], 2)

    def test_restore_preflight_does_not_replace_active_generation_without_safety_backup(self):
        source = profile("source", "restore", "old", "source")
        destination = profile("destination", "both", "new", "destination")
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            (runtime / "active").mkdir(parents=True)
            preview = preflight_operation("restore", source, None, release_id="r1", active_root=runtime)
            self.assertTrue(preview.safety_backup_required)
            self.assertEqual(preview.route.source.key, "source")

    def test_restore_health_failure_leaves_previous_generation_active(self):
        source = profile("source", "restore", "old", "source")
        source_client = FakeS3()
        publish_local_backup(
            source_client,
            source,
            source_root="/tmp",
            release_id="restore-release",
            files=[("payload.bin", b"verified")],
        )
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            active = runtime / "active"
            active.mkdir(parents=True)
            (active / "previous.txt").write_text("previous", encoding="utf-8")
            with self.assertRaises(DataOpsPipelineError) as caught:
                run_operation_pipeline(
                    "restore",
                    (source,),
                    source_profile="source",
                    source_client=source_client,
                    release_id="restore-release",
                    staging_root=Path(temp) / "staging",
                    runtime_root=runtime,
                    safety_backup=lambda: {"release_id": "safety-1"},
                    health_checker=lambda _result: {"status": "degraded"},
                    max_retries=0,
                )
            self.assertEqual(caught.exception.code, "health_check_retryable")
            self.assertEqual((active / "previous.txt").read_text(encoding="utf-8"), "previous")
            self.assertFalse((runtime / "generations" / "restore-release").exists())

    def test_restore_requires_a_release_and_rejects_remote_dataset_mismatch(self):
        source = profile("source", "both", "old", "source")
        destination = ResolvedProfile(
            "destination",
            "Destination",
            "both",
            "https://objects.example.invalid",
            "new",
            "ap-south-1",
            "other-dataset",
            "destination",
            "OPS",
            "destination",
        )
        missing_release = preflight_operation("restore", source, None)
        self.assertIn("release_id_missing", {issue.code for issue in missing_release.issues})
        mismatch = preflight_operation("restore", source, destination, release_id="r1")
        self.assertIn("dataset_mismatch", {issue.code for issue in mismatch.issues})


if __name__ == "__main__":
    unittest.main()
