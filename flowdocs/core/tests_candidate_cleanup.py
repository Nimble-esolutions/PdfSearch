import hashlib
import io
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.artifact_cleanup import (
    CleanupError,
    apply_legacy_backup_cleanup,
    apply_cleanup,
    capacity_report,
    cleanup_plan,
    inventory_legacy_backups,
    legacy_backup_cleanup_plan,
)
from core.candidate_maintenance import (
    CandidateMaintenanceError,
    WORKSPACE_MANIFEST,
    create_workspace,
    validate_candidate,
)
from core.maintenance_plans import workbench_maintenance_state


class _Values:
    def __init__(self, pdf_ids, folder_ids):
        self.pdf_ids = pdf_ids
        self.folder_ids = folder_ids

    def exclude(self, **kwargs):
        return self

    def values_list(self, field, flat=False):
        return self.pdf_ids if field == "pdf_id" else self.folder_ids


class CandidateWorkspaceTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.control = self.root / "control"
        self.data.mkdir()
        self.control.mkdir()
        for name in ("media", "faiss_indexes", "chroma_db"):
            (self.data / name).mkdir()
        (self.data / "media" / "pdfs").mkdir()
        (self.data / "media" / "pdfs" / "one.pdf").write_bytes(b"%PDF-1.4")
        self.database = self.data / "db.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE core_pdffile ("
            "id INTEGER PRIMARY KEY, folder_id INTEGER, file TEXT, "
            "page_chunks TEXT, chunk_embeddings TEXT, lifecycle TEXT)"
        )
        connection.execute(
            "INSERT INTO core_pdffile VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                7,
                "pdfs/one.pdf",
                json.dumps(["searchable text"]),
                json.dumps([[1.0, 0.0]]),
                "ready",
            ),
        )
        connection.commit()
        connection.close()
        self.settings = override_settings(
            DATA_ROOT=self.data,
            DATA_CONTROL_ROOT=self.control,
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.database),
                },
                "control": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.control / "control.sqlite3"),
                },
            },
            MEDIA_ROOT=self.data / "media",
            FAISS_INDEX_DIR=self.data / "faiss_indexes",
            CHROMA_DIR=self.data / "chroma_db",
            MAINTENANCE_WORKSPACE_ROOT=self.control / "maintenance-workspaces",
            RUNTIME_GENERATION_ID="source-runtime",
            RUNTIME_MANIFEST_DIGEST="source-manifest",
        )
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.temporary.cleanup()

    def test_workspace_snapshot_never_changes_source_database_bytes(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        job = SimpleNamespace(
            public_id=uuid.uuid4(),
            kind="reindex_selected",
            options={"recovery_set_id": "rs-test"},
            items=_Values([1], [7]),
        )
        barrier = SimpleNamespace(current_epoch=7)
        with (
            patch(
                "core.candidate_maintenance.capacity_report",
                return_value={
                    "byte_capacity_ok": True,
                    "inode_capacity_ok": True,
                },
            ),
            patch(
                "vaultops.services.mutations.request_barrier",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.assert_barrier_owner",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.release_barrier",
                return_value=True,
            ),
        ):
            workspace = create_workspace(job)
        manifest = json.loads(
            (workspace / WORKSPACE_MANIFEST).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source"]["mutation_epoch"], 7)
        self.assertEqual(
            manifest["source"]["database_sha256"],
            hashlib.sha256((workspace / "db.sqlite3").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            set(manifest["source"]["source_trees"]),
            {"media", "faiss", "chroma"},
        )
        candidate = sqlite3.connect(workspace / "db.sqlite3")
        candidate.execute(
            "UPDATE core_pdffile SET lifecycle='processing' WHERE id=1"
        )
        candidate.commit()
        candidate.close()

        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(), before
        )
        self.assertNotEqual(
            hashlib.sha256((workspace / "db.sqlite3").read_bytes()).hexdigest(),
            before,
        )
        self.assertEqual(manifest["source"]["runtime_generation_id"], "source-runtime")
        self.assertRegex(manifest["source"]["parent_tree"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["source"]["snapshot"]["sha256"], r"^[0-9a-f]{64}$")

    def test_workspace_rejects_pointer_change_during_snapshot(self):
        job = SimpleNamespace(
            public_id=uuid.uuid4(),
            kind="reindex_selected",
            options={"recovery_set_id": "rs-test"},
            items=_Values([1], [7]),
        )
        first = SimpleNamespace(
            generation_id="source-runtime",
            manifest_digest="source-manifest",
            pointer_digest="1" * 64,
            runtime_path=str(self.data),
        )
        second = SimpleNamespace(**{**first.__dict__, "pointer_digest": "2" * 64})
        barrier = SimpleNamespace(current_epoch=0)
        with (
            patch("core.candidate_maintenance.capacity_report", return_value={
                "byte_capacity_ok": True, "inode_capacity_ok": True,
            }),
            patch(
                "core.candidate_maintenance._maintenance_source_parent",
                side_effect=[first, second],
            ),
            patch(
                "vaultops.services.mutations.request_barrier",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.assert_barrier_owner",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.release_barrier",
                return_value=True,
            ),
            self.assertRaises(CandidateMaintenanceError) as raised,
        ):
            create_workspace(job)
        self.assertEqual(
            raised.exception.reason_code, "maintenance_source_authority_changed"
        )
        self.assertFalse(any(self.control.glob("maintenance-workspaces/mw-*")))

    def test_workspace_rejects_parent_byte_change_during_snapshot(self):
        job = SimpleNamespace(
            public_id=uuid.uuid4(),
            kind="reindex_selected",
            options={"recovery_set_id": "rs-test"},
            items=_Values([1], [7]),
        )
        parent = SimpleNamespace(
            generation_id="source-runtime",
            manifest_digest="source-manifest",
            pointer_digest="1" * 64,
            runtime_path=str(self.data),
        )

        def resolve_parent():
            if resolve_parent.calls:
                (self.data / "media" / "pdfs" / "one.pdf").write_bytes(b"changed")
            resolve_parent.calls += 1
            return parent

        resolve_parent.calls = 0
        barrier = SimpleNamespace(current_epoch=0)
        with (
            patch("core.candidate_maintenance.capacity_report", return_value={
                "byte_capacity_ok": True, "inode_capacity_ok": True,
            }),
            patch(
                "core.candidate_maintenance._maintenance_source_parent",
                side_effect=resolve_parent,
            ),
            patch(
                "vaultops.services.mutations.request_barrier",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.assert_barrier_owner",
                return_value=barrier,
            ),
            patch(
                "vaultops.services.mutations.release_barrier",
                return_value=True,
            ),
            self.assertRaises(CandidateMaintenanceError) as raised,
        ):
            create_workspace(job)
        self.assertEqual(
            raised.exception.reason_code, "maintenance_source_authority_changed"
        )
        self.assertFalse(any(self.control.glob("maintenance-workspaces/mw-*")))

    def test_workspace_rejects_mutation_epoch_drift_and_removes_temporary(self):
        job = SimpleNamespace(
            public_id=uuid.uuid4(),
            kind="reindex_selected",
            options={"recovery_set_id": "rs-test"},
            items=_Values([1], [7]),
        )
        parent = SimpleNamespace(
            generation_id="source-runtime",
            manifest_digest="source-manifest",
            pointer_digest="1" * 64,
            runtime_path=str(self.data),
        )
        with (
            patch(
                "core.candidate_maintenance.capacity_report",
                return_value={
                    "byte_capacity_ok": True,
                    "inode_capacity_ok": True,
                },
            ),
            patch(
                "core.candidate_maintenance._maintenance_source_parent",
                return_value=parent,
            ),
            patch(
                "vaultops.services.mutations.request_barrier",
                return_value=SimpleNamespace(current_epoch=4),
            ),
            patch(
                "vaultops.services.mutations.assert_barrier_owner",
                side_effect=[
                    SimpleNamespace(current_epoch=4),
                    SimpleNamespace(current_epoch=5),
                ],
            ),
            patch(
                "vaultops.services.mutations.release_barrier",
                return_value=True,
            ),
            self.assertRaises(CandidateMaintenanceError) as raised,
        ):
            create_workspace(job)

        self.assertEqual(
            raised.exception.reason_code, "maintenance_source_snapshot_changed"
        )
        self.assertFalse(any(self.control.rglob("*.tmp")))
        self.assertFalse(any(self.control.glob("maintenance-workspaces/mw-*")))

    def test_candidate_validation_checks_media_embeddings_and_faiss(self):
        import faiss
        import numpy as np

        workspace = self.control / "validation-workspace"
        workspace.mkdir()
        (workspace / "media" / "pdfs").mkdir(parents=True)
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"%PDF-1.4")
        (workspace / "faiss_indexes").mkdir()
        workspace_db = workspace / "db.sqlite3"
        workspace_db.write_bytes(self.database.read_bytes())
        index = faiss.IndexFlatIP(2)
        index.add(np.asarray([[1.0, 0.0]], dtype="float32"))
        faiss.write_index(
            index, str(workspace / "faiss_indexes" / "folder_7.index")
        )
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps(
                {
                    "affected_folder_ids": [7],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        result = validate_candidate(workspace)
        self.assertEqual(result["sqlite"]["integrity"], "ok")
        self.assertEqual(result["media"]["missing"], 0)
        self.assertEqual(result["embeddings"]["dimensions"], [2])
        self.assertEqual(result["faiss"]["7"]["vectors"], 1)

    def test_candidate_validation_preserves_unavailable_row_without_media(self):
        workspace = self.control / "unavailable-media-workspace"
        workspace.mkdir()
        (workspace / "media").mkdir()
        (workspace / "faiss_indexes").mkdir()
        workspace_db = workspace / "db.sqlite3"
        workspace_db.write_bytes(self.database.read_bytes())
        connection = sqlite3.connect(workspace_db)
        for definition in (
            "media_expected_sha256 TEXT",
            "media_expected_size INTEGER",
            "media_prior_lifecycle TEXT",
        ):
            connection.execute(f"ALTER TABLE core_pdffile ADD COLUMN {definition}")
        connection.execute(
            "UPDATE core_pdffile SET lifecycle='unavailable', "
            "media_expected_sha256='', media_expected_size=NULL, "
            "media_prior_lifecycle='uploaded' WHERE id=1"
        )
        connection.commit()
        connection.close()
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps({"affected_folder_ids": []})
        )

        result = validate_candidate(workspace)

        self.assertEqual(result["media"]["missing"], 0)
        self.assertEqual(result["embeddings"]["vectors"], 0)
        self.assertEqual(result["media"]["unavailable"]["count"], 1)

    def test_candidate_validation_rejects_faiss_vector_count_mismatch(self):
        import faiss

        workspace = self.control / "count-mismatch-workspace"
        workspace.mkdir()
        (workspace / "media" / "pdfs").mkdir(parents=True)
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"%PDF-1.4")
        (workspace / "faiss_indexes").mkdir()
        (workspace / "db.sqlite3").write_bytes(self.database.read_bytes())
        faiss.write_index(
            faiss.IndexFlatIP(2),
            str(workspace / "faiss_indexes" / "folder_7.index"),
        )
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps({"affected_folder_ids": [7]})
        )

        with self.assertRaises(CandidateMaintenanceError) as raised:
            validate_candidate(workspace)

        self.assertEqual(
            raised.exception.reason_code, "candidate_faiss_count_mismatch"
        )

    def test_candidate_validation_checks_unaffected_searchable_folders(self):
        import faiss
        import numpy as np

        workspace = self.control / "whole-runtime-workspace"
        workspace.mkdir()
        (workspace / "media" / "pdfs").mkdir(parents=True)
        for name in ("one.pdf", "two.pdf"):
            (workspace / "media" / "pdfs" / name).write_bytes(b"%PDF-1.4")
        (workspace / "faiss_indexes").mkdir()
        workspace_db = workspace / "db.sqlite3"
        workspace_db.write_bytes(self.database.read_bytes())
        connection = sqlite3.connect(workspace_db)
        connection.execute(
            "INSERT INTO core_pdffile VALUES (?, ?, ?, ?, ?, ?)",
            (
                2,
                8,
                "pdfs/two.pdf",
                json.dumps(["unaffected searchable text"]),
                json.dumps([[0.0, 1.0]]),
                "ready",
            ),
        )
        connection.commit()
        connection.close()
        index = faiss.IndexFlatIP(2)
        index.add(np.asarray([[1.0, 0.0]], dtype="float32"))
        faiss.write_index(
            index, str(workspace / "faiss_indexes" / "folder_7.index")
        )
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps({"affected_folder_ids": [7]})
        )

        with self.assertRaises(CandidateMaintenanceError) as raised:
            validate_candidate(workspace)

        self.assertEqual(raised.exception.reason_code, "candidate_faiss_missing")
        self.assertEqual(str(raised.exception), "8")

    def test_candidate_validation_reports_full_runtime_folder_scope(self):
        import faiss
        import numpy as np

        workspace = self.control / "whole-runtime-valid"
        workspace.mkdir()
        (workspace / "media" / "pdfs").mkdir(parents=True)
        for name in ("one.pdf", "two.pdf"):
            (workspace / "media" / "pdfs" / name).write_bytes(b"%PDF-1.4")
        (workspace / "faiss_indexes").mkdir()
        workspace_db = workspace / "db.sqlite3"
        workspace_db.write_bytes(self.database.read_bytes())
        connection = sqlite3.connect(workspace_db)
        connection.execute(
            "INSERT INTO core_pdffile VALUES (?, ?, ?, ?, ?, ?)",
            (
                2,
                8,
                "pdfs/two.pdf",
                json.dumps(["unaffected searchable text"]),
                json.dumps([[0.0, 1.0]]),
                "ready",
            ),
        )
        connection.commit()
        connection.close()
        for folder_id, vector in ((7, [1.0, 0.0]), (8, [0.0, 1.0])):
            index = faiss.IndexFlatIP(2)
            index.add(np.asarray([vector], dtype="float32"))
            faiss.write_index(
                index,
                str(workspace / "faiss_indexes" / f"folder_{folder_id}.index"),
            )
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps({"affected_folder_ids": [7]})
        )

        result = validate_candidate(workspace)

        self.assertEqual(result["affected_folder_ids"], [7])
        self.assertEqual(result["validated_folder_ids"], [7, 8])


