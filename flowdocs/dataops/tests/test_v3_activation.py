from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from dataops.models import (
    DataConnection,
    DataOperation,
    RecoveryPoint,
    RestoreCandidate,
)
from dataops.v3_activation import (
    V3ActivationError,
    project_candidate_runtime,
    schedule_candidate_activation,
)
from vaultops.models import ArtifactValidation, RestoreWorkspace


@override_settings(
    APP_ENV="staging",
    STAGING_RUNTIME_ACTIVATION_ENABLED=True,
    STAGING_INITIAL_ACTIVATION_ENABLED=True,
    VAULT_ADMIN_MUTATIONS_ENABLED=True,
    ACTIVATION_INTENT_SIGNING_KEY="activation-test-signing-key-32-characters",
    ENV_IDENTITY=SimpleNamespace(
        app_env=SimpleNamespace(value="staging"),
        is_production=False,
        deployment_id="stage-2026",
        dataset_id="ai-sahakar-stage-2026",
        app_release_version="2026.08.03",
        app_image_digest="repo@example.invalid/app@sha256:" + "a" * 64,
    ),
)
class V3ActivationTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "candidate"
        for relative in ("media", "pdf_cache", "faiss_indexes", "chroma_db"):
            (self.workspace / relative).mkdir(parents=True)
        (self.workspace / "db.sqlite3").write_bytes(b"sqlite")
        (self.workspace / "media" / "one.pdf").write_bytes(b"pdf")
        (self.workspace / "pdf_cache" / "one.json").write_text(
            "{}", encoding="utf-8"
        )
        (self.workspace / "faiss_indexes" / "folder_1.index").write_bytes(
            b"faiss"
        )
        (self.workspace / "chroma_db" / "state.bin").write_bytes(b"chroma")
        self.runtime_root = self.root / "runtimes"
        self.control_root = self.root / "control"
        self.settings_override = override_settings(
            RUNTIME_GENERATIONS_ROOT=self.runtime_root,
            DATA_CONTROL_ROOT=self.control_root,
        )
        self.settings_override.enable()
        connection = DataConnection.objects.using("control").create(
            name="Stage owned",
            endpoint="https://rustfs.example.invalid",
            bucket="stage",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://stage",
            is_primary=True,
        )
        self.operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.RESTORE,
            connection=connection,
            lifecycle_plan_digest="b" * 64,
        )
        point = RecoveryPoint.objects.using("control").create(
            connection=connection,
            dataset_id=connection.dataset_id,
            release_id="stage-recovery-1",
            prefix="v3/recovery-points/stage-recovery-1.json",
            manifest_digest="c" * 64,
        )
        self.candidate = RestoreCandidate.objects.using("control").create(
            recovery_point=point,
            operation=self.operation,
            workspace=str(self.workspace),
            state=RestoreCandidate.State.READY,
            manifest_digest=point.manifest_digest,
            evidence={
                "indexing_ratio": 1.0,
                "component_reconciliation": {
                    name: {
                        "ready": True,
                        "source": (
                            "candidate_preparation"
                            if name in {"pdf_cache", "faiss", "chroma"}
                            else "signed_manifest_and_restore"
                        ),
                        "rebuild_reasons": (
                            ["signed_rebuild_required"]
                            if name in {"pdf_cache", "faiss", "chroma"}
                            else []
                        ),
                        "signed": (
                            {"complete": True, "coherent": True}
                            if name in {"database", "media"}
                            else {
                                "complete": False,
                                "coherent": False,
                                "rebuild_required": True,
                            }
                        ),
                    }
                    for name in (
                        "database",
                        "media",
                        "pdf_cache",
                        "faiss",
                        "chroma",
                    )
                },
                "candidate_preparation": {
                    "success": True,
                    "rebuilt_components": ["pdf_cache", "faiss", "chroma"],
                    "validation": {
                        "faiss": {
                            "1": {
                                "vectors": 2,
                                "dimension": 1536,
                                "sha256": "e" * 64,
                            }
                        },
                        "validated_folder_ids": [1],
                        "chroma": {
                            "ready": True,
                            "file_count": 1,
                            "sha256": "f" * 64,
                        },
                    },
                },
            },
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temporary.cleanup()

    def test_ready_candidate_projects_to_immutable_runtime_idempotently(self):
        first = project_candidate_runtime(self.candidate)
        second = project_candidate_runtime(self.candidate)
        self.assertEqual(first.pk, second.pk)
        runtime = Path(first.runtime_path)
        self.assertTrue((runtime / "runtime-evidence.json").is_file())
        for relative in (
            "media",
            "pdf_cache",
            "faiss_indexes",
            "chroma_db",
        ):
            self.assertTrue((runtime / relative).is_dir())
        self.assertTrue(
            (runtime / "faiss_indexes" / "folder_1.index").is_file()
        )
        self.assertEqual(runtime.stat().st_mode & 0o222, 0)
        self.assertEqual((runtime / "db.sqlite3").stat().st_mode & 0o222, 0)

    def test_non_ready_candidate_is_rejected_without_runtime(self):
        self.candidate.state = RestoreCandidate.State.VERIFIED
        self.candidate.save(update_fields=["state", "updated_at"])
        with self.assertRaisesRegex(V3ActivationError, "candidate_not_activation_ready"):
            project_candidate_runtime(self.candidate)
        self.assertFalse(self.runtime_root.exists())

    def test_ready_state_without_component_evidence_is_rejected(self):
        self.candidate.evidence = {"indexing_ratio": 1.0}
        self.candidate.save(update_fields=["evidence", "updated_at"])
        with self.assertRaisesRegex(
            V3ActivationError,
            "candidate_component_evidence_missing",
        ):
            project_candidate_runtime(self.candidate)
        self.assertFalse(self.runtime_root.exists())

    @patch("dataops.v3_activation.schedule_activation")
    def test_schedule_binds_signed_intent_to_candidate_evidence(self, schedule):
        schedule.return_value = SimpleNamespace(
            public_id="e81b83d8-8da1-41ba-838f-c0db7a565ba8",
            intent_digest="d" * 64,
            target_generation_id="dataops-stage-recovery-1",
            manifest_digest="c" * 64,
        )
        evidence = schedule_candidate_activation(
            self.candidate,
            operation=self.operation,
            confirmed=True,
        )
        self.candidate.refresh_from_db(using="control")
        self.assertEqual(evidence["intent_digest"], "d" * 64)
        self.assertEqual(
            self.candidate.evidence["activation"]["manifest_digest"],
            self.candidate.manifest_digest,
        )
        runtime = Path(self.candidate.evidence["activation"]["runtime_path"])
        runtime_evidence = json.loads(
            (runtime / "runtime-evidence.json").read_text(encoding="utf-8")
        )
        self.assertTrue((runtime / "db.sqlite3").is_file())
        for relative in (
            "media",
            "pdf_cache",
            "faiss_indexes",
            "chroma_db",
        ):
            self.assertTrue((runtime / relative).is_dir())
        self.assertTrue(
            (runtime / "faiss_indexes" / "folder_1.index").is_file()
        )
        self.assertEqual(
            runtime_evidence["index_evidence"]["validated_folder_ids"],
            [1],
        )
        self.assertEqual(
            runtime_evidence["index_evidence"]["faiss_index_count"],
            1,
        )
        self.assertEqual(
            runtime_evidence["index_evidence"]["chroma_file_count"],
            1,
        )
        workspace = RestoreWorkspace.objects.using("control").get(
            import_idempotency_key=f"dataops-v3-candidate-{self.candidate.pk}"
        )
        self.assertEqual(
            workspace.validation_evidence["index_evidence"],
            runtime_evidence["index_evidence"],
        )
        validation = ArtifactValidation.objects.using("control").get(
            generation=workspace.generation,
            validation_type="dataops_v3_candidate",
        )
        self.assertEqual(
            validation.evidence["index_evidence"],
            runtime_evidence["index_evidence"],
        )
        schedule.assert_called_once()
