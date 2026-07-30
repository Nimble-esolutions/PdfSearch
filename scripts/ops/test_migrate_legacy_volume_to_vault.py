from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import migrate_legacy_volume_to_vault as migration


class FakeS3Error(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, dict[str, object]] = {}
        self.version = 0
        self.fail_pointer_cas_once = False

    def put_object(self, **params):
        key = params["Key"]
        existing = self.objects.get(key)
        if params.get("IfNoneMatch") == "*" and existing is not None:
            raise FakeS3Error("PreconditionFailed")
        if "IfMatch" in params and (
            existing is None or existing["etag"] != params["IfMatch"]
        ):
            raise FakeS3Error("PreconditionFailed")
        if self.fail_pointer_cas_once and key.endswith("/control/authoritative.json"):
            self.fail_pointer_cas_once = False
            raise FakeS3Error("PreconditionFailed")
        body = params["Body"]
        data = body.read() if hasattr(body, "read") else bytes(body)
        self.version += 1
        etag = f'"etag-{self.version}"'
        self.objects[key] = {
            "body": data,
            "metadata": dict(params.get("Metadata") or {}),
            "etag": etag,
            "content_type": params.get("ContentType", ""),
        }
        return {"ETag": etag}

    def get_object(self, *, Bucket, Key):
        del Bucket
        try:
            value = self.objects[Key]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {"Body": io.BytesIO(value["body"]), "ETag": value["etag"]}

    def head_object(self, *, Bucket, Key):
        del Bucket
        try:
            value = self.objects[Key]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {
            "ContentLength": len(value["body"]),
            "Metadata": dict(value["metadata"]),
            "ETag": value["etag"],
        }

    def copy_object(self, **params):
        source = self.objects[params["CopySource"]["Key"]]
        if (
            params.get("CopySourceIfMatch")
            and params["CopySourceIfMatch"] != source["etag"]
        ):
            raise FakeS3Error("PreconditionFailed")
        return self.put_object(
            Bucket=params["Bucket"],
            Key=params["Key"],
            Body=source["body"],
            ContentType=params.get("ContentType", ""),
            Metadata=params.get("Metadata", {}),
            IfNoneMatch="*",
        )

    def delete_object(self, *, Bucket, Key):
        del Bucket
        self.objects.pop(Key, None)
        return {}


def make_source(root: Path) -> None:
    (root / "media").mkdir()
    (root / "faiss_indexes").mkdir()
    (root / "pdf_cache").mkdir()
    (root / "staticfiles").mkdir()
    (root / "media" / "नियम.pdf").write_bytes(b"pdf")
    (root / "faiss_indexes" / "folder_1.index").write_bytes(b"index")
    (root / "pdf_cache" / "ocr.json").write_text("{}")
    (root / "staticfiles" / "app.css").write_text("body{}")
    connection = sqlite3.connect(root / "db.sqlite3")
    connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO sample (value) VALUES ('stable')")
    connection.commit()
    connection.close()


def migration_args(
    root: Path | None,
    checkpoint: Path | None,
    *,
    publish: bool = False,
    promote_generation: str | None = None,
    register_dataset: bool = False,
) -> argparse.Namespace:
    generation_id = "legacy-20260726T000000Z-a1b2c3d4"
    return argparse.Namespace(
        source_root=root,
        database=None,
        dataset_id="ai-sahakar-prod",
        bucket="test-bucket",
        production_source_id="ai-sahakar-prod",
        app_release="e9d4d8c4870cced6994d5138dbe6feaf2689548c",
        image_digest="sha256:" + "1" * 64,
        generation_id=generation_id,
        source_label="read-only-test-source",
        output=None,
        checkpoint=checkpoint,
        include_backups=False,
        include_static=False,
        publish=publish,
        repack_authoritative=False,
        repair_registration_metadata=False,
        candidate_only=False,
        register_dataset=register_dataset,
        promote_generation=promote_generation,
        confirm_promotion=(
            f"ai-sahakar-prod:{promote_generation}" if promote_generation else ""
        ),
        writer_ttl_seconds=120,
    )


