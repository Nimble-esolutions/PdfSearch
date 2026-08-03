"""Quarantine-first DataOps v3 recovery tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase, override_settings

from dataops.lifecycle import (
    ArtifactPassport,
    ArtifactTrust,
    InstanceIdentity,
    LifecycleCapabilities,
    LifecycleIntent,
    LifecycleRequest,
    SourceKind,
    compile_lifecycle_plan,
)
from dataops.models import (
    DataConnection,
    DataOperation,
    RecoveryPoint,
    RestoreCandidate,
)
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
from dataops.v3_restore_executor import execute_restore_operation


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
            db.execute("CREATE TABLE core_folder (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO core_folder VALUES (1)")
            db.execute("CREATE TABLE core_customuser (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO core_customuser VALUES (1)")
            db.execute(
                "CREATE TABLE django_migrations "
                "(app TEXT, name TEXT, applied TEXT)"
            )
            db.execute(
                "INSERT INTO django_migrations VALUES "
                "('core', '0027', '2026-08-03T00:00:00Z')"
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
            "included_epoch": 1,
            "source_stable": True,
            "consistency": {
                "sqlite_integrity": "ok",
                "foreign_keys": "ok",
            },
            "files": records,
            "inventory": {
                "database": {
                    "migrations": {"latest": "core.0027", "count": 1}
                },
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
        self.snapshot = snapshot
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

    def operation_for(
        self,
        point,
        *,
        intent=LifecycleIntent.RESTORE,
        activate=False,
    ):
        plan = compile_lifecycle_plan(
            LifecycleRequest(
                intent,
                activate=activate,
                confirmation_present=activate,
            ),
            InstanceIdentity(
                environment="staging",
                deployment_id="stage-2026",
                dataset_id=self.connection.dataset_id,
            ),
            LifecycleCapabilities(
                owned_store_readable=True,
                owned_store_writable=True,
                source_readable=True,
                quarantine_writable=True,
                signing_available=activate,
                activation_available=activate,
                isolated_restore_available=True,
            ),
            ArtifactPassport(
                source_kind=SourceKind.RECOVERY_POINT,
                dataset_id=point.dataset_id,
                generation_id=point.release_id,
                manifest_sha256=point.manifest_digest,
                format_version=3,
                trust=ArtifactTrust.VERIFIED,
                complete=True,
                read_only=True,
                signature_valid=True,
            ),
        )
        return DataOperation.objects.using("control").create(
            kind=(
                DataOperation.Kind.TEST_RECOVERY
                if intent is LifecycleIntent.TEST_RECOVERY
                else DataOperation.Kind.RESTORE
            ),
            state=DataOperation.State.RUNNING,
            connection=self.connection,
            release_id="import-prod-001" if point.dataset_id != self.connection.dataset_id else "",
            lifecycle_route=plan.route,
            lifecycle_plan=plan.as_dict(),
            lifecycle_plan_digest=plan.plan_digest,
            checkpoint={"recovery_point_id": str(point.public_id)},
            idempotency_key=str(uuid.uuid4()),
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

    def test_reuses_a_successfully_rehearsed_workspace(self):
        first = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "rehearsed-quarantine",
        )

        def migration_runner(_database, *, promote_to, **_kwargs):
            with sqlite3.connect(promote_to) as database:
                database.execute("CREATE TABLE migrated_marker (value TEXT)")
            return {"success": True, "migration_leaf_after": "0029"}

        rehearsal = rehearse_quarantine(
            first,
            migration_runner=migration_runner,
        )
        second = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "rehearsed-quarantine",
        )

        self.assertTrue(rehearsal["success"])
        self.assertEqual(len(rehearsal["database_sha256"]), 64)
        self.assertGreater(rehearsal["database_size"], 0)
        self.assertTrue(second["reused"])

    def test_rehearsal_receipt_does_not_allow_other_file_mutation(self):
        first = materialize_quarantine(
            self.verified(),
            quarantine_root=self.root / "tampered-quarantine",
        )
        rehearse_quarantine(
            first,
            migration_runner=lambda *_args, **_kwargs: {"success": True},
        )
        workspace = Path(first["workspace"])
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"tampered")

        with self.assertRaisesMessage(V3RestoreError, "restore_workspace_conflict"):
            materialize_quarantine(
                self.verified(),
                quarantine_root=self.root / "tampered-quarantine",
            )

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

    def test_queued_same_dataset_restore_builds_ready_candidate(self):
        operation = self.operation_for(self.point)

        def migration_runner(*_args, **_kwargs):
            return {"success": True, "migration_leaf_after": "0029"}

        with override_settings(
            ACTIVATION_INTENT_SIGNING_KEY=self.signing_key.decode(),
            DATAOPS_RESTORE_STAGING_ROOT=self.root / "executor-quarantine",
        ):
            result = execute_restore_operation(
                operation,
                client_factory=lambda _connection: self.client,
                migration_runner=migration_runner,
            )
        candidate = RestoreCandidate.objects.using("control").get(
            operation=operation
        )
        self.assertEqual(result["status"], "ready_for_activation")
        self.assertEqual(result["indexing_ratio"], 1.0)
        self.assertEqual(candidate.state, RestoreCandidate.State.READY)
        self.assertFalse(result["activation_performed"])

    def test_approved_activation_is_scheduled_only_after_candidate_is_ready(self):
        operation = self.operation_for(self.point, activate=True)
        scheduled = []

        def scheduler(candidate, *, operation, confirmed):
            scheduled.append((candidate.state, operation.public_id, confirmed))
            return {
                "state": "scheduled",
                "intent_id": "activation-intent-1",
                "manifest_digest": candidate.manifest_digest,
            }

        with override_settings(
            ACTIVATION_INTENT_SIGNING_KEY=self.signing_key.decode(),
            DATAOPS_RESTORE_STAGING_ROOT=self.root / "activation-quarantine",
        ):
            result = execute_restore_operation(
                operation,
                client_factory=lambda _connection: self.client,
                migration_runner=lambda *_args, **_kwargs: {"success": True},
                activation_scheduler=scheduler,
            )
        self.assertEqual(
            scheduled,
            [(RestoreCandidate.State.READY, operation.public_id, True)],
        )
        self.assertEqual(result["activation"]["state"], "scheduled")
        self.assertFalse(result["activation_performed"])

    def test_queued_isolated_recovery_never_imports_or_activates(self):
        operation = self.operation_for(
            self.point,
            intent=LifecycleIntent.TEST_RECOVERY,
        )
        with override_settings(
            ACTIVATION_INTENT_SIGNING_KEY=self.signing_key.decode(),
            DATAOPS_RESTORE_STAGING_ROOT=self.root / "isolated-quarantine",
        ):
            result = execute_restore_operation(
                operation,
                client_factory=lambda _connection: self.client,
                migration_runner=lambda *_args, **_kwargs: {"success": True},
            )
        self.assertEqual(result["route"], "isolated_rehearsal")
        self.assertEqual(
            result["source_recovery_point_id"],
            result["effective_recovery_point_id"],
        )
        self.assertFalse(result["activation_performed"])

    def test_queued_foreign_restore_imports_then_rehearses_owned_point(self):
        source_model = DataConnection.objects.using("control").create(
            name="Production source",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="prod-source",
            dataset_id="ai-sahakar-prod-v2",
            credential_ref="secret://dataops/prod-source",
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        source_client = FakeS3()
        source_manifest, source_receipt = publish_snapshot(
            snapshot=self.snapshot,
            connection=connection_from_model(source_model),
            client=source_client,
            recovery_point_id="prod-rp-001",
            source_instance_id="legacy-production",
            source_environment="production",
            image_digest="repo@example.invalid/app@sha256:" + "a" * 64,
            release_version="2026.08.03",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )
        source_point = RecoveryPoint.objects.using("control").create(
            connection=source_model,
            dataset_id=source_model.dataset_id,
            release_id=source_receipt.recovery_point_id,
            format_version=3,
            prefix=source_receipt.recovery_point_key,
            manifest_digest=source_receipt.manifest_sha256,
            signature_key_id="stage-manifest-1",
            data_complete=True,
            state=RecoveryPoint.State.VERIFIED,
            counts=source_manifest["counts"],
            evidence={"signature_valid": True},
        )
        operation = self.operation_for(source_point)

        def clients(connection):
            return (
                source_client
                if connection.dataset_id == source_model.dataset_id
                else self.client
            )

        with override_settings(
            ACTIVATION_INTENT_SIGNING_KEY=self.signing_key.decode(),
            DATAOPS_RESTORE_STAGING_ROOT=self.root / "foreign-quarantine",
        ):
            result = execute_restore_operation(
                operation,
                client_factory=clients,
                migration_runner=lambda *_args, **_kwargs: {"success": True},
            )
        effective = RecoveryPoint.objects.using("control").get(
            public_id=result["effective_recovery_point_id"]
        )
        self.assertEqual(effective.dataset_id, self.connection.dataset_id)
        self.assertEqual(
            effective.identity["parent_dataset_id"],
            source_model.dataset_id,
        )
        self.assertNotEqual(
            result["source_recovery_point_id"],
            result["effective_recovery_point_id"],
        )
