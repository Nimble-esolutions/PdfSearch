"""Recovery-point manifest v3 contract tests."""

from __future__ import annotations

from copy import deepcopy
import unittest

from dataops.package_v3 import (
    ManifestV3Error,
    build_manifest,
    manifest_digest,
    sign_manifest,
    validate_manifest,
    verify_manifest_signature,
)


class ManifestV3Tests(unittest.TestCase):
    def setUp(self):
        self.key = b"manifest-test-key-not-for-production"
        self.file_digest = "a" * 64
        self.manifest = build_manifest(
            recovery_point_id="rp-20260803-001",
            dataset_id="ai-sahakar-stage-2026",
            source_instance_id="stage-2026",
            source_environment="staging",
            backup_mode="smart",
            captured_epoch=17,
            created_at="2026-08-03T12:00:00Z",
            application={
                "image_digest": "repo@example.invalid/app@sha256:" + "b" * 64,
                "release_version": "2026.08.03",
                "database_schema": "core:0029",
                "index_configuration_sha256": "c" * 64,
            },
            consistency={
                "sqlite_integrity": "ok",
                "foreign_keys": "ok",
                "source_stable": True,
            },
            components={
                "database": {"complete": True, "coherent": True},
                "media": {"complete": True, "coherent": True},
                "pdf_cache": {"complete": False, "rebuild_required": True},
                "faiss": {"complete": True, "coherent": True},
                "chroma": {"complete": False, "rebuild_required": True},
            },
            files=[
                {
                    "path": "db.sqlite3",
                    "kind": "database",
                    "size": 123,
                    "sha256": self.file_digest,
                    "blob": "sha256:" + self.file_digest,
                }
            ],
            counts={"documents": 242, "folders": 46, "users": 7, "objects": 1},
        )

    def test_manifest_is_canonical_and_deterministic(self):
        copied = deepcopy(self.manifest)
        self.assertEqual(manifest_digest(copied), self.manifest["manifest_sha256"])
        self.assertEqual(copied, self.manifest)
        validate_manifest(self.manifest, require_signature=False)

    def test_signed_manifest_round_trips(self):
        signed = sign_manifest(self.manifest, key=self.key, key_id="stage-manifest-1")
        validate_manifest(signed)
        self.assertTrue(verify_manifest_signature(signed, key=self.key))
        self.assertFalse(verify_manifest_signature(signed, key=b"wrong-key"))

    def test_signature_cannot_hide_a_changed_manifest(self):
        signed = sign_manifest(self.manifest, key=self.key, key_id="stage-manifest-1")
        signed["counts"]["documents"] = 241
        with self.assertRaisesRegex(ManifestV3Error, "manifest_digest_mismatch"):
            validate_manifest(signed)

    def test_recovery_point_is_logically_full_without_an_incremental_chain(self):
        later = build_manifest(
            **{
                "recovery_point_id": "rp-20260803-002",
                "dataset_id": self.manifest["dataset_id"],
                "source_instance_id": self.manifest["source_instance_id"],
                "source_environment": self.manifest["source_environment"],
                "backup_mode": "smart",
                "captured_epoch": 18,
                "created_at": "2026-08-03T12:05:00Z",
                "base_manifest_sha256": self.manifest["manifest_sha256"],
                "application": self.manifest["application"],
                "consistency": self.manifest["consistency"],
                "components": self.manifest["components"],
                "files": self.manifest["files"],
                "counts": self.manifest["counts"],
                "lineage": self.manifest["lineage"],
            }
        )
        self.assertEqual(later["files"], self.manifest["files"])
        self.assertEqual(
            later["base_manifest_sha256"],
            self.manifest["manifest_sha256"],
        )

    def test_import_rebind_requires_complete_foreign_parent_lineage(self):
        foreign = deepcopy(self.manifest)
        foreign["lineage"] = {
            "transition": "import_rebind",
            "parent_dataset_id": "ai-sahakar-prod-v2",
            "parent_generation_id": "legacy-prod-1",
            "parent_manifest_sha256": "d" * 64,
        }
        foreign.pop("manifest_sha256")
        rebound = build_manifest(
            recovery_point_id=foreign["recovery_point_id"],
            dataset_id=foreign["dataset_id"],
            source_instance_id=foreign["source_instance_id"],
            source_environment=foreign["source_environment"],
            backup_mode="import",
            captured_epoch=foreign["captured_epoch"],
            created_at=foreign["created_at"],
            application=foreign["application"],
            consistency=foreign["consistency"],
            components=foreign["components"],
            files=foreign["files"],
            counts=foreign["counts"],
            lineage=foreign["lineage"],
        )
        validate_manifest(rebound, require_signature=False)
        broken = deepcopy(rebound)
        broken["lineage"]["parent_manifest_sha256"] = ""
        broken["manifest_sha256"] = manifest_digest(broken)
        with self.assertRaisesRegex(ManifestV3Error, "lineage_parent_manifest_invalid"):
            validate_manifest(broken, require_signature=False)

    def test_rebind_to_same_dataset_is_refused(self):
        broken = deepcopy(self.manifest)
        broken["lineage"] = {
            "transition": "import_rebind",
            "parent_dataset_id": broken["dataset_id"],
            "parent_generation_id": "same-dataset-generation",
            "parent_manifest_sha256": "d" * 64,
        }
        broken["manifest_sha256"] = manifest_digest(broken)
        with self.assertRaisesRegex(ManifestV3Error, "lineage_rebind_dataset_unchanged"):
            validate_manifest(broken, require_signature=False)

    def test_unsafe_and_duplicate_paths_are_refused(self):
        for path in ("../db.sqlite3", "/db.sqlite3", "media\\one.pdf", "media/../db.sqlite3"):
            broken = deepcopy(self.manifest)
            broken["files"][0]["path"] = path
            broken["manifest_sha256"] = manifest_digest(broken)
            with self.subTest(path=path), self.assertRaisesRegex(ManifestV3Error, "file_path_unsafe"):
                validate_manifest(broken, require_signature=False)
        duplicate = deepcopy(self.manifest)
        duplicate["files"].append(deepcopy(duplicate["files"][0]))
        duplicate["manifest_sha256"] = manifest_digest(duplicate)
        with self.assertRaisesRegex(ManifestV3Error, "file_path_duplicate"):
            validate_manifest(duplicate, require_signature=False)

    def test_blob_key_is_bound_to_file_digest_and_size(self):
        broken = deepcopy(self.manifest)
        broken["files"][0]["blob"] = "sha256:" + "e" * 64
        broken["manifest_sha256"] = manifest_digest(broken)
        with self.assertRaisesRegex(ManifestV3Error, "file_blob_reference_invalid"):
            validate_manifest(broken, require_signature=False)

    def test_authoritative_database_and_media_must_be_complete(self):
        for component in ("database", "media"):
            broken = deepcopy(self.manifest)
            broken["components"][component]["complete"] = False
            broken["manifest_sha256"] = manifest_digest(broken)
            with self.subTest(component=component), self.assertRaisesRegex(ManifestV3Error, "authoritative_component_incomplete"):
                validate_manifest(broken, require_signature=False)

    def test_stale_indexes_can_request_rebuild_without_blocking_data_backup(self):
        self.assertFalse(self.manifest["components"]["chroma"]["complete"])
        self.assertTrue(self.manifest["components"]["chroma"]["rebuild_required"])
        validate_manifest(self.manifest, require_signature=False)

    def test_chroma_payload_requires_explicit_fail_closed_component_state(self):
        broken = deepcopy(self.manifest)
        broken["files"][0]["kind"] = "chroma"
        broken["files"][0]["path"] = "chroma_db/chroma.sqlite3"
        broken["components"]["chroma"] = {
            "complete": True,
            "coherent": True,
            "rebuild_required": False,
        }
        broken["manifest_sha256"] = manifest_digest(broken)
        with self.assertRaisesRegex(
            ManifestV3Error,
            "chroma_component_evidence_invalid",
        ):
            validate_manifest(broken, require_signature=False)

    def test_secret_bearing_fields_are_refused_recursively(self):
        for field in ("secret_key", "access_key", "password", "token"):
            broken = deepcopy(self.manifest)
            broken["application"][field] = "not-printed"
            broken["manifest_sha256"] = manifest_digest(broken)
            with self.subTest(field=field), self.assertRaisesRegex(ManifestV3Error, "manifest_contains_secret_field"):
                validate_manifest(broken, require_signature=False)

    def test_source_must_be_stable_and_database_valid(self):
        cases = (
            ("source_stable", False, "source_not_stable"),
            ("sqlite_integrity", "failed", "sqlite_integrity_failed"),
            ("foreign_keys", "failed", "sqlite_foreign_keys_failed"),
        )
        for field, value, code in cases:
            broken = deepcopy(self.manifest)
            broken["consistency"][field] = value
            broken["manifest_sha256"] = manifest_digest(broken)
            with self.subTest(field=field), self.assertRaisesRegex(ManifestV3Error, code):
                validate_manifest(broken, require_signature=False)


if __name__ == "__main__":
    unittest.main()
