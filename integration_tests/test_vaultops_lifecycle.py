"""Real MinIO/Redis proof for the vaultops lifecycle control plane."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE", "flowdocs.settings_integration"
)

import django

django.setup()

from django.core.cache import cache
from django.test import TransactionTestCase
from django.utils import timezone

from core.artifact_vault import ArtifactVault
from core.lease import (
    LeaseConflict,
    LeaseLost,
    acquire_lease,
    release_lease,
    renew_lease,
)
from core.namespace import KeyBuilder
from vaultops.models import (
    ArtifactGeneration,
    SourceSnapshot,
    VaultConnectionProfile,
    VaultJob,
)
from vaultops.services.inventory import verify_generation
from vaultops.services.profiles import profile_fingerprint
from vaultops.services.publication import (
    promote_candidate,
    publish_snapshot_candidate,
)
from vaultops.services.restore import run_restore_job


DATASET_ID = "ai-sahakar-prod"
SOURCE_ID = "ai-sahakar-prod"


def _database_bytes(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE django_migrations ("
        "id INTEGER PRIMARY KEY, app TEXT, name TEXT)"
    )
    connection.execute(
        "INSERT INTO django_migrations (app, name) "
        "VALUES ('core', '0019_sitesetting')"
    )
    connection.execute(
        "CREATE TABLE core_customuser ("
        "id INTEGER PRIMARY KEY, username TEXT, email TEXT, "
        "first_name TEXT, last_name TEXT, password TEXT, "
        "is_active INTEGER, is_staff INTEGER, "
        "is_superuser INTEGER, role TEXT)"
    )
    connection.execute(
        "CREATE TABLE django_session (session_key TEXT PRIMARY KEY)"
    )
    connection.commit()
    connection.close()
    return path.read_bytes()


class VaultOpsLifecycleIntegrationTests(TransactionTestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.vault = ArtifactVault()
        self._delete_dataset()
        cache.clear()
        self.profile = VaultConnectionProfile.objects.create(
            key="vaultops-ci",
            display_name="Vaultops CI",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            enabled=True,
            read_only=False,
            environment_locked=True,
            endpoint_origin=self.vault.config.endpoint,
            bucket=self.vault.config.bucket,
            region=self.vault.config.region,
            dataset_id=DATASET_ID,
            production_source_id=SOURCE_ID,
        )
        self.profile.fingerprint = profile_fingerprint(self.profile)
        self.profile.save(update_fields=["fingerprint", "updated_at"])

    def tearDown(self):
        self._delete_dataset()
        cache.clear()
        self.temporary.cleanup()
        super().tearDown()

    def _delete_dataset(self):
        prefix = f"datasets/{DATASET_ID}/"
        try:
            response = self.vault.client.list_objects_v2(
                Bucket=self.vault.config.bucket,
                Prefix=prefix,
            )
            objects = [
                {"Key": item["Key"]}
                for item in response.get("Contents", [])
            ]
            if objects:
                self.vault.client.delete_objects(
                    Bucket=self.vault.config.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
        except Exception:
            pass

    def _snapshot_and_job(self):
        workspace = self.root / "snapshot"
        workspace.mkdir()
        database_path = workspace / "db.sqlite3"
        payload = _database_bytes(database_path)
        digest = hashlib.sha256(payload).hexdigest()
        job = VaultJob.objects.create(
            operation="active_sync",
            status=VaultJob.Status.RUNNING,
            profile=self.profile,
            profile_fingerprint=self.profile.fingerprint,
            dataset_id=DATASET_ID,
            idempotency_key=f"publish:{uuid.uuid4()}",
        )
        snapshot = SourceSnapshot.objects.create(
            job=job,
            deployment_id="vaultops-ci-deployment",
            state=SourceSnapshot.State.FINALIZED,
            initial_epoch=1,
            included_epoch=1,
            snapshot_digest=digest,
            workspace_path=str(workspace),
            file_count=1,
            byte_count=len(payload),
            finalized_at=timezone.now(),
        )
        evidence = {
            "snapshot_id": str(snapshot.public_id),
            "files": [
                {
                    "path": "db.sqlite3",
                    "size_bytes": len(payload),
                    "sha256": digest,
                }
            ],
            "inventory": {
                "schema": {
                    "inventory_schema": "pdfsearch-artifact-inventory/v1"
                },
                "database": {
                    "migrations": {"latest": "0019_sitesetting"}
                },
                "counts": {"pdf_rows": 0},
                "embedding_index": {},
                "faiss": {"file_count": 0, "files": []},
                "pdf_storage": {"count": 0},
                "chroma": {"file_count": 0},
                "static": {"file_count": 0},
            },
        }
        (workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence, sort_keys=True),
            encoding="utf-8",
        )
        return snapshot, job

    def test_candidate_promotion_inventory_restore_and_redis_fencing(self):
        snapshot, publication_job = self._snapshot_and_job()

        generation = publish_snapshot_candidate(
            snapshot=snapshot,
            profile=self.profile,
            job=publication_job,
            vault=self.vault,
        )
        self.assertEqual(
            generation.vault_state,
            ArtifactGeneration.VaultState.CANDIDATE,
        )
        pointer_key = KeyBuilder(DATASET_ID).control_authoritative()
        with self.assertRaises(Exception):
            self.vault.client.head_object(
                Bucket=self.vault.config.bucket,
                Key=pointer_key,
            )

        generation, pointer = promote_candidate(
            generation=generation,
            job=publication_job,
            profile=self.profile,
            confirmed=True,
            vault=self.vault,
        )
        self.assertEqual(
            generation.vault_state,
            ArtifactGeneration.VaultState.AUTHORITATIVE,
        )
        self.assertEqual(pointer["generation_id"], generation.generation_id)

        verified = verify_generation(
            self.vault,
            self.profile,
            generation_id=generation.generation_id,
            verify_objects=True,
        )
        self.assertTrue(verified.authoritative)
        self.assertEqual(verified.manifest_digest, generation.manifest_digest)
        self.assertEqual(verified.file_count, 1)

        restore_job = VaultJob.objects.create(
            operation="restore_generation",
            status=VaultJob.Status.RUNNING,
            profile=self.profile,
            profile_fingerprint=self.profile.fingerprint,
            dataset_id=DATASET_ID,
            generation_id=generation.generation_id,
            idempotency_key=f"restore:{uuid.uuid4()}",
        )
        workspace = run_restore_job(
            restore_job,
            vault=self.vault,
            run_rehearsal=False,
        )
        self.assertEqual(workspace.state, "activation_ready")
        self.assertTrue(Path(workspace.runtime_path, "db.sqlite3").is_file())
        self.assertEqual(
            workspace.validation_evidence["manifest_digest"],
            generation.manifest_digest,
        )

        first = acquire_lease(
            DATASET_ID,
            "vaultops-ci-instance",
            ttl_seconds=30,
        )
        with self.assertRaises(LeaseConflict):
            acquire_lease(
                DATASET_ID,
                "competing-instance",
                ttl_seconds=30,
            )
        renewed = renew_lease(first, ttl_seconds=30)
        release_lease(first)
        with self.assertRaises(LeaseLost):
            renew_lease(renewed, ttl_seconds=30)
        successor = acquire_lease(
            DATASET_ID,
            "successor-instance",
            ttl_seconds=30,
        )
        release_lease(first)
        self.assertEqual(
            renew_lease(successor, ttl_seconds=30).owner_token,
            successor.owner_token,
        )
        release_lease(successor)