class LegacyVolumeInventoryTests(unittest.TestCase):
    def test_inventory_captures_runtime_trees_and_static_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_source(root)
            snapshot = root / "snapshot.sqlite3"
            migration.snapshot_sqlite(root / "db.sqlite3", snapshot)

            manifest = migration.inventory_source(
                root,
                snapshot,
                "legacy-20260726T000000Z-a1b2c3d4",
                "ai-sahakar-prod",
                False,
                app_release="e9d4d8c4870cced6994d5138dbe6feaf2689548c",
                image_digest="sha256:" + "1" * 64,
            )

            self.assertEqual(manifest["counts"]["files"], 4)
            self.assertEqual(manifest["counts"]["staticfiles"], 0)
            pdf = next(
                item
                for item in manifest["files"]
                if item["path"].endswith("नियम.pdf")
            )
            self.assertTrue(
                pdf["object_key"].startswith(
                    "datasets/ai-sahakar-prod/blobs/pdfs/sha256/"
                )
            )
            self.assertNotIn("नियम", pdf["object_key"])

            with_static = migration.inventory_source(
                root,
                snapshot,
                "legacy-20260726T000000Z-a1b2c3d4",
                "ai-sahakar-prod",
                False,
                include_static=True,
                app_release="e9d4d8c4870cced6994d5138dbe6feaf2689548c",
                image_digest="sha256:" + "1" * 64,
            )
            self.assertEqual(with_static["counts"]["files"], 5)
            self.assertEqual(with_static["counts"]["staticfiles"], 1)

    def test_stable_snapshot_detects_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            workspace = Path(temp_dir) / "workspace"
            root.mkdir()
            workspace.mkdir()
            make_source(root)
            original = migration.snapshot_sqlite

            def mutate_after_database_snapshot(source, destination):
                original(source, destination)
                (root / "media" / "नियम.pdf").write_bytes(b"changed")

            with mock.patch.object(
                migration,
                "snapshot_sqlite",
                side_effect=mutate_after_database_snapshot,
            ):
                with self.assertRaisesRegex(
                    migration.SourceChangedError, "consistent_snapshot_unproven"
                ):
                    migration.create_stable_snapshot(
                        root,
                        root / "db.sqlite3",
                        workspace,
                        include_backups=False,
                        include_static=False,
                    )

    def test_manifest_validation_rejects_cross_dataset_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_source(root)
            snapshot = root / "snapshot.sqlite3"
            migration.snapshot_sqlite(root / "db.sqlite3", snapshot)
            generation = "legacy-20260726T000000Z-a1b2c3d4"
            manifest = migration.inventory_source(
                root,
                snapshot,
                generation,
                "ai-sahakar-prod",
                False,
                app_release="e9d4d8c4870cced6994d5138dbe6feaf2689548c",
                image_digest="sha256:" + "1" * 64,
            )
            manifest["files"][1]["object_key"] = (
                "datasets/another-dataset/blobs/files/" + "a" * 64
            )
            with self.assertRaisesRegex(
                migration.MigrationError, "escapes dataset"
            ):
                migration.validate_manifest(
                    manifest, "ai-sahakar-prod", generation
                )

    def test_registration_validation_requires_app_source_and_schema(self):
        registration = json.loads(
            migration.registration_payload(
                "ai-sahakar-prod", "ai-sahakar-prod", "test"
            )
        )
        migration.validate_registration(
            registration, "ai-sahakar-prod", "ai-sahakar-prod"
        )
        registration["production_source_id"] = "wrong-source"
        with self.assertRaisesRegex(migration.MigrationError, "source"):
            migration.validate_registration(
                registration, "ai-sahakar-prod", "ai-sahakar-prod"
            )


