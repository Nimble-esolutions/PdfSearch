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
        self.root = Path(self.temporary.name)
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
        self.job.options["candidate_workspace_id"] = self.workspace.name
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
        for patcher in self.patches:
            patcher.start()

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
        first = import_maintenance_candidate(self.job)
        second = import_maintenance_candidate(self.job)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ArtifactGeneration.objects.filter(
                origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE
            ).count(),
            1,
        )

    def test_stale_parent_is_rejected_before_runtime_copy(self):
        self.manifest["source"]["runtime_manifest_digest"] = "d" * 64
        self._write_manifest()

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(self.job)

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_candidate_parent_stale",
        )
        self.assertEqual(list(self.runtime_root.iterdir()), [])

    def test_unexpected_candidate_entry_is_rejected(self):
        (self.workspace / "unexpected.txt").write_text("unsafe")

        with self.assertRaises(MaintenanceImportError) as raised:
            import_maintenance_candidate(self.job)

        self.assertEqual(
            raised.exception.reason_code,
            "maintenance_candidate_entries_unexpected",
        )
