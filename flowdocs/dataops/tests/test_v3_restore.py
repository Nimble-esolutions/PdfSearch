"""Quarantine-first DataOps v3 recovery tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase

from dataops.models import DataConnection, RecoveryPoint
from dataops.package_v3 import canonical_json_bytes
from dataops.tests.test_v3_backup import FakeS3
from dataops.v3_backup import blob_key, publish_snapshot
from dataops.v3_config import connection_from_model
from dataops.v3_import import V3ImportError, import_rebind_recovery_point
from dataops.v3_restore import (
    V3RestoreError,
    load_verified_recovery_point,
    materialize_quarantine,
    rehearse_quarantine,
)


class V3RestoreTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.signing_key = b"recovery-signing-key-for-tests"
        self.client = FakeS3()
        self.connection = DataConnection.objects.using("control").create(
            name="Owned recovery storage",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        workspace = self.root / "snapshot"
        (workspace / "media" / "pdfs").mkdir(parents=True)
        database = workspace / "db.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TABLE core_pdffile "
                "(indexed INTEGER, processing_status TEXT)"
            )
            db.executemany(
                "INSERT INTO core_pdffile VALUES (?, ?)",
                [(1, "ready"), (1, "ready")],
            )
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"pdf-one")
        (workspace / "media" / "pdfs" / "two.PDF").write_bytes(b"pdf-two")
        records = []
        for relative, category in (
            ("db.sqlite3", "database"),
            ("media/pdfs/one.pdf", "media"),
            ("media/pdfs/two.PDF", "media"),
        ):
            body = (workspace / relative).read_bytes()
            records.append(
                {
                    "path": relative,
                    "category": category,
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        snapshot_id = uuid.uuid4()
        evidence = {
            "snapshot_id": str(snapshot_id),
            "source_stable": True,
            "consistency": {
                "sqlite_integrity": "ok",
                "foreign_keys": "ok",
            },
            "files": records,
            "inventory": {
                "database": {"migrations": {"latest": "0027"}},
                "counts": {"pdf_rows": 2, "folders": 1, "users": 1},
            },
            "faiss": {"unavailable_documents": {"count": 0}},
            "configuration_fingerprint": {"sha256": "f" * 64},
        }
        evidence["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(evidence)
        ).hexdigest()
        (workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )
        snapshot = SimpleNamespace(
            public_id=snapshot_id,
            workspace_path=str(workspace),
            included_epoch=1,
            evidence_sha256=evidence["evidence_sha256"],
        )
        manifest, receipt = publish_snapshot(
            snapshot=snapshot,
            connection=connection_from_model(self.connection),
            client=self.client,
            recovery_point_id="stage-rp-001",
            source_instance_id="stage-2026",
            source_environment="staging",
            image_digest="repo@example.invalid/app@sha256:" + "a" * 64,
            release_version="2026.08.03",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )
        self.manifest = manifest
        self.receipt = receipt
        self.point = RecoveryPoint.objects.using("control").create(
            connection=self.connection,
            dataset_id=self.connection.dataset_id,
            release_id=receipt.recovery_point_id,
            format_version=3,
            prefix=receipt.recovery_point_key,
            manifest_digest=receipt.manifest_sha256,
            signature_key_id="stage-manifest-1",
            data_complete=True,
            activation_ready=True,
            state=RecoveryPoint.State.VERIFIED,
            evidence={"signature_valid": True},
        )

    def tearDown(self):
        self.temporary.cleanup()

    def verified(self):
        return load_verified_recovery_point(
            self.point,
            signing_key=self.signing_key,
            client_factory=lambda _connection: self.client,
        )

    def test_materializes_complete_verified_generation_and_reuses_it(self):
        first = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "quarantine",
        )
        second = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "quarantine",
        )
        workspace = Path(first["workspace"])
        self.assertEqual(first["evidence"]["sqlite"]["integrity"], "ok")
        self.assertEqual(first["evidence"]["documents"], 2)
        self.assertEqual(first["evidence"]["indexing_ratio"], 1.0)
        self.assertTrue((workspace / "media" / "pdfs" / "two.PDF").is_file())
        self.assertTrue(second["reused"])

    def test_invalid_signature_is_rejected_before_materialization(self):
        key = self.receipt.manifest_key
        payload = json.loads(self.client.objects[(self.connection.bucket, key)]["body"])
        payload["signature"]["value"] = "0" * 64
        self.client.objects[(self.connection.bucket, key)]["body"] = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()
        with self.assertRaisesRegex(V3RestoreError, "manifest_signature_invalid"):
            self.verified()
        self.assertFalse((self.root / "quarantine").exists())

    def test_corrupt_blob_retains_failed_quarantine_and_never_touches_active(self):
        active = self.root / "active-generation"
        active.write_text("previous\n", encoding="utf-8")
        media = next(item for item in self.manifest["files"] if item["kind"] == "media")
        key = blob_key(connection_from_model(self.connection), media["sha256"])
        self.client.tamper_reads.add((self.connection.bucket, key))
        with self.assertRaisesRegex(V3RestoreError, "recovery_blob_digest_mismatch"):
            materialize_quarantine(
                self.verified(),
                quarantine_root=self.root / "quarantine",
            )
        self.assertEqual(active.read_text(encoding="utf-8"), "previous\n")
        self.assertTrue(
            any(
                path.name.startswith("failed-stage-rp-001-")
                for path in (self.root / "quarantine").iterdir()
            )
        )

    def test_projection_mismatch_is_rejected(self):
        self.point.manifest_digest = "0" * 64
        self.point.save(using="control", update_fields=["manifest_digest", "updated_at"])
        with self.assertRaisesRegex(V3RestoreError, "recovery_point_descriptor_mismatch"):
            self.verified()

    def test_migration_rehearsal_targets_candidate_database_and_is_idempotent(self):
        restored = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "quarantine",
        )
        calls = []

        def runner(database, *, workspace_path, promote_to):
            calls.append((database, workspace_path, promote_to))
            return {
                "success": True,
                "migration_leaf_before": "0027",
                "migration_leaf_after": "0029",
                "integrity_ok": True,
                "foreign_keys_ok": True,
            }

        first = rehearse_quarantine(restored, migration_runner=runner)
        second = rehearse_quarantine(restored, migration_runner=runner)
        workspace = Path(restored["workspace"])
        self.assertEqual(calls, [(workspace / "db.sqlite3", workspace, workspace / "db.sqlite3")])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])

    def test_failed_migration_rehearsal_retains_candidate(self):
        restored = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "quarantine",
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError("raw subprocess output")

        with self.assertRaisesRegex(V3RestoreError, "migration_rehearsal_failed"):
            rehearse_quarantine(restored, migration_runner=fail)
        workspace = Path(restored["workspace"])
        self.assertTrue(workspace.is_dir())
        self.assertFalse((workspace / ".dataops-rehearsal.json").exists())

    def test_foreign_import_rebinds_dataset_and_preserves_parent_lineage(self):
        restored = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "quarantine",
        )
        destination_model = DataConnection.objects.using("control").create(
            name="Destination",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="destination",
            dataset_id="ai-sahakar-stage-owned",
            credential_ref="secret://dataops/destination",
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        destination = connection_from_model(destination_model)
        destination_client = FakeS3()
        source_before = {
            key: dict(value) for key, value in self.client.objects.items()
        }
        manifest, receipt = import_rebind_recovery_point(
            source=self.verified(),
            quarantine_receipt=restored,
            destination=destination,
            destination_client=destination_client,
            recovery_point_id="import-prod-001",
            destination_instance_id="stage-2026",
            destination_environment="staging",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )
        self.assertEqual(manifest["dataset_id"], destination.dataset_id)
        self.assertEqual(manifest["lineage"]["transition"], "import_rebind")
        self.assertEqual(
            manifest["lineage"]["parent_manifest_sha256"],
            self.point.manifest_digest,
        )
        self.assertEqual(receipt.uploaded_objects, 3)
        self.assertEqual(self.client.objects, source_before)

    def test_import_rebind_reuses_destination_blobs_on_retry(self):
        restored = materialize_quarantine(
            self.verified(), quarantine_root=self.root / "quarantine"
        )
        destination_model = DataConnection.objects.using("control").create(
            name="Destination",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="destination",
            dataset_id="ai-sahakar-stage-owned",
            credential_ref="secret://dataops/destination",
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        destination = connection_from_model(destination_model)
        destination_client = FakeS3()
        arguments = {
            "source": self.verified(),
            "quarantine_receipt": restored,
            "destination": destination,
            "destination_client": destination_client,
            "recovery_point_id": "import-prod-001",
            "destination_instance_id": "stage-2026",
            "destination_environment": "staging",
            "signing_key": self.signing_key,
            "signing_key_id": "stage-manifest-1",
        }
        import_rebind_recovery_point(**arguments)
        _, second = import_rebind_recovery_point(**arguments)
        self.assertEqual(second.uploaded_objects, 0)
        self.assertEqual(second.reused_objects, 3)

    def test_same_dataset_import_is_refused(self):
        restored = materialize_quarantine(
            self.verified(), quarantine_root=self.root / "quarantine"
        )
        with self.assertRaisesRegex(V3ImportError, "import_rebind_dataset_unchanged"):
            import_rebind_recovery_point(
                source=self.verified(),
                quarantine_receipt=restored,
                destination=connection_from_model(self.connection),
                destination_client=FakeS3(),
                recovery_point_id="invalid",
                destination_instance_id="stage-2026",
                destination_environment="staging",
                signing_key=self.signing_key,
                signing_key_id="stage-manifest-1",
            )
