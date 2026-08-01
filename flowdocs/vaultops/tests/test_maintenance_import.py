import json
import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.candidate_maintenance import CandidateMaintenanceError
from core.models import MaintenanceJob
from vaultops.models import (
    ArtifactGeneration,
    RestoreWorkspace,
    VaultConnectionProfile,
)
from vaultops.services.maintenance_import import (
    MaintenanceImportError,
    import_maintenance_candidate,
)


class MaintenanceCandidateImportTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.maintenance_root = self.root / "maintenance"
        self.runtime_root = self.root / "runtimes"
        self.control_root = self.root / "control"
        for path in (
            self.maintenance_root,
            self.runtime_root,
            self.control_root,
        ):
            path.mkdir()
        self.identity = SimpleNamespace(
            dataset_id="dataset-test",
            deployment_id="staging-test",
            app_release_version="release-test",
            app_image_digest="sha256:image-test",
        )
        self.settings = override_settings(
            ENV_IDENTITY=self.identity,
            MAINTENANCE_WORKSPACE_ROOT=self.maintenance_root,
            RUNTIME_GENERATIONS_ROOT=self.runtime_root,
            DATA_CONTROL_ROOT=self.control_root,
            VAULT_DEFAULT_PROFILE="test-profile",
            VAULT_MAX_MANIFEST_BYTES=1024 * 1024,
            VAULT_MAX_MANIFEST_OBJECTS=100,
            VAULT_MAX_GENERATION_BYTES=1024 * 1024,
            ACTIVATION_INTENT_SIGNING_KEY="x" * 32,
        )
        self.settings.enable()
        self.profile = VaultConnectionProfile.objects.create(
            key="test-profile",
            display_name="Test",
            dataset_id="dataset-test",
            fingerprint="f" * 64,
        )
        self.job = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="completed",
            options={
                "candidate_state": "activation_ready",
                "recovery_set_id": "rs-test",
            },
        )
        self.workspace = (
            self.maintenance_root / f"mw-{self.job.public_id}-candidate"
        )
        self.workspace.mkdir()
        (self.workspace / "db.sqlite3").write_bytes(b"sqlite-candidate")
        for directory in (
            "media",
            "pdf_cache",
            "faiss_indexes",
            "chroma_db",
            "backups",
        ):
            (self.workspace / directory).mkdir()
        (self.workspace / "media" / "one.pdf").write_bytes(b"%PDF-1.4")
        workspace_stat = self.workspace.stat()
        self.job.options["candidate_workspace_id"] = self.workspace.name
        self.job.options["candidate_workspace_identity"] = {
            "device": workspace_stat.st_dev,
            "inode": workspace_stat.st_ino,
        }
        self.job.save(update_fields=["options", "updated_at"])
        self.manifest = {
            "manifest_version": 1,
            "workspace_id": self.workspace.name,
            "state": "activation_ready",
            "operation": self.job.kind,
            "expires_at": (timezone.now() + timedelta(days=1)).isoformat(),
            "source": {
                "job_id": str(self.job.public_id),
                "runtime_generation_id": "active-generation",
                "runtime_manifest_digest": "a" * 64,
                "recovery_set_id": "rs-test",
            },
            "derived": {"manifest_basis_sha256": "b" * 64},
        }
        self._write_manifest()
        self.parent = SimpleNamespace(
            generation_id="active-generation",
            manifest_digest="a" * 64,
            pointer_digest="c" * 64,
        )
        self.patches = [
            patch(
                "vaultops.services.maintenance_import.verify_set",
                return_value={
                    "set_id": "rs-test",
                    "verification_state": "verified",
                },
            ),
            patch(
                "vaultops.services.maintenance_import.validate_candidate",
                return_value={
                    "sqlite": {"integrity": "ok"},
                    "media": {"missing": 0},
                    "embeddings": {"vectors": 0},
                    "faiss": {},
                },
            ),
            patch(
                "vaultops.services.maintenance_import._active_parent",
                return_value=self.parent,
            ),
            patch(
                "vaultops.services.maintenance_import.rehearse_migrations",
                return_value={
                    "success": True,
                    "app_release": "release-test",
                    "image_digest": "sha256:image-test",
                },
            ),
            patch(
                "vaultops.services.maintenance_import.verify_recovery_superadmin"
            ),
        ]
        self.started_patches = [patcher.start() for patcher in self.patches]
        self.validate_candidate_mock = self.started_patches[1]

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.settings.disable()
        for directory, directories, files in os.walk(
            self.root, topdown=False
        ):
            for name in files:
                os.chmod(Path(directory) / name, 0o600)
            for name in directories:
                os.chmod(Path(directory) / name, 0o700)
            os.chmod(Path(directory), 0o700)
        self.temporary.cleanup()

    def _write_manifest(self):
        (self.workspace / "maintenance-candidate.json").write_text(
            json.dumps(self.manifest),
            encoding="utf-8",
        )

    def test_import_projects_explicit_local_origin_and_immutable_runtime(self):
        workspace = import_maintenance_candidate(
            self.job,
            idempotency_key="maintenance-import-request-1",
            actor_id=17,
            actor_name="operator",
        )

        generation = workspace.generation
        self.assertEqual(
            generation.origin,
            ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
        )
        self.assertEqual(
            generation.vault_state, ArtifactGeneration.VaultState.UNKNOWN
        )
        self.assertEqual(
            generation.runtime_state, ArtifactGeneration.RuntimeState.INACTIVE
        )
        self.assertEqual(generation.lineage_job_public_id, self.job.public_id)
        self.assertEqual(generation.parent_generation_id, "active-generation")
        self.assertEqual(workspace.state, RestoreWorkspace.State.ACTIVATION_READY)
        self.assertTrue(Path(workspace.runtime_path).is_dir())
        self.assertFalse(Path(workspace.runtime_path).stat().st_mode & 0o222)
        evidence = json.loads(
            (Path(workspace.runtime_path) / "runtime-evidence.json").read_text()
        )
        self.assertEqual(evidence["origin"], "local_maintenance")

    def test_exact_job_retry_reuses_projected_runtime(self):
        first = import_maintenance_candidate(
            self.job, idempotency_key="maintenance-import-request-1"
        )
        second = import_maintenance_candidate(
            self.job, idempotency_key="maintenance-import-request-1"
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ArtifactGeneration.objects.filter(
                origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE
            ).count(),
            1,
        )
        self.assertGreaterEqual(self.validate_candidate_mock.call_count, 4)

    def test_post_rehearsal_coherence_failure_prevents_projection(self):
        valid = {
            "sqlite": {"integrity": "ok"},
            "media": {"missing": 0},
            "embeddings": {"vectors": 0},
            "faiss": {},
        }
        self.validate_candidate_mock.side_effect = [
            valid,
            CandidateMaintenanceError("candidate_faiss_missing", "8"),
        ]

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job, idempotency_key="maintenance-import-request-1"
            )

        self.assertEqual(raised.exception.reason_code, "candidate_faiss_missing")
        self.assertFalse(ArtifactGeneration.objects.exists())
        self.assertFalse(RestoreWorkspace.objects.exists())
        self.assertEqual(
            [
                path for path in self.runtime_root.iterdir()
                if path.name != ".maintenance-import.lock"
            ],
            [],
        )

    def test_idempotent_reuse_revalidates_published_runtime(self):
        import_maintenance_candidate(
            self.job, idempotency_key="maintenance-import-request-1"
        )
        self.validate_candidate_mock.side_effect = CandidateMaintenanceError(
            "candidate_faiss_count_mismatch", "8"
        )

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job, idempotency_key="maintenance-import-request-1"
            )

        self.assertEqual(
            raised.exception.reason_code, "candidate_faiss_count_mismatch"
        )

    def test_same_job_with_new_key_is_rejected(self):
        import_maintenance_candidate(
            self.job, idempotency_key="maintenance-import-request-1"
        )

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job, idempotency_key="maintenance-import-request-2"
            )

        self.assertEqual(raised.exception.reason_code, "idempotency_conflict")

    def test_idempotency_key_cannot_be_reused_for_another_job(self):
        import_maintenance_candidate(
            self.job, idempotency_key="maintenance-import-request-1"
        )
        other_job = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="completed",
            options={},
        )

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                other_job,
                idempotency_key="maintenance-import-request-1",
            )

        self.assertEqual(raised.exception.reason_code, "idempotency_conflict")

    def test_stale_parent_is_rejected_before_runtime_copy(self):
        self.manifest["source"]["runtime_manifest_digest"] = "d" * 64
        self._write_manifest()

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job, idempotency_key="maintenance-import-request-1"
            )

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_candidate_parent_stale",
        )
        self.assertEqual(list(self.runtime_root.iterdir()), [])

    def test_unexpected_candidate_entry_is_rejected(self):
        (self.workspace / "unexpected.txt").write_text("unsafe")

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job, idempotency_key="maintenance-import-request-1"
            )

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_candidate_entries_unexpected",
        )

    def test_process_death_orphan_is_verified_and_reconciled(self):
        with patch(
            "vaultops.services.maintenance_import.ArtifactGeneration.objects.create",
            side_effect=SystemExit("simulated process death"),
        ):
            with self.assertRaises(SystemExit):
                import_maintenance_candidate(
                    self.job,
                    idempotency_key="maintenance-import-request-1",
                )

        runtimes = [
            path
            for path in self.runtime_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        self.assertEqual(len(runtimes), 1)
        self.assertTrue(
            (runtimes[0] / "local-generation-manifest.json").is_file()
        )

        workspace = import_maintenance_candidate(
            self.job,
            idempotency_key="maintenance-import-request-1",
        )

        self.assertEqual(Path(workspace.runtime_path), runtimes[0])
        self.assertEqual(ArtifactGeneration.objects.count(), 1)

    def test_symlinked_workspace_root_is_rejected(self):
        linked_root = self.root / "linked-maintenance"
        linked_root.symlink_to(self.maintenance_root, target_is_directory=True)

        with override_settings(MAINTENANCE_WORKSPACE_ROOT=linked_root):
            with self.assertRaises(MaintenanceImportError) as raised:
                import_maintenance_candidate(
                    self.job,
                    idempotency_key="maintenance-import-request-1",
                )

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_workspace_root_unsafe",
        )

    def test_workspace_replacement_after_candidate_creation_is_rejected(self):
        original = self.maintenance_root / f"{self.workspace.name}.original"
        self.workspace.rename(original)
        self.workspace.mkdir()

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job,
                idempotency_key="maintenance-import-swapped-workspace",
            )

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_candidate_workspace_unsafe",
        )
        self.assertFalse(ArtifactGeneration.objects.exists())

    def test_symlinked_import_lock_is_rejected(self):
        lock_target = self.root / "lock-target"
        lock_target.write_text("unsafe")
        (self.runtime_root / ".maintenance-import.lock").symlink_to(
            lock_target
        )

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(
                self.job,
                idempotency_key="maintenance-import-request-1",
            )

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_import_lock_unsafe",
        )