class ArtifactCleanupPlannerTests(TestCase):
    databases = {"default", "control"}
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        roots = {
            "RECOVERY_SET_ROOT": self.root / "recovery",
            "VAULT_SNAPSHOT_ROOT": self.root / "snapshots",
            "VAULT_RESTORE_ROOT": self.root / "restore",
            "MAINTENANCE_WORKSPACE_ROOT": self.root / "maintenance",
            "RUNTIME_GENERATIONS_ROOT": self.root / "runtimes",
            "DATA_CONTROL_ROOT": self.root / "control",
        }
        for path in roots.values():
            path.mkdir()
        self.settings = override_settings(**roots)
        self.settings.enable()
        failed = roots["VAULT_SNAPSHOT_ROOT"] / "failed-snapshot"
        failed.mkdir()
        (failed / "payload.bin").write_bytes(b"x" * 32)
        (failed / "snapshot.json").write_text(
            json.dumps(
                {
                    "state": "failed",
                    "created_at": "2020-01-01T00:00:00+00:00",
                }
            )
        )
        candidate = roots["MAINTENANCE_WORKSPACE_ROOT"] / "candidate"
        candidate.mkdir()
        (candidate / "db.sqlite3").write_bytes(b"candidate")
        (candidate / WORKSPACE_MANIFEST).write_text(
            json.dumps(
                {
                    "state": "activation_ready",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        held = roots["VAULT_SNAPSHOT_ROOT"] / "incident-held"
        held.mkdir()
        (held / "payload.bin").write_bytes(b"held")
        (held / "snapshot.json").write_text(
            json.dumps(
                {
                    "state": "failed",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "retention": {"incident_hold": True},
                }
            )
        )
        referenced = roots["VAULT_RESTORE_ROOT"] / "activation-reference"
        referenced.mkdir()
        (referenced / "workspace.json").write_text(
            json.dumps(
                {
                    "state": "failed",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "activation_reference": "intent-1",
                }
            )
        )
        resumable = roots["VAULT_SNAPSHOT_ROOT"] / "resumable"
        resumable.mkdir()
        (resumable / "snapshot.json").write_text(
            json.dumps(
                {
                    "state": "failed",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "resumable_checkpoint": "checkpoint-1",
                }
            )
        )
        for generation, state in (
            ("active-runtime", "active"),
            ("previous-runtime", "previous"),
        ):
            runtime = roots["RUNTIME_GENERATIONS_ROOT"] / generation
            runtime.mkdir()
            (runtime / "runtime-manifest.json").write_text(
                json.dumps(
                    {
                        "generation_id": generation,
                        "runtime_state": state,
                        "created_at": "2020-01-01T00:00:00+00:00",
                    }
                )
            )

    def tearDown(self):
        self.settings.disable()
        self.temporary.cleanup()

    def test_plan_protects_references_and_requires_current_confirmation(self):
        plan = cleanup_plan()
        self.assertEqual(len(plan["candidates"]), 1)
        self.assertEqual(
            plan["candidates"][0]["reason_code"],
            "failed_diagnostic_payload_expired",
        )
        protections = {
            item["name"]: item["protection_reasons"]
            for item in plan["protected"]
        }
        self.assertNotIn("candidate", protections)
        self.assertIn(
            "candidate", {item["name"] for item in plan["retained"]}
        )
        self.assertEqual(protections["incident-held"], ["incident_hold"])
        self.assertEqual(
            protections["activation-reference"], ["activation_reference"]
        )
        self.assertEqual(protections["resumable"], ["resumable_checkpoint"])
        self.assertEqual(
            protections["active-runtime"], ["active_or_previous_runtime"]
        )
        self.assertEqual(
            protections["previous-runtime"], ["active_or_previous_runtime"]
        )
        with self.assertRaisesRegex(CleanupError, "stale_cleanup_plan"):
            apply_cleanup("wrong")
        result = apply_cleanup(plan["plan_id"])
        self.assertEqual(len(result["removed"]), 1)
        self.assertFalse(Path(result["removed"][0]["path"]).exists())

    def test_expired_activation_ready_workspace_is_a_candidate(self):
        workspace = Path(settings.MAINTENANCE_WORKSPACE_ROOT) / "expired-ready"
        workspace.mkdir()
        (workspace / "db.sqlite3").write_bytes(b"candidate")
        (workspace / WORKSPACE_MANIFEST).write_text(
            json.dumps(
                {
                    "state": "activation_ready",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "expires_at": "2020-01-08T00:00:00+00:00",
                    "source": {"job_id": str(uuid.uuid4())},
                }
            )
        )

        plan = cleanup_plan()

        candidate = next(
            item for item in plan["candidates"]
            if item["name"] == "expired-ready"
        )
        self.assertEqual(
            candidate["reason_code"], "unactivated_workspace_expired"
        )

    @patch("core.artifact_cleanup.MAX_APPLY_BYTES", 1)
    def test_apply_is_blocked_above_approval_boundary(self):
        plan = cleanup_plan()
        self.assertFalse(plan["apply_allowed"])
        with self.assertRaisesRegex(
            CleanupError, "cleanup_exceeds_20_gib_approval_boundary"
        ):
            apply_cleanup(plan["plan_id"])

    def test_unreferenced_local_runtime_expires_after_seven_days(self):
        runtime = Path(settings.RUNTIME_GENERATIONS_ROOT) / "local-runtime"
        runtime.mkdir()
        (runtime / "db.sqlite3").write_bytes(b"candidate")
        (runtime / "local-generation-manifest.json").write_text(
            json.dumps(
                {
                    "origin": "local_maintenance",
                    "generation_id": "local-generation",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "vault_authority": {
                        "state": "unpublished",
                        "stale": True,
                    },
                }
            )
        )

        plan = cleanup_plan()

        candidate = next(
            item for item in plan["candidates"]
            if item["name"] == "local-runtime"
        )
        self.assertEqual(
            candidate["reason_code"], "unactivated_local_runtime_expired"
        )
        self.assertEqual(
            candidate["manifest"]["generation_id"], "local-generation"
        )

    def test_local_runtime_for_current_job_is_protected(self):
        from core.models import MaintenanceJob

        job = MaintenanceJob.objects.create(
            kind="repair_indexes",
            status="running",
        )
        runtime = Path(settings.RUNTIME_GENERATIONS_ROOT) / "current-job-runtime"
        runtime.mkdir()
        (runtime / "db.sqlite3").write_bytes(b"candidate")
        (runtime / "local-generation-manifest.json").write_text(
            json.dumps(
                {
                    "origin": "local_maintenance",
                    "generation_id": "current-job-generation",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "maintenance": {"job_public_id": str(job.public_id)},
                    "vault_authority": {"state": "unpublished"},
                }
            )
        )

        plan = cleanup_plan()

        protected = next(
            item for item in plan["protected"]
            if item["name"] == "current-job-runtime"
        )
        self.assertIn(
            "current_or_checkpointed_job",
            protected["protection_reasons"],
        )

    def test_activation_ready_runtime_reference_is_protected(self):
        from vaultops.models import (
            ArtifactGeneration,
            RestoreWorkspace,
            VaultConnectionProfile,
        )

        runtime = Path(settings.RUNTIME_GENERATIONS_ROOT) / "prepared-runtime"
        runtime.mkdir()
        (runtime / "db.sqlite3").write_bytes(b"candidate")
        (runtime / "local-generation-manifest.json").write_text(
            json.dumps(
                {
                    "origin": "local_maintenance",
                    "generation_id": "prepared-generation",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "vault_authority": {"state": "unpublished"},
                }
            )
        )
        profile = VaultConnectionProfile.objects.create(
            key="cleanup-profile",
            display_name="Cleanup",
            dataset_id="cleanup-dataset",
            fingerprint="c" * 64,
        )
        generation = ArtifactGeneration.objects.create(
            profile=profile,
            origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
            dataset_id=profile.dataset_id,
            generation_id="prepared-generation",
            manifest_digest="d" * 64,
            vault_state=ArtifactGeneration.VaultState.UNKNOWN,
            runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
            lineage_job_public_id=uuid.uuid4(),
            parent_generation_id="parent-generation",
            parent_manifest_digest="e" * 64,
        )
        RestoreWorkspace.objects.create(
            generation=generation,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=generation.manifest_digest,
            runtime_path=str(runtime),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )

        plan = cleanup_plan()

        protected = next(
            item for item in plan["protected"]
            if item["name"] == "prepared-runtime"
        )
        self.assertIn(
            "workspace_reference",
            protected["protection_reasons"],
        )

    def test_apply_rechecks_pointer_protection_before_deletion(self):
        from vaultops.models import RuntimePointerObservation

        runtime = Path(settings.RUNTIME_GENERATIONS_ROOT) / "pointer-race"
        runtime.mkdir()
        (runtime / "db.sqlite3").write_bytes(b"candidate")
        (runtime / "local-generation-manifest.json").write_text(
            json.dumps(
                {
                    "origin": "local_maintenance",
                    "generation_id": "pointer-race-generation",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "vault_authority": {"state": "unpublished"},
                }
            )
        )
        plan = cleanup_plan()
        real_cleanup_plan = cleanup_plan
        calls = 0

        def plan_with_pointer_change(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                RuntimePointerObservation.objects.create(
                    deployment_id=settings.ENV_IDENTITY.deployment_id,
                    active_generation_id="pointer-race-generation",
                    status="ready",
                    observed_at=datetime.now(timezone.utc),
                )
            return real_cleanup_plan(*args, **kwargs)

        with patch(
            "core.artifact_cleanup.cleanup_plan",
            side_effect=plan_with_pointer_change,
        ):
            with self.assertRaisesRegex(CleanupError, "stale_cleanup_plan"):
                apply_cleanup(plan["plan_id"])

        self.assertTrue(runtime.exists())

    @patch(
        "core.artifact_cleanup._runtime_protections",
        side_effect=CleanupError("cleanup_protection_state_unavailable"),
    )
    def test_plan_fails_closed_when_protection_state_is_unavailable(self, _mock):
        with self.assertRaisesRegex(
            CleanupError, "cleanup_protection_state_unavailable"
        ):
            cleanup_plan()

    def test_unreadable_manifest_blocks_plan_and_apply_without_deletion(self):
        workspace = (
            Path(settings.MAINTENANCE_WORKSPACE_ROOT)
            / "unreadable-workspace"
        )
        workspace.mkdir()
        manifest = workspace / WORKSPACE_MANIFEST
        manifest.write_text(
            json.dumps(
                {
                    "state": "activation_ready",
                    "created_at": "2020-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        manifest.chmod(0)
        try:
            with self.assertRaisesRegex(
                CleanupError, "^cleanup_inventory_unavailable$"
            ) as raised:
                cleanup_plan()
            self.assertNotIn(str(manifest), str(raised.exception))

            with self.assertRaisesRegex(
                CleanupError, "^cleanup_inventory_unavailable$"
            ):
                apply_cleanup("untrusted-plan")
            self.assertTrue(workspace.exists())
        finally:
            manifest.chmod(0o600)

    def test_malformed_manifest_blocks_workbench_without_leaking_or_candidates(
        self,
    ):
        workspace = (
            Path(settings.MAINTENANCE_WORKSPACE_ROOT)
            / "malformed-private-workspace"
        )
        workspace.mkdir()
        manifest = workspace / WORKSPACE_MANIFEST
        private_detail = "private-document-name.pdf"
        manifest.write_text(
            '{"state":"activation_ready","private":"'
            + private_detail,
            encoding="utf-8",
        )

        state = workbench_maintenance_state()

        cleanup = state["health"]["cleanup"]
        self.assertEqual(cleanup["state"], "blocked")
        self.assertEqual(
            cleanup["reason_code"], "cleanup_inventory_unavailable"
        )
        self.assertEqual(cleanup["prunable_bytes"], 0)
        self.assertEqual(cleanup["protected_bytes"], 0)
        self.assertEqual(cleanup["plan_id"], "")
        self.assertNotIn(private_detail, json.dumps(state, default=str))

        with self.assertRaisesRegex(
            CleanupError, "^cleanup_inventory_unavailable$"
        ):
            apply_cleanup("untrusted-plan")
        self.assertTrue(workspace.exists())


class LegacyBackupCleanupTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.settings = override_settings(
            BACKUP_DIR=self.backups,
            DATA_CONTROL_ROOT=self.root,
        )
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.temporary.cleanup()

    def _backup(self, timestamp, *, valid=True):
        path = self.backups / f"db_backup_{timestamp}.sqlite3"
        if not valid:
            path.write_bytes(b"not sqlite")
            return path
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE evidence (value TEXT)")
            connection.execute("INSERT INTO evidence VALUES (?)", (timestamp,))
            connection.commit()
        finally:
            connection.close()
        return path

    def _six_backups(self):
        return [
            self._backup(f"2020-01-0{day}_000000")
            for day in range(1, 7)
        ]

    def test_plan_is_read_only_and_keeps_three_verified_newest(self):
        paths = self._six_backups()

        plan = legacy_backup_cleanup_plan()

        self.assertEqual(plan["inventory_count"], 6)
        self.assertEqual(len(plan["retained"]), 3)
        self.assertEqual(len(plan["candidates"]), 3)
        self.assertTrue(
            all(item["integrity"] == "ok" for item in plan["retained"])
        )
        self.assertTrue(
            all(len(item["sha256"]) == 64 for item in plan["retained"])
        )
        self.assertEqual(
            {Path(item["path"]).name for item in plan["retained"]},
            {path.name for path in paths[-3:]},
        )
        self.assertTrue(all(path.exists() for path in paths))

    def test_apply_requires_fresh_plan_and_removes_only_candidates(self):
        paths = self._six_backups()
        plan = legacy_backup_cleanup_plan()
        with self.assertRaisesRegex(
            CleanupError, "stale_legacy_backup_cleanup_plan"
        ):
            apply_legacy_backup_cleanup("wrong")

        result = apply_legacy_backup_cleanup(plan["plan_id"])

        self.assertEqual(len(result["removed"]), 3)
        self.assertEqual(
            {item["name"] for item in inventory_legacy_backups()},
            {path.name for path in paths[-3:]},
        )

    def test_candidate_drift_rejects_apply_without_deleting_it(self):
        paths = self._six_backups()
        plan = legacy_backup_cleanup_plan()
        candidate = Path(plan["candidates"][0]["path"])
        with candidate.open("ab") as stream:
            stream.write(b"changed")

        with self.assertRaisesRegex(
            CleanupError, "stale_legacy_backup_cleanup_plan"
        ):
            apply_legacy_backup_cleanup(plan["plan_id"])

        self.assertTrue(candidate.exists())
        self.assertTrue(all(path.exists() for path in paths))

    def test_later_candidate_drift_is_prevalidated_before_any_deletion(self):
        paths = self._six_backups()
        plan = legacy_backup_cleanup_plan()
        later_candidate = Path(plan["candidates"][-1]["path"])
        real_record = __import__(
            "core.artifact_cleanup", fromlist=["_legacy_backup_record"]
        )._legacy_backup_record
        calls = 0

        def drift_before_later_validation(path, root):
            nonlocal calls
            calls += 1
            if calls == len(plan["candidates"]):
                with later_candidate.open("ab") as stream:
                    stream.write(b"changed")
            return real_record(path, root)

        with patch(
            "core.artifact_cleanup._legacy_backup_record",
            side_effect=drift_before_later_validation,
        ):
            with self.assertRaisesRegex(
                CleanupError, "stale_legacy_backup_cleanup_plan"
            ):
                apply_legacy_backup_cleanup(plan["plan_id"])

        self.assertTrue(all(path.exists() for path in paths))

    def test_disappearing_candidate_is_stale_before_any_other_deletion(self):
        self._six_backups()
        plan = legacy_backup_cleanup_plan()
        later_candidate = Path(plan["candidates"][-1]["path"])
        real_record = __import__(
            "core.artifact_cleanup", fromlist=["_legacy_backup_record"]
        )._legacy_backup_record
        calls = 0

        def disappear_before_later_validation(path, root):
            nonlocal calls
            calls += 1
            if calls == len(plan["candidates"]):
                later_candidate.unlink()
                raise FileNotFoundError(str(later_candidate))
            return real_record(path, root)

        with patch(
            "core.artifact_cleanup.legacy_backup_cleanup_plan",
            return_value=plan,
        ), patch(
            "core.artifact_cleanup._legacy_backup_record",
            side_effect=disappear_before_later_validation,
        ):
            with self.assertRaisesRegex(
                CleanupError, "stale_legacy_backup_cleanup_plan"
            ):
                apply_legacy_backup_cleanup(plan["plan_id"])

        self.assertFalse(later_candidate.exists())
        self.assertTrue(
            all(
                Path(item["path"]).exists()
                for item in plan["candidates"]
                if Path(item["path"]) != later_candidate
            )
        )

    def test_unlink_failure_reports_truthful_partial_progress(self):
        self._six_backups()
        plan = legacy_backup_cleanup_plan()
        first = Path(plan["candidates"][0]["path"])
        second = Path(plan["candidates"][1]["path"])
        real_unlink = Path.unlink

        def fail_second(path, *args, **kwargs):
            if path == second:
                raise OSError("simulated unlink failure")
            return real_unlink(path, *args, **kwargs)

        with patch.object(
            Path, "unlink", autospec=True, side_effect=fail_second
        ):
            with self.assertRaises(CleanupError) as raised:
                apply_legacy_backup_cleanup(plan["plan_id"])

        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(
            raised.exception.reason_code,
            "legacy_backup_cleanup_failed",
        )
        self.assertEqual(
            raised.exception.progress["removed"],
            [{"path": str(first), "bytes": plan["candidates"][0]["bytes"]}],
        )
        self.assertEqual(
            raised.exception.progress["remaining_candidates"], 2
        )

    def test_corrupt_retained_backup_blocks_plan(self):
        self._six_backups()
        newest = self.backups / "db_backup_2020-01-07_000000.sqlite3"
        newest.write_bytes(b"not sqlite")

        with self.assertRaisesRegex(
            CleanupError, "legacy_backup_retained_set_unverified"
        ):
            legacy_backup_cleanup_plan()

    def test_matching_symlink_blocks_inventory(self):
        target = self.root / "outside.sqlite3"
        target.write_bytes(b"outside")
        link = self.backups / "db_backup_2020-01-01_000000.sqlite3"
        link.symlink_to(target)

        with self.assertRaisesRegex(
            CleanupError, "unsafe_legacy_backup_path"
        ):
            inventory_legacy_backups()

    def test_symlinked_backup_root_blocks_inventory(self):
        real_root = self.root / "real-backups"
        real_root.mkdir()
        self.settings.disable()
        self.backups.rmdir()
        self.backups.symlink_to(real_root)
        self.settings.enable()

        with self.assertRaisesRegex(
            CleanupError, "unsafe_legacy_backup_root"
        ):
            inventory_legacy_backups()

    def test_capacity_report_surfaces_unmanaged_legacy_debt(self):
        paths = self._six_backups()

        report = capacity_report(
            source_bytes=1,
            operation="maintenance",
            target_root=self.root,
        )

        debt = report["legacy_flat_backups"]
        self.assertEqual(debt["count"], 6)
        self.assertEqual(
            debt["bytes"], sum(path.stat().st_size for path in paths)
        )
        self.assertFalse(debt["managed_automatically"])

    def test_management_command_requires_exact_confirmation(self):
        self._six_backups()
        output = io.StringIO()
        call_command("legacy_backup_cleanup", "plan", stdout=output)
        plan = json.loads(output.getvalue())
        with self.assertRaisesRegex(CommandError, "--confirm"):
            call_command("legacy_backup_cleanup", "apply")

        call_command(
            "legacy_backup_cleanup",
            "apply",
            confirm=plan["plan_id"],
            stdout=io.StringIO(),
        )
        self.assertEqual(len(inventory_legacy_backups()), 3)
