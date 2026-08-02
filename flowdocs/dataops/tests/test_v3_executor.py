"""DataOps-authoritative execution tests around the transitional snapshot adapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase, override_settings

from dataops.lifecycle import (
    InstanceIdentity,
    LifecycleCapabilities,
    LifecycleIntent,
    LifecycleRequest,
    compile_lifecycle_plan,
)
from dataops.models import DataConnection, DataOperation, RecoveryPoint
from dataops.tests.test_v3_backup import FakeS3
from dataops.v3_executor import V3ExecutionError, execute_backup_operation


@override_settings(
    ACTIVATION_INTENT_SIGNING_KEY="executor-test-signing-key",
    APP_IMAGE_DIGEST="repo@example.invalid/app@sha256:" + "a" * 64,
    APP_RELEASE_VERSION="2026.08.03",
    ENV_IDENTITY=SimpleNamespace(
        deployment_id="stage-2026",
        app_image_digest="repo@example.invalid/app@sha256:" + "a" * 64,
        app_release_version="2026.08.03",
    ),
)
class V3BackupExecutorTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        self.connection = DataConnection.objects.using("control").create(
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
            capabilities={"read": True, "write": True, "conditional_write": True},
        )
        plan = compile_lifecycle_plan(
            LifecycleRequest(LifecycleIntent.BACKUP),
            InstanceIdentity(
                environment="staging",
                deployment_id="stage-2026",
                dataset_id=self.connection.dataset_id,
            ),
            LifecycleCapabilities(
                owned_store_readable=True,
                owned_store_writable=True,
            ),
        )
        self.operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.BACKUP,
            state=DataOperation.State.RUNNING,
            connection=self.connection,
            lifecycle_route=plan.route,
            lifecycle_plan=plan.as_dict(),
            lifecycle_plan_digest=plan.plan_digest,
            idempotency_key="backup-1",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot_factory(self, _operation, *, lease=None):
        workspace = self.root / "snapshot"
        (workspace / "media" / "pdfs").mkdir(parents=True, exist_ok=True)
        (workspace / "db.sqlite3").write_bytes(b"sqlite")
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"pdf")
        snapshot_id = uuid.uuid4()
        files = []
        for path, category in (
            ("db.sqlite3", "database"),
            ("media/pdfs/one.pdf", "media"),
        ):
            body = (workspace / path).read_bytes()
            files.append(
                {
                    "path": path,
                    "category": category,
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        (workspace / "snapshot-evidence.json").write_text(
            json.dumps(
                {
                    "snapshot_id": str(snapshot_id),
                    "files": files,
                    "inventory": {
                        "database": {"migrations": {"latest": "0027"}},
                        "counts": {"pdf_rows": 242, "folders": 46, "users": 7},
                    },
                    "faiss": {"unavailable_documents": {"count": 0}},
                    "configuration_fingerprint": {"sha256": "f" * 64},
                }
            ),
            encoding="utf-8",
        )
        if lease:
            lease()
        return SimpleNamespace(
            public_id=snapshot_id,
            workspace_path=str(workspace),
            included_epoch=3,
        )

    def test_executor_publishes_projection_and_binds_plan_receipt(self):
        leases = []
        result = execute_backup_operation(
            self.operation,
            lease=lambda: leases.append(True),
            snapshot_factory=self.snapshot_factory,
            client_factory=lambda _connection: self.client,
        )
        point = RecoveryPoint.objects.using("control").get(
            public_id=result["recovery_point_id"]
        )
        self.assertEqual(point.format_version, 3)
        self.assertEqual(point.manifest_digest, result["manifest_digest"])
        self.assertEqual(
            point.evidence["plan_digest"],
            self.operation.lifecycle_plan_digest,
        )
        self.assertEqual(point.counts["documents"], 242)
        self.assertGreaterEqual(len(leases), 3)

    def test_tampered_plan_is_refused_before_snapshot_or_storage(self):
        self.operation.lifecycle_plan["target_dataset_id"] = "other"
        self.operation.save(update_fields=["lifecycle_plan"])
        called = []
        with self.assertRaisesRegex(V3ExecutionError, "lifecycle_plan_digest_mismatch"):
            execute_backup_operation(
                self.operation,
                snapshot_factory=lambda *_args, **_kwargs: called.append(True),
                client_factory=lambda _connection: called.append(True),
            )
        self.assertEqual(called, [])

    def test_projection_collision_does_not_replace_existing_digest(self):
        recovery_id = f"rp-{self.operation.public_id}"
        RecoveryPoint.objects.using("control").create(
            connection=self.connection,
            dataset_id=self.connection.dataset_id,
            release_id=recovery_id,
            format_version=3,
            prefix="existing",
            manifest_digest="0" * 64,
            state=RecoveryPoint.State.VERIFIED,
        )
        with self.assertRaisesRegex(V3ExecutionError, "recovery_point_projection_conflict"):
            execute_backup_operation(
                self.operation,
                snapshot_factory=self.snapshot_factory,
                client_factory=lambda _connection: self.client,
            )
        point = RecoveryPoint.objects.using("control").get(release_id=recovery_id)
        self.assertEqual(point.manifest_digest, "0" * 64)
