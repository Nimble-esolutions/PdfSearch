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
    clone_confirmation_phrase,
    preflight_operation,
    publish_local_backup,
    run_operation_pipeline,
    stage_remote_generation,
    transfer_generation,
)
from dataops.clone import clone_rebind_generation


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
    def test_environment_overrides_matching_stored_profile_without_hiding_others(self):
        stored = [
            {"name": "archive", "role": "backup", "endpoint": "https://archive.example", "bucket": "archive", "dataset_id": "dataset", "namespace": "stored/archive", "credential_ref": "ARCHIVE"},
            {"name": "primary", "role": "backup", "endpoint": "https://stale.example", "bucket": "stale", "dataset_id": "dataset", "namespace": "stored/primary", "credential_ref": "STALE"},
        ]
        environ = {
            "DATAOPS_PROFILE_MANIFEST": '[{"name":"primary","role":"backup","provider":"aws","endpoint":"https://s3.us-east-1.amazonaws.com","bucket":"current","dataset_id":"dataset","namespace":"env/primary","credential_ref":"CURRENT"}]'
        }

        profiles = resolve_profiles(environ, stored_profiles=stored)

        self.assertEqual([profile.key for profile in profiles], ["archive", "primary"])
        self.assertEqual(profiles[0].effective_source, "stored")
        self.assertEqual(profiles[1].effective_source, "environment")
        self.assertEqual(profiles[1].bucket, "current")
        self.assertTrue(profiles[1].environment_locked)
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

    def test_provider_is_inferred_for_legacy_self_hosted_manifest(self):
        profiles = resolve_profiles({"DATAOPS_PROFILE_MANIFEST": '[{"name":"local","role":"both","endpoint":"http://rustfs:9000","bucket":"data","dataset_id":"dataset","namespace":"local","credential_ref":"LOCAL"}]'})
        self.assertEqual(profiles[0].provider, "rustfs")

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
    @staticmethod
    def _legacy_manifest(dataset_id, generation_id):
        database = b"sqlite-copy"
        pdf = b"pdf-copy"
        database_entry = {
            "path": "db.sqlite3",
            "bytes": len(database),
            "sha256": hashlib.sha256(database).hexdigest(),
            "object_key": f"datasets/{dataset_id}/blobs/files/{hashlib.sha256(database).hexdigest()}",
            "artifact_type": "database",
        }
        pdf_entry = {
            "path": "media/a.pdf",
            "bytes": len(pdf),
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "object_key": f"datasets/{dataset_id}/blobs/pdfs/sha256/{hashlib.sha256(pdf).hexdigest()}.pdf",
            "artifact_type": "media",
        }
        return {
            "manifest_version": 1,
            "read_only": True,
            "release_id": generation_id,
            "dataset_id": dataset_id,
            "production_source_id": "production",
            "app_release": "test",
            "image_digest": "sha256:" + "a" * 64,
            "schema": {"inventory_schema": "legacy-volume-port/v2"},
            "source": {"kind": "legacy-data-root", "root_contract": "read-only-volume"},
            "database": {"entry": database_entry},
            "pdf_storage": {"root": "media", "files": [pdf_entry], "file_count": 1},
            "faiss": {"root": "faiss_indexes", "files": [], "count": 0},
            "chroma": {"root": "chroma_db", "files": [], "count": 0},
            "counts": {"files": 2, "pdfs": 1, "faiss": 0, "chroma": 0, "pdf_cache": 0, "staticfiles": 0, "backups": 0},
            "files": [database_entry, pdf_entry],
        }, {"db.sqlite3": database, "media/a.pdf": pdf}

    def test_clone_rebind_preserves_source_and_rewrites_lineage(self):
        source = ResolvedProfile("production_v2_source", "Production", "restore", "https://objects.example.invalid", "source-bucket", "us-east-1", "source-dataset", "production", "OPS", "production-v2")
        destination = ResolvedProfile("stage_2026", "Stage", "both", "https://objects.example.invalid", "stage-bucket", "us-east-1", "stage-dataset", "stage", "OPS", "stage-2026")
        source_client = FakeS3()
        destination_client = FakeS3()
        generation = "legacy-generation"
        manifest, files = self._legacy_manifest(source.dataset_id, generation)
        for entry in manifest["files"]:
            source_client.put_object(Bucket=source.bucket, Key=entry["object_key"], Body=files[entry["path"]])
        manifest_key = f"datasets/{source.dataset_id}/generations/{generation}/manifest.json"
        source_client.put_object(Bucket=source.bucket, Key=manifest_key, Body=(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode())
        source_before = dict(source_client.objects)
        destination_generation = "stage-rehearsal-1"
        confirmation = clone_confirmation_phrase(source.key, generation, destination.key, destination_generation)

        result = clone_rebind_generation(
            source_client,
            source,
            destination_client,
            destination,
            generation,
            destination_generation_id=destination_generation,
            confirmation=confirmation,
        )

        self.assertEqual(source_client.objects, source_before)
        self.assertEqual(result["source_manifest_digest"], hashlib.sha256(source_client.objects[(source.bucket, manifest_key)]).hexdigest())
        self.assertEqual(result["lineage"]["parent_dataset_id"], source.dataset_id)
        self.assertEqual(result["lineage"]["parent_generation_id"], generation)
        self.assertEqual(result["manifest"]["dataset_id"], destination.dataset_id)
        self.assertTrue(all(entry["object_key"].startswith(f"datasets/{destination.dataset_id}/") for entry in result["manifest"]["files"]))
        pointer = json.loads(destination_client.objects[(destination.bucket, f"datasets/{destination.dataset_id}/control/authoritative.json")])
        self.assertEqual(pointer["generation_id"], destination_generation)
        self.assertEqual(pointer["manifest_sha256"], result["manifest_digest"])

    def test_clone_rebind_rejects_immutable_destination_collision(self):
        source = ResolvedProfile("source", "Source", "restore", "https://objects.example.invalid", "source-bucket", "us-east-1", "source-dataset", "production", "OPS", "source")
        destination = ResolvedProfile("destination", "Destination", "both", "https://objects.example.invalid", "destination-bucket", "us-east-1", "stage-dataset", "stage", "OPS", "destination")
        source_client = FakeS3()
        destination_client = FakeS3()
        generation = "collision-generation"
        manifest, files = self._legacy_manifest(source.dataset_id, generation)
        for entry in manifest["files"]:
            source_client.put_object(Bucket=source.bucket, Key=entry["object_key"], Body=files[entry["path"]])
        manifest_key = f"datasets/{source.dataset_id}/generations/{generation}/manifest.json"
        source_client.put_object(Bucket=source.bucket, Key=manifest_key, Body=(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode())
        destination_key = manifest["files"][0]["object_key"].replace(f"datasets/{source.dataset_id}/", f"datasets/{destination.dataset_id}/")
        destination_client.put_object(Bucket=destination.bucket, Key=destination_key, Body=b"wrong-content")

        with self.assertRaises(DataOpsPipelineError) as caught:
            clone_rebind_generation(
                source_client,
                source,
                destination_client,
                destination,
                generation,
                confirmation=clone_confirmation_phrase(source.key, generation, destination.key, "clone-" + generation),
            )
        self.assertEqual(caught.exception.code, "destination_immutable_conflict")
        self.assertNotIn((destination.bucket, f"datasets/{destination.dataset_id}/control/authoritative.json"), destination_client.objects)

    def test_legacy_clone_manifest_stages_files_at_application_paths(self):
        source = ResolvedProfile("source", "Source", "restore", "https://objects.example.invalid", "source-bucket", "us-east-1", "source-dataset", "production", "OPS", "source")
        source_client = FakeS3()
        generation = "stage-source"
        manifest, files = self._legacy_manifest(source.dataset_id, generation)
        for entry in manifest["files"]:
            source_client.put_object(Bucket=source.bucket, Key=entry["object_key"], Body=files[entry["path"]])
        source_client.put_object(
            Bucket=source.bucket,
            Key=f"datasets/{source.dataset_id}/generations/{generation}/manifest.json",
            Body=(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        with tempfile.TemporaryDirectory() as temp:
            staged = stage_remote_generation(source_client, source, release_id=generation, destination_root=temp)
            self.assertEqual((Path(staged["workspace"]) / "media" / "a.pdf").read_bytes(), files["media/a.pdf"])
            self.assertEqual(staged["manifest"]["format_version"], 2)

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
