"""Full-manifest/incremental-transfer publication tests for DataOps v3."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase

from dataops.models import DataConnection, RecoveryPoint
from dataops.package_v3 import (
    canonical_json_bytes,
    validate_manifest,
    verify_manifest_signature,
)
from dataops.v3_backup import V3BackupError, blob_key, publish_snapshot
from dataops.v3_config import connection_from_model
from dataops.v3_restore import load_verified_recovery_point, materialize_quarantine
from dataops.v3_storage import V3StorageError


class MissingObject(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.tamper_reads = set()
        self.before_put = None

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
        if self.before_put is not None:
            self.before_put(Bucket=Bucket, Key=Key, Body=Body, kwargs=kwargs)
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


class V3BackupPublicationTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        self.signing_key = b"backup-signing-key-for-tests"
        self.connection_model = DataConnection.objects.using("control").create(
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        self.connection = connection_from_model(self.connection_model)
        database = self.root / "fixture.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE core_pdffile "
                "(indexed INTEGER, processing_status TEXT)"
            )
            db.execute("INSERT INTO core_pdffile VALUES (1, 'ready')")
            db.execute("CREATE TABLE core_folder (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO core_folder VALUES (1)")
            db.execute("CREATE TABLE core_customuser (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO core_customuser VALUES (1)")
            db.execute(
                "CREATE TABLE django_migrations "
                "(app TEXT, name TEXT, applied TEXT)"
            )
            db.execute(
                "INSERT INTO django_migrations VALUES "
                "('core', '0027', '2026-08-03T00:00:00Z')"
            )
        self.database_bytes = database.read_bytes()

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, *, database=None, media=b"pdf", chroma=None, epoch=1):
        workspace = self.root / f"snapshot-{uuid.uuid4()}"
        (workspace / "media" / "pdfs").mkdir(parents=True)
        (workspace / "db.sqlite3").write_bytes(database or self.database_bytes)
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(media)
        record_specs = [
            ("db.sqlite3", "database"),
            ("media/pdfs/one.pdf", "media"),
        ]
        if chroma is not None:
            (workspace / "chroma_db").mkdir()
            (workspace / "chroma_db" / "chroma.sqlite3").write_bytes(chroma)
            record_specs.append(("chroma_db/chroma.sqlite3", "chroma_db"))
        records = []
        for relative, category in record_specs:
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
            "included_epoch": epoch,
            "source_stable": True,
            "consistency": {
                "sqlite_integrity": "ok",
                "foreign_keys": "ok",
            },
            "files": records,
            "inventory": {
                "database": {
                    "migrations": {"latest": "core.0027", "count": 1}
                },
                "counts": {"pdf_rows": 1, "folders": 1, "users": 1},
            },
            "faiss": {"unavailable_documents": {"count": 0}},
            "configuration_fingerprint": {"sha256": "f" * 64},
        }
        evidence["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(evidence)
        ).hexdigest()
        (workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence),
            encoding="utf-8",
        )
        return SimpleNamespace(
            public_id=snapshot_id,
            workspace_path=str(workspace),
            included_epoch=epoch,
            evidence_sha256=evidence["evidence_sha256"],
        )

    def rewrite_evidence(self, snapshot, mutator):
        evidence_path = Path(snapshot.workspace_path) / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence.pop("evidence_sha256")
        mutator(evidence)
        evidence["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(evidence)
        ).hexdigest()
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        snapshot.evidence_sha256 = evidence["evidence_sha256"]

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
        self.assertEqual(
            manifest["counts"],
            {
                "documents": 1,
                "folders": 1,
                "users": 1,
                "migrations": 1,
                "objects": 2,
                "bytes": sum(item["size"] for item in manifest["files"]),
            },
        )
        self.assertEqual(len(manifest["files"]), 2)
        self.assertEqual(receipt.base_manifest_sha256, "")
        self.assertEqual(receipt.total_objects, manifest["counts"]["objects"])
        self.assertEqual(receipt.total_bytes, manifest["counts"]["bytes"])
        self.assertEqual(
            manifest["components"]["chroma"],
            {
                "complete": True,
                "coherent": True,
                "rebuild_required": False,
            },
        )

    def test_existing_chroma_payload_remains_fail_closed_without_coherence_proof(self):
        for suffix, payload in (("content", b"legacy-chroma"), ("empty", b"")):
            with self.subTest(payload=suffix):
                self.client = FakeS3()
                manifest, receipt = self.publish(
                    self.snapshot(chroma=payload),
                    f"rp-with-chroma-{suffix}",
                )
                self.assertEqual(receipt.total_objects, 3)
                self.assertEqual(
                    manifest["components"]["chroma"],
                    {
                        "complete": True,
                        "coherent": False,
                        "rebuild_required": True,
                    },
                )

    def test_unlisted_chroma_payload_blocks_publication(self):
        snapshot = self.snapshot()
        workspace = Path(snapshot.workspace_path)
        (workspace / "chroma_db").mkdir()
        (workspace / "chroma_db" / "unlisted.sqlite3").write_bytes(b"state")

        with self.assertRaisesRegex(
            V3BackupError,
            "snapshot_chroma_inventory_mismatch",
        ):
            self.publish(snapshot, "rp-unlisted-chroma")
        self.assertEqual(self.client.objects, {})

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
        point = RecoveryPoint.objects.using("control").create(
            connection=self.connection_model,
            dataset_id=self.connection.dataset_id,
            release_id=receipt.recovery_point_id,
            format_version=3,
            prefix=receipt.recovery_point_key,
            manifest_digest=receipt.manifest_sha256,
            signature_key_id="stage-manifest-1",
            data_complete=True,
            state=RecoveryPoint.State.VERIFIED,
            counts=second_manifest["counts"],
        )
        verified = load_verified_recovery_point(
            point,
            signing_key=self.signing_key,
            client_factory=lambda _connection: self.client,
        )
        restored = materialize_quarantine(
            verified,
            quarantine_root=self.root / "restore-quarantine",
        )
        restored_root = Path(restored["workspace"])
        self.assertTrue(restored["verified"])
        self.assertEqual(restored["evidence"]["documents"], 1)
        self.assertEqual(restored["evidence"]["indexing_ratio"], 1.0)
        self.assertEqual(
            (restored_root / "media" / "pdfs" / "one.pdf").read_bytes(),
            b"pdf",
        )
        self.assertTrue((restored_root / "faiss_indexes").is_dir())
        self.assertTrue((restored_root / "chroma_db").is_dir())

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
        digest = hashlib.sha256(self.database_bytes).hexdigest()
        key = blob_key(self.connection, digest)
        self.client.objects[(self.connection.bucket, key)] = {
            "body": self.database_bytes,
            "metadata": {"sha256": "0" * 64},
            "etag": "conflict",
        }
        with self.assertRaisesRegex(V3StorageError, "immutable_object_conflict"):
            self.publish(self.snapshot(), "rp-conflict")

    def test_recovery_point_collision_cannot_overwrite_immutable_receipt(self):
        self.publish(self.snapshot(epoch=1), "rp-001")
        with self.assertRaisesRegex(V3BackupError, "recovery_point_retry_conflict"):
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
        evidence.pop("evidence_sha256")
        evidence["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(evidence)
        ).hexdigest()
        snapshot.evidence_sha256 = evidence["evidence_sha256"]
        evidence_path.write_text(json.dumps(evidence))
        manifest, receipt = self.publish(snapshot, "rp-001")
        self.assertEqual(receipt.total_objects, 2)
        self.assertFalse(
            any(item["path"].startswith("staticfiles/") for item in manifest["files"])
        )

    def test_signed_inventory_must_match_sqlite_before_any_remote_write(self):
        mutations = {
            "snapshot_document_count_mismatch": lambda evidence: evidence[
                "inventory"
            ]["counts"].update(pdf_rows=242),
            "snapshot_folder_count_mismatch": lambda evidence: evidence[
                "inventory"
            ]["counts"].update(folders=46),
            "snapshot_user_count_mismatch": lambda evidence: evidence[
                "inventory"
            ]["counts"].update(users=7),
            "snapshot_migration_mismatch": lambda evidence: evidence[
                "inventory"
            ]["database"]["migrations"].update(latest="core.9999"),
            "snapshot_migration_count_mismatch": lambda evidence: evidence[
                "inventory"
            ]["database"]["migrations"].update(count=29),
        }
        for code, mutation in mutations.items():
            with self.subTest(code=code):
                snapshot = self.snapshot()
                self.rewrite_evidence(snapshot, mutation)
                self.client.objects.clear()
                self.client.puts.clear()
                with self.assertRaisesRegex(V3BackupError, code):
                    self.publish(snapshot, f"rp-{code}")
                self.assertEqual(self.client.objects, {})
                self.assertEqual(self.client.puts, [])

    def test_pdf_object_set_must_match_sqlite_before_any_remote_write(self):
        snapshot = self.snapshot()
        self.rewrite_evidence(
            snapshot,
            lambda evidence: evidence.__setitem__(
                "files",
                [item for item in evidence["files"] if item["category"] != "media"],
            ),
        )
        with self.assertRaisesRegex(
            V3BackupError,
            "snapshot_pdf_object_count_mismatch",
        ):
            self.publish(snapshot, "rp-missing-pdf")
        self.assertEqual(self.client.objects, {})

    def test_retry_after_pointer_failure_reuses_identical_manifest(self):
        snapshot = self.snapshot(epoch=1_754_184_000_000_000_000)
        failed_manifest_digest = None

        def fail_pointer_once(*, Key, **_kwargs):
            nonlocal failed_manifest_digest
            if Key.endswith("/refs/latest.json"):
                manifest_keys = [
                    key
                    for bucket, key in self.client.objects
                    if bucket == self.connection.bucket and "/manifests/" in key
                ]
                failed_manifest_digest = manifest_keys[0].rsplit("/", 1)[-1][:-5]
                self.client.before_put = None
                raise RuntimeError("simulated worker death before pointer CAS")

        self.client.before_put = fail_pointer_once
        with self.assertRaisesRegex(V3BackupError, "latest_pointer_conflict"):
            self.publish(snapshot, "rp-retry")
        manifest, receipt = self.publish(snapshot, "rp-retry")
        self.assertEqual(receipt.manifest_sha256, failed_manifest_digest)
        self.assertEqual(manifest["manifest_sha256"], failed_manifest_digest)
        self.assertEqual(receipt.uploaded_objects, 0)
        self.assertEqual(receipt.reused_objects, 2)

    def test_retry_after_pointer_publication_is_an_exact_noop(self):
        snapshot = self.snapshot(epoch=1_754_184_000_000_000_000)
        first_manifest, first_receipt = self.publish(snapshot, "rp-retry")
        puts_before_retry = list(self.client.puts)
        second_manifest, second_receipt = self.publish(snapshot, "rp-retry")
        self.assertEqual(second_manifest, first_manifest)
        self.assertEqual(second_receipt.manifest_sha256, first_receipt.manifest_sha256)
        self.assertEqual(second_receipt.reused_objects, 2)
        self.assertEqual(self.client.puts, puts_before_retry)

    def test_latest_pointer_cas_is_fenced_to_original_observation(self):
        self.publish(self.snapshot(epoch=1), "rp-base")
        latest_key = (
            f"v3/datasets/{self.connection.dataset_id}/refs/latest.json"
        )
        interleaved = canonical_json_bytes(
            {
                "schema_version": 3,
                "dataset_id": self.connection.dataset_id,
                "recovery_point_id": "rp-interleaved",
                "manifest_sha256": "b" * 64,
                "recovery_point_key": (
                    f"v3/datasets/{self.connection.dataset_id}/"
                    "recovery-points/rp-interleaved.json"
                ),
            }
        )

        def interleave_writer(*, Bucket, Key, **_kwargs):
            if Key == latest_key:
                self.client.before_put = None
                self.client.objects[(Bucket, Key)] = {
                    "body": interleaved,
                    "metadata": {
                        "sha256": hashlib.sha256(interleaved).hexdigest()
                    },
                    "etag": self.client._etag(interleaved),
                }

        self.client.before_put = interleave_writer
        with self.assertRaisesRegex(V3BackupError, "latest_pointer_conflict"):
            self.publish(self.snapshot(media=b"changed", epoch=2), "rp-target")
        self.assertEqual(
            self.client.objects[(self.connection.bucket, latest_key)]["body"],
            interleaved,
        )


if __name__ == "__main__":
    unittest.main()
