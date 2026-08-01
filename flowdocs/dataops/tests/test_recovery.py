import unittest

from dataops.recovery import RecoveryInspectionError, inspect_manifest, plan_v1_repack


class RecoveryInspectionTests(unittest.TestCase):
    def test_legacy_manifest_is_inspected_and_repack_is_quarantine_marked(self):
        payload = {
            "manifest_version": 1,
            "read_only": True,
            "release_id": "legacy-1",
            "source": {"dataset_id": "prod", "source_id": "source-1"},
            "files": [{"key": "datasets/prod/db.sqlite3"}],
        }
        inspected = inspect_manifest(payload)
        self.assertEqual((inspected.format_version, inspected.object_count), (1, 1))
        draft = plan_v1_repack(payload)
        self.assertEqual(draft.raw["format_version"], 2)
        self.assertTrue(draft.raw["evidence"]["hashes_pending"])

    def test_traversal_is_rejected(self):
        with self.assertRaises(RecoveryInspectionError):
            inspect_manifest({"manifest_version": 1, "read_only": True, "release_id": "x", "source": {"dataset_id": "p"}, "files": [{"key": "../secret"}]})


if __name__ == "__main__":
    unittest.main()
