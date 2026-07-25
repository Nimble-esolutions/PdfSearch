from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from core.management.commands.publish_legacy_generation import build_release_manifest


class LegacyVaultMigrationManifestTests(SimpleTestCase):
    def test_manifest_is_scoped_and_excludes_rebuildable_static_files_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "media" / "pdfs").mkdir(parents=True)
            (root / "chroma_db").mkdir()
            (root / "staticfiles").mkdir()
            (root / "media" / "pdfs" / "guide.pdf").write_bytes(b"pdf")
            (root / "chroma_db" / "embedding_metadata.json").write_text("{}")
            (root / "staticfiles" / "app.css").write_text("body{}")
            database = root / "db.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()

            snapshot = root / "snapshot.sqlite3"
            snapshot.write_bytes(database.read_bytes())
            manifest = build_release_manifest(
                root,
                database_path=snapshot,
                media_root=root / "media",
                faiss_root=root / "faiss_indexes",
                chroma_root=root / "chroma_db",
                static_root=root / "staticfiles",
                dataset_id="legacy-prod",
                generation_id="legacy-20260726T000000Z-a1b2c3d4",
                snapshot_path=snapshot,
                source_label="test-volume",
            )

            self.assertEqual(manifest["dataset_id"], "legacy-prod")
            self.assertEqual(manifest["database"]["path"], "db.sqlite3")
            self.assertFalse(manifest["static"]["included"])
            keys = {entry["object_key"] for entry in manifest["files"]}
            self.assertIn(
                "datasets/legacy-prod/generations/legacy-20260726T000000Z-a1b2c3d4/database.sqlite3",
                keys,
            )
            pdf_digest = hashlib.sha256(b"pdf").hexdigest()
            self.assertIn(
                f"datasets/legacy-prod/blobs/pdfs/sha256/{pdf_digest}.pdf",
                keys,
            )
            self.assertTrue(any(entry["artifact_type"] == "chroma" for entry in manifest["files"]))
            self.assertFalse(any(entry["artifact_type"] == "static" for entry in manifest["files"]))

    def test_static_files_can_be_explicitly_included(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "staticfiles").mkdir()
            (root / "staticfiles" / "app.css").write_text("body{}")
            database = root / "db.sqlite3"
            sqlite3.connect(database).close()
            snapshot = root / "snapshot.sqlite3"
            snapshot.write_bytes(database.read_bytes())
            manifest = build_release_manifest(
                root,
                database_path=snapshot,
                media_root=root / "media",
                faiss_root=root / "faiss_indexes",
                chroma_root=root / "chroma_db",
                static_root=root / "staticfiles",
                dataset_id="legacy-stage",
                generation_id="legacy-20260726T000001Z-a1b2c3d4",
                snapshot_path=snapshot,
                source_label="test-volume",
                include_static=True,
            )

            self.assertTrue(manifest["static"]["included"])
            self.assertTrue(any(entry["artifact_type"] == "static" for entry in manifest["files"]))
