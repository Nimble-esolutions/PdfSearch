"""Read-only legacy S3 to canonical DataOps v3 import tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dataops.package_v3 import validate_manifest, verify_manifest_signature
from dataops.tests.test_v3_backup import FakeS3
from dataops.v3_config import ConnectionView
from dataops.v3_legacy import (
    V3LegacyImportError,
    discover_legacy_generations,
    import_legacy_generation,
    legacy_manifest_key,
    load_legacy_generation,
    materialize_legacy_generation,
)


class ListingFakeS3(FakeS3):
    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        del ContinuationToken
        keys = sorted(
            key
            for bucket, key in self.objects
            if bucket == Bucket and key.startswith(Prefix)
        )
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }


class LegacyV3ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = ListingFakeS3()
        self.destination = FakeS3()
        self.source_bucket = "legacy-production"
        self.source_dataset = "ai-sahakar-prod-v2"
        self.generation_id = "legacy-20260802T085639Z-86288855"
        self.signing_key = b"legacy-import-signing-key"
        self.connection = ConnectionView(
            public_id="connection-stage",
            name="Stage recovery storage",
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
        database_path = self.root / "legacy.sqlite3"
        with sqlite3.connect(database_path) as db:
            db.execute("CREATE TABLE core_pdffile (id INTEGER PRIMARY KEY)")
            db.executemany("INSERT INTO core_pdffile DEFAULT VALUES", [(), ()])
            db.execute("CREATE TABLE core_folder (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO core_folder DEFAULT VALUES")
            db.execute("CREATE TABLE auth_user (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO auth_user DEFAULT VALUES")
            db.execute("CREATE TABLE django_migrations (app TEXT, name TEXT)")
            db.execute("INSERT INTO django_migrations VALUES ('core', '0029_latest')")
        payloads = {
            "db.sqlite3": ("database", database_path.read_bytes()),
            "media/pdfs/one.pdf": ("media", b"%PDF-one"),
            "media/pdfs/two.pdf": ("media", b"%PDF-two"),
            "faiss_indexes/index.faiss": ("faiss_indexes", b"faiss-derived"),
        }
        files = []
        for path, (artifact_type, body) in payloads.items():
            digest = hashlib.sha256(body).hexdigest()
            key = (
                f"datasets/{self.source_dataset}/blobs/pdfs/sha256/{digest}.pdf"
                if path.startswith("media/") and path.endswith(".pdf")
                else f"datasets/{self.source_dataset}/blobs/files/{digest}"
            )
            self._put(self.source, self.source_bucket, key, body)
            files.append(
                {
                    "path": path,
                    "bytes": len(body),
                    "sha256": digest,
                    "object_key": key,
                    "artifact_type": artifact_type,
                }
            )
        self.manifest = {
            "manifest_version": 1,
            "read_only": True,
            "release_id": self.generation_id,
            "dataset_id": self.source_dataset,
            "production_source_id": "ai-sahakar-prod",
            "app_release": "legacy-volume-port",
            "image_digest": "sha256:" + "a" * 64,
            "created_at": "2026-08-02T08:56:39+00:00",
            "source": {
                "kind": "legacy-data-root",
                "root_contract": "read-only-volume",
                "snapshot_evidence": {
                    "algorithm": "two-scan-copy-plus-sqlite-backup/v1",
                    "stable": True,
                },
            },
            "database": {"migrations": {"latest": "0029_latest"}},
            "embedding_index": {"model": "text-embedding-3-small"},
            "runtime_trees": ["media", "faiss_indexes", "chroma_db", "pdf_cache"],
            "files": files,
        }
        self._publish_manifest(self.manifest)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _put(client, bucket, key, body):
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            Metadata={"sha256": hashlib.sha256(body).hexdigest()},
        )

    def _publish_manifest(self, manifest):
        body = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        self._put(
            self.source,
            self.source_bucket,
            legacy_manifest_key(self.source_dataset, self.generation_id),
            body,
        )
        return body

    def load(self):
        return load_legacy_generation(
            self.source,
            bucket=self.source_bucket,
            dataset_id=self.source_dataset,
            generation_id=self.generation_id,
        )

    def test_discovers_then_imports_explicit_generation_without_mutating_source(self):
        discovered = discover_legacy_generations(
            self.source,
            bucket=self.source_bucket,
            dataset_id=self.source_dataset,
        )
        self.assertEqual(
            [item["generation_id"] for item in discovered],
            [self.generation_id],
        )
        generation = self.load()
        source_before = {
            key: {name: value for name, value in record.items()}
            for key, record in self.source.objects.items()
        }
        materialized = materialize_legacy_generation(
            generation,
            client=self.source,
            quarantine_root=self.root / "quarantine",
        )
        manifest, receipt = import_legacy_generation(
            generation=generation,
            materialization=materialized,
            destination=self.connection,
            destination_client=self.destination,
            recovery_point_id="import-legacy-prod-001",
            destination_instance_id="stage-2026",
            destination_environment="staging",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )
        validate_manifest(manifest)
        self.assertTrue(verify_manifest_signature(manifest, key=self.signing_key))
        self.assertEqual(manifest["lineage"]["transition"], "legacy_import")
        self.assertEqual(
            manifest["lineage"]["parent_manifest_sha256"],
            generation.manifest_sha256,
        )
        self.assertEqual(manifest["counts"]["documents"], 2)
        self.assertEqual(manifest["counts"]["folders"], 1)
        self.assertEqual(manifest["counts"]["users"], 1)
        self.assertTrue(manifest["components"]["faiss"]["rebuild_required"])
        self.assertFalse(receipt.dataset_id == self.source_dataset)
        self.assertEqual(self.source.objects, source_before)

    def test_materialization_and_destination_publication_are_idempotent(self):
        generation = self.load()
        first_materialization = materialize_legacy_generation(
            generation,
            client=self.source,
            quarantine_root=self.root / "quarantine",
        )
        second_materialization = materialize_legacy_generation(
            generation,
            client=self.source,
            quarantine_root=self.root / "quarantine",
        )
        self.assertFalse(first_materialization["reused"])
        self.assertTrue(second_materialization["reused"])
        arguments = {
            "generation": generation,
            "materialization": second_materialization,
            "destination": self.connection,
            "destination_client": self.destination,
            "recovery_point_id": "import-legacy-prod-001",
            "destination_instance_id": "stage-2026",
            "destination_environment": "staging",
            "signing_key": self.signing_key,
            "signing_key_id": "stage-manifest-1",
        }
        import_legacy_generation(**arguments)
        _, retry = import_legacy_generation(**arguments)
        self.assertEqual(retry.uploaded_objects, 0)
        self.assertEqual(retry.reused_objects, 4)

    def test_corrupt_source_object_is_retained_and_nothing_is_published(self):
        generation = self.load()
        media = next(
            item for item in generation.manifest["files"] if item["kind"] == "media"
        )
        self.source.tamper_reads.add((self.source_bucket, media["object_key"]))
        with self.assertRaisesRegex(
            V3LegacyImportError, "legacy_object_digest_mismatch"
        ):
            materialize_legacy_generation(
                generation,
                client=self.source,
                quarantine_root=self.root / "quarantine",
            )
        self.assertEqual(self.destination.objects, {})
        self.assertTrue(
            any(
                path.name.startswith(f"failed-{self.generation_id}-")
                for path in (self.root / "quarantine").iterdir()
            )
        )

    def test_unknown_legacy_artifact_type_is_refused(self):
        key = legacy_manifest_key(self.source_dataset, self.generation_id)
        self.source.objects.pop((self.source_bucket, key))
        self.manifest["files"][0]["artifact_type"] = "backups"
        self._publish_manifest(self.manifest)
        with self.assertRaisesRegex(
            V3LegacyImportError, "legacy_artifact_type_unsupported"
        ):
            self.load()

    def test_dataset_mismatch_is_refused_before_payload_download(self):
        with self.assertRaisesRegex(
            V3LegacyImportError, "legacy_manifest_read_failed"
        ):
            load_legacy_generation(
                self.source,
                bucket=self.source_bucket,
                dataset_id="wrong-dataset",
                generation_id=self.generation_id,
            )


if __name__ == "__main__":
    unittest.main()
