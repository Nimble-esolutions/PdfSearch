"""Fresh-control-volume discovery tests for DataOps v3."""

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
from dataops.v3_backup import publish_snapshot
from dataops.v3_config import connection_from_model
from dataops.v3_discovery import V3DiscoveryError, discover_latest_recovery_point


class V3RecoveryPointDiscoveryTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = FakeS3()
        self.signing_key = b"fresh-control-discovery-signing-key"
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
            db.execute("INSERT INTO core_pdffile VALUES (1, 'ready')")
        (workspace / "media" / "pdfs" / "one.pdf").write_bytes(b"pdf")
        records = []
        for relative, category in (
            ("db.sqlite3", "database"),
            ("media/pdfs/one.pdf", "media"),
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
            "consistency": {"sqlite_integrity": "ok", "foreign_keys": "ok"},
            "files": records,
            "inventory": {
                "database": {"migrations": {"latest": "0027"}},
                "counts": {"pdf_rows": 1, "folders": 0, "users": 0},
            },
            "faiss": {"unavailable_documents": {"count": 0}},
            "configuration_fingerprint": {"sha256": "f" * 64},
        }
        evidence["evidence_sha256"] = hashlib.sha256(
            canonical_json_bytes(evidence)
        ).hexdigest()
        (workspace / "snapshot-evidence.json").write_text(json.dumps(evidence))
        snapshot = SimpleNamespace(
            public_id=snapshot_id,
            workspace_path=str(workspace),
            included_epoch=1,
            evidence_sha256=evidence["evidence_sha256"],
        )
        self.manifest, self.receipt = publish_snapshot(
            snapshot=snapshot,
            connection=connection_from_model(self.connection),
            client=self.client,
            recovery_point_id="stage-rp-fresh-001",
            source_instance_id="stage-2026",
            source_environment="staging",
            image_digest="repo.example/app@sha256:" + "a" * 64,
            release_version="2026.08.03",
            signing_key=self.signing_key,
            signing_key_id="stage-manifest-1",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def discover(self):
        return discover_latest_recovery_point(
            self.connection,
            signing_key=self.signing_key,
            client_factory=lambda _connection: self.client,
        )

    def test_fresh_control_database_projects_signed_latest_point_read_only(self):
        puts_before = list(self.client.puts)
        self.assertFalse(RecoveryPoint.objects.using("control").exists())

        point = self.discover()

        self.assertEqual(self.client.puts, puts_before)
        self.assertEqual(point.state, RecoveryPoint.State.VERIFIED)
        self.assertEqual(point.release_id, self.receipt.recovery_point_id)
        self.assertEqual(point.manifest_digest, self.receipt.manifest_sha256)
        self.assertEqual(point.counts["documents"], 1)
        self.assertTrue(point.evidence["signature_valid"])

    def test_tampered_manifest_creates_no_local_projection(self):
        manifest_key = self.receipt.manifest_key
        stored = self.client.objects[(self.connection.bucket, manifest_key)]
        manifest = json.loads(stored["body"])
        manifest["signature"]["value"] = "0" * 64
        body = canonical_json_bytes(manifest)
        stored["body"] = body
        stored["etag"] = self.client._etag(body)

        with self.assertRaisesRegex(V3DiscoveryError, "manifest_signature_invalid"):
            self.discover()

        self.assertFalse(RecoveryPoint.objects.using("control").exists())

    def test_existing_conflicting_projection_is_never_rewritten(self):
        existing = RecoveryPoint.objects.using("control").create(
            connection=self.connection,
            dataset_id=self.connection.dataset_id,
            release_id=self.receipt.recovery_point_id,
            format_version=3,
            prefix=self.receipt.recovery_point_key,
            manifest_digest="0" * 64,
            signature_key_id="stage-manifest-1",
            state=RecoveryPoint.State.VERIFIED,
        )

        with self.assertRaisesRegex(
            V3DiscoveryError, "recovery_point_projection_conflict"
        ):
            self.discover()

        existing.refresh_from_db(using="control")
        self.assertEqual(existing.manifest_digest, "0" * 64)
