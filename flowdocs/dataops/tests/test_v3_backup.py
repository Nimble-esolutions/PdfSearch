"""Full-manifest/incremental-transfer publication tests for DataOps v3."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from dataops.package_v3 import validate_manifest, verify_manifest_signature
from dataops.v3_backup import V3BackupError, blob_key, publish_snapshot
from dataops.v3_config import ConnectionView
from dataops.v3_storage import V3StorageError


class MissingObject(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.tamper_reads = set()

    def _etag(self, body):
        return hashlib.md5(body, usedforsecurity=False).hexdigest()

    def head_object(self, *, Bucket, Key):
        try:
            item = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObject() from exc
        return {
            "ContentLength": len(item["body"]),
            "Metadata": dict(item["metadata"]),
            "ETag": item["etag"],
        }

    def get_object(self, *, Bucket, Key):
        try:
            item = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObject() from exc
        body = item["body"]
        if (Bucket, Key) in self.tamper_reads:
            body = b"tampered"
        return {"Body": io.BytesIO(body)}

    def put_object(self, *, Bucket, Key, Body, Metadata=None, **kwargs):
        existing = self.objects.get((Bucket, Key))
        if existing is not None and kwargs.get("IfNoneMatch") == "*":
            raise RuntimeError("precondition failed")
        if "IfMatch" in kwargs and (
            existing is None or existing["etag"] != kwargs["IfMatch"]
        ):
            raise RuntimeError("compare and swap failed")
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = {
            "body": body,
            "metadata": dict(Metadata or {}),
            "etag": self._etag(body),
        }
        self.puts.append((Bucket, Key))
        return {"ETag": self._etag(body)}


class V3BackupPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        self.signing_key = b"backup-signing-key-for-tests"
        self.connection = ConnectionView(
            public_id="connection-1",
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            region="us-east-1",
            prefix="v3",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            capabilities={"read": True, "write": True, "conditional_write": True},
            source="control_database",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, *, database=b"sqlite", media=b"pdf", epoch=1):
        workspace = self.root / f"snapshot-{uuid.uuid4()}"
        (workspace / "media" / "pdfs").mkdir(parents=True)
        (workspace / "db.sqlite3").write_bytes(database)
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(media)
        records = []
        for relative, category in (
            ("db.sqlite3", "database"),
            ("media/pdfs/one.pdf", "media"),
        ):
            body = (workspace / relative).read_bytes()
            records.append(
                {
                    "path": relative,
                    "category": category,
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        snapshot_id = uuid.uuid4()
        evidence = {
            "snapshot_id": str(snapshot_id),
            "files": records,
            "inventory": {
                "database": {"migrations": {"latest": "0027"}},
                "counts": {"pdf_rows": 242, "folders": 46, "users": 7},
            },
            "faiss": {"unavailable_documents": {"count": 0}},
            "configuration_fingerprint": {"sha256": "f" * 64},
        }
        (workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence),
            encoding="utf-8",
        )
        return SimpleNamespace(
            public_id=snapshot_id,
            workspace_path=str(workspace),
            included_epoch=epoch,
        )

    def publish(self, snapshot, recovery_point_id):
        return publish_snapshot(
            snapshot=snapshot,
            connection=self.connection,
            client=self.client,
            recovery_point_id=recovery_point_id,
            source_instance_id="stage-2026",
            source_environment="staging",
            image_digest="repo@example.invalid/app@sha256:" + "a" * 64,
            release_version="2026.08.03",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )

    def test_first_backup_uploads_all_payload_objects_and_complete_manifest(self):
        manifest, receipt = self.publish(self.snapshot(), "rp-001")
        validate_manifest(manifest)
        self.assertTrue(
            verify_manifest_signature(manifest, key=self.signing_key)
        )
        self.assertEqual(receipt.uploaded_objects, 2)
        self.assertEqual(receipt.reused_objects, 0)
        self.assertEqual(manifest["counts"]["documents"], 242)
        self.assertEqual(len(manifest["files"]), 2)
        self.assertEqual(receipt.base_manifest_sha256, "")

    def test_no_change_backup_uploads_no_payload_and_is_independently_restorable(self):
        first_manifest, _ = self.publish(self.snapshot(epoch=1), "rp-001")
        second_manifest, receipt = self.publish(self.snapshot(epoch=2), "rp-002")
        self.assertEqual(receipt.uploaded_objects, 0)
        self.assertEqual(receipt.reused_objects, 2)
        self.assertEqual(
            receipt.base_manifest_sha256,
            first_manifest["manifest_sha256"],
        )
        self.assertEqual(second_manifest["files"], first_manifest["files"])
        validate_manifest(second_manifest)

    def test_changed_incremental_uploads_only_changed_payload_object(self):
        self.publish(self.snapshot(media=b"pdf-v1", epoch=1), "rp-001")
        manifest, receipt = self.publish(
            self.snapshot(media=b"pdf-v2", epoch=2),
            "rp-002",
        )
        self.assertEqual(receipt.uploaded_objects, 1)
        self.assertEqual(receipt.reused_objects, 1)
        self.assertEqual(len(manifest["files"]), 2)

    def test_remote_blob_hash_mismatch_never_publishes_manifest_or_recovery_point(self):
        snapshot = self.snapshot()
        media_digest = hashlib.sha256(b"pdf").hexdigest()
        media_key = blob_key(self.connection, media_digest)
        self.client.tamper_reads.add((self.connection.bucket, media_key))
        with self.assertRaisesRegex(V3StorageError, "remote_object_digest_mismatch"):
            self.publish(snapshot, "rp-corrupt")
        published_keys = [key for _bucket, key in self.client.objects]
        self.assertFalse(any("/manifests/" in key for key in published_keys))
        self.assertFalse(any("/recovery-points/" in key for key in published_keys))

    def test_existing_blob_key_with_conflicting_metadata_is_fatal(self):
        digest = hashlib.sha256(b"sqlite").hexdigest()
        key = blob_key(self.connection, digest)
        self.client.objects[(self.connection.bucket, key)] = {
            "body": b"sqlite",
            "metadata": {"sha256": "0" * 64},
            "etag": "conflict",
        }
        with self.assertRaisesRegex(V3StorageError, "immutable_object_conflict"):
            self.publish(self.snapshot(), "rp-conflict")

    def test_recovery_point_collision_cannot_overwrite_immutable_receipt(self):
        self.publish(self.snapshot(epoch=1), "rp-001")
        with self.assertRaisesRegex(V3StorageError, "immutable_object_conflict"):
            self.publish(self.snapshot(media=b"changed", epoch=2), "rp-001")

    def test_static_and_control_artifacts_are_not_payload_files(self):
        snapshot = self.snapshot()
        workspace = Path(snapshot.workspace_path)
        (workspace / "staticfiles").mkdir()
        (workspace / "staticfiles" / "app.css").write_bytes(b"css")
        evidence_path = workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text())
        evidence["files"].append(
            {
                "path": "staticfiles/app.css",
                "category": "staticfiles",
                "size_bytes": 3,
                "sha256": hashlib.sha256(b"css").hexdigest(),
            }
        )
        evidence_path.write_text(json.dumps(evidence))
        manifest, receipt = self.publish(snapshot, "rp-001")
        self.assertEqual(receipt.total_objects, 2)
        self.assertFalse(
            any(item["path"].startswith("staticfiles/") for item in manifest["files"])
        )


if __name__ == "__main__":
    unittest.main()
