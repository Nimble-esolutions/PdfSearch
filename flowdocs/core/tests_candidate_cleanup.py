import hashlib
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.artifact_cleanup import (
    CleanupError,
    apply_cleanup,
    cleanup_plan,
)
from core.candidate_maintenance import (
    CandidateMaintenanceError,
    WORKSPACE_MANIFEST,
    create_workspace,
    validate_candidate,
)


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
        with patch(
            "core.candidate_maintenance.capacity_report",
            return_value={
                "byte_capacity_ok": True,
                "inode_capacity_ok": True,
            },
        ):
            workspace = create_workspace(job)
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


class ArtifactCleanupPlannerTests(SimpleTestCase):
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
                    "created_at": "2020-01-01T00:00:00+00:00",
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

    def test_plan_protects_candidate_and_requires_current_confirmation(self):
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
        self.assertEqual(protections["candidate"], ["maintenance_candidate"])
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

    @patch("core.artifact_cleanup.MAX_APPLY_BYTES", 1)
    def test_apply_is_blocked_above_approval_boundary(self):
        plan = cleanup_plan()
        self.assertFalse(plan["apply_allowed"])
        with self.assertRaisesRegex(
            CleanupError, "cleanup_exceeds_20_gib_approval_boundary"
        ):
            apply_cleanup(plan["plan_id"])