class CandidatePublicationTests(unittest.TestCase):
    def test_remote_verification_accepts_rustfs_metadata_casing(self):
        client = FakeS3()
        body = b"stable"
        digest = hashlib.sha256(body).hexdigest()
        client.objects["blob"] = {
            "body": body,
            "metadata": {"Sha256": digest, "Immutable": "true"},
            "etag": '"etag-1"',
            "content_type": "application/octet-stream",
        }

        migration.verify_remote_object(
            client,
            "vault",
            "blob",
            digest,
            len(body),
        )

    def test_publish_is_candidate_only_and_retry_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            make_source(root)
            checkpoint = Path(temp_dir) / "checkpoint.json"
            client = FakeS3()
            args = migration_args(
                root, checkpoint, publish=True, register_dataset=True
            )

            with mock.patch.object(migration, "s3_client", return_value=client):
                first = migration.migrate(args)
                second = migration.migrate(args)

            self.assertTrue(first["candidate_published"])
            self.assertFalse(first["pointer_updated"])
            self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
            self.assertGreater(second["already_present"], 0)
            self.assertNotIn(
                migration.pointer_key(args.dataset_id), client.objects
            )
            manifest_body = client.objects[
                migration.manifest_key(args.dataset_id, args.generation_id)
            ]["body"]
            self.assertEqual(
                hashlib.sha256(manifest_body).hexdigest(),
                first["manifest_sha256"],
            )

    def test_retry_rejects_changed_source_before_network_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            make_source(root)
            checkpoint = Path(temp_dir) / "checkpoint.json"
            client = FakeS3()
            args = migration_args(
                root, checkpoint, publish=True, register_dataset=True
            )
            with mock.patch.object(migration, "s3_client", return_value=client):
                migration.migrate(args)
            object_count = len(client.objects)
            (root / "media" / "नियम.pdf").write_bytes(b"new-content")

            with mock.patch.object(migration, "s3_client", return_value=client):
                with self.assertRaisesRegex(
                    migration.MigrationError, "checkpoint_manifest_mismatch"
                ):
                    migration.migrate(args)
            self.assertEqual(len(client.objects), object_count)

    def test_strict_repack_repairs_registration_and_copies_database_blob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            make_source(root)
            checkpoint = Path(temp_dir) / "checkpoint.json"
            client = FakeS3()
            args = migration_args(
                root, checkpoint, publish=True, register_dataset=True
            )
            with mock.patch.object(migration, "s3_client", return_value=client):
                migration.migrate(args)

            source_manifest_key = migration.manifest_key(
                args.dataset_id, args.generation_id
            )
            source_manifest = json.loads(
                client.objects[source_manifest_key]["body"]
            )
            source_manifest.pop("app_release")
            source_manifest.pop("image_digest")
            database = next(
                entry
                for entry in source_manifest["files"]
                if entry["path"] == "db.sqlite3"
            )
            current_database_key = database["object_key"]
            legacy_database_key = (
                f"datasets/{args.dataset_id}/generations/"
                f"{args.generation_id}/database.sqlite3"
            )
            client.objects[legacy_database_key] = client.objects.pop(
                current_database_key
            )
            database["object_key"] = legacy_database_key
            source_manifest["database"]["entry"] = database
            source_body = migration.canonical_json(source_manifest)
            client.put_object(
                Bucket=args.bucket,
                Key=source_manifest_key,
                Body=source_body,
                Metadata={"sha256": hashlib.sha256(source_body).hexdigest()},
            )
            pointer = {
                "dataset_id": args.dataset_id,
                "generation_id": args.generation_id,
                "manifest_object_key": source_manifest_key,
                "manifest_sha256": hashlib.sha256(source_body).hexdigest(),
                "production_source_id": args.production_source_id,
                "writer_epoch": 0,
            }
            client.put_object(
                Bucket=args.bucket,
                Key=migration.pointer_key(args.dataset_id),
                Body=migration.canonical_json(pointer),
                Metadata={"sha256": "legacy"},
            )
            registration = client.objects[
                migration.registration_key(args.dataset_id)
            ]
            registration["metadata"] = {}

            repack_args = migration_args(None, None)
            repack_args.generation_id = "repacked-20260727T000000Z-a1b2c3d4"
            repack_args.repack_authoritative = True
            repack_args.repair_registration_metadata = True
            with mock.patch.object(migration, "s3_client", return_value=client):
                result = migration.migrate(repack_args)

            self.assertTrue(result["candidate_published"])
            self.assertFalse(result["pointer_updated"])
            self.assertIn(current_database_key, client.objects)
            repacked_manifest = json.loads(
                client.objects[
                    migration.manifest_key(
                        repack_args.dataset_id,
                        repack_args.generation_id,
                    )
                ]["body"]
            )
            self.assertEqual(
                repacked_manifest["app_release"],
                repack_args.app_release,
            )
            self.assertEqual(
                repacked_manifest["image_digest"],
                repack_args.image_digest,
            )
            registration_head = client.head_object(
                Bucket=args.bucket,
                Key=migration.registration_key(args.dataset_id),
            )
            self.assertRegex(
                registration_head["Metadata"]["sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertTrue(
                any(
                    "/control/recovery/registration/" in key
                    for key in client.objects
                )
            )

    def test_publication_requires_durable_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_source(root)
            args = migration_args(root, None, publish=True)
            with self.assertRaisesRegex(
                migration.MigrationError, "--checkpoint is required"
            ):
                migration.migrate(args)


class PromotionTests(unittest.TestCase):
    def _published_candidate(self, temp_dir: str) -> tuple[FakeS3, argparse.Namespace]:
        root = Path(temp_dir) / "source"
        root.mkdir()
        make_source(root)
        checkpoint = Path(temp_dir) / "checkpoint.json"
        client = FakeS3()
        args = migration_args(
            root, checkpoint, publish=True, register_dataset=True
        )
        with mock.patch.object(migration, "s3_client", return_value=client):
            migration.migrate(args)
        return client, args

    def test_explicit_promotion_verifies_and_fences_pointer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, publish_args = self._published_candidate(temp_dir)
            promote_args = migration_args(
                None,
                None,
                promote_generation=publish_args.generation_id,
            )
            with mock.patch.object(migration, "s3_client", return_value=client):
                result = migration.migrate(promote_args)

            self.assertTrue(result["pointer_updated"])
            self.assertTrue(result["writer_released"])
            pointer = json.loads(
                client.objects[migration.pointer_key(promote_args.dataset_id)][
                    "body"
                ]
            )
            self.assertEqual(pointer["generation_id"], publish_args.generation_id)
            self.assertEqual(pointer["writer_epoch"], 1)
            writer = json.loads(
                client.objects[migration.writer_key(promote_args.dataset_id)][
                    "body"
                ]
            )
            self.assertLessEqual(writer["expires_at"], time.time())

    def test_pointer_cas_race_leaves_candidate_intact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client, publish_args = self._published_candidate(temp_dir)
            client.fail_pointer_cas_once = True
            promote_args = migration_args(
                None,
                None,
                promote_generation=publish_args.generation_id,
            )
            with mock.patch.object(migration, "s3_client", return_value=client):
                with self.assertRaisesRegex(
                    migration.MigrationError, "pointer changed"
                ):
                    migration.migrate(promote_args)
            self.assertIn(
                migration.manifest_key(
                    publish_args.dataset_id, publish_args.generation_id
                ),
                client.objects,
            )
            self.assertNotIn(
                migration.pointer_key(promote_args.dataset_id), client.objects
            )

    def test_safe_release_cannot_expire_a_successor(self):
        client = FakeS3()
        writer = migration.acquire_writer(
            client,
            "test-bucket",
            "ai-sahakar-prod",
            "ai-sahakar-prod",
            ttl_seconds=120,
        )
        key = migration.writer_key("ai-sahakar-prod")
        successor = json.loads(client.objects[key]["body"])
        successor["owner_token_hash"] = "f" * 64
        successor["writer_epoch"] = 2
        successor["expires_at"] = time.time() + 120
        client.put_object(
            Bucket="test-bucket",
            Key=key,
            Body=migration.canonical_json(successor),
            Metadata={"sha256": "unused"},
        )

        released = migration.release_writer(
            client,
            "test-bucket",
            "ai-sahakar-prod",
            "ai-sahakar-prod",
            writer,
        )

        self.assertFalse(released)
        stored = json.loads(client.objects[key]["body"])
        self.assertEqual(stored["writer_epoch"], 2)
        self.assertGreater(stored["expires_at"], time.time())


if __name__ == "__main__":
    unittest.main()
