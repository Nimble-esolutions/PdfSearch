from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from migrate_legacy_volume_to_vault import inventory_source, object_key


class LegacyVolumeInventoryTests(unittest.TestCase):
    def test_inventory_captures_runtime_trees_and_safe_digest_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "media").mkdir()
            (root / "faiss_indexes").mkdir()
            (root / "pdf_cache").mkdir()
            (root / "staticfiles").mkdir()
            (root / "media" / "नियम.pdf").write_bytes(b"pdf")
            (root / "faiss_indexes" / "folder_1.index").write_bytes(b"index")
            (root / "pdf_cache" / "ocr.json").write_text("{}")
            (root / "staticfiles" / "app.css").write_text("body{}")
            database = root / "db.sqlite3"
            sqlite3.connect(database).close()
            snapshot = root / "snapshot.sqlite3"
            snapshot.write_bytes(database.read_bytes())

            manifest = inventory_source(
                root,
                snapshot,
                "legacy-20260726T000000Z-a1b2c3d4",
                "ai-sahakar-prod",
                False,
            )

            self.assertEqual(manifest["counts"]["files"], 5)
            pdf = next(item for item in manifest["files"] if item["path"].endswith("नियम.pdf"))
            self.assertTrue(pdf["object_key"].startswith("datasets/ai-sahakar-prod/blobs/pdfs/sha256/"))
            self.assertNotIn("नियम", pdf["object_key"])
            self.assertEqual(
                object_key("ai-sahakar-prod", "gen-1", "db.sqlite3", "a" * 64),
                "datasets/ai-sahakar-prod/generations/gen-1/database.sqlite3",
            )


if __name__ == "__main__":
    unittest.main()
