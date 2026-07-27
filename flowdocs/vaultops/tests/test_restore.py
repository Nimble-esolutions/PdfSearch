import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth.hashers import check_password

from core.artifact_vault import ArtifactVault, VaultConfig
from core.namespace import KeyBuilder
from core.rehearsal import RehearsalError, rehearse_migrations
from vaultops.models import (
    ArtifactGeneration,
    RestoreWorkspace,
    VaultConnectionProfile,
    VaultJob,
    VaultJobStep,
)
from vaultops.services.inventory import InventoryError, verify_generation
from vaultops.services.jobs import claim_job
from vaultops.services.profiles import (
    VaultProfileError,
    probe_restore_profile,
    profile_fingerprint,
    resolve_credentials,
    upsert_restore_profile,
    validate_endpoint,
)
from vaultops.services.restore import (
    RestoreError,
    _capacity,
    queue_restore_job,
    run_restore_job,
)
from vaultops.services.sync import execute_claimed_job


class FakeS3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.head_bucket_calls = 0

    def put(self, key, payload, *, metadata=True):
        digest = hashlib.sha256(payload).hexdigest()
        self.objects[key] = {
            "body": payload,
            "metadata": {"sha256": digest} if metadata else {},
            "etag": f'"{digest[:16]}"',
        }

    def get_object(self, **kwargs):
        try:
            item = self.objects[kwargs["Key"]]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {
            "Body": io.BytesIO(item["body"]),
            "Metadata": dict(item["metadata"]),
            "ETag": item["etag"],
        }

    def head_object(self, **kwargs):
        try:
            item = self.objects[kwargs["Key"]]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {
            "ContentLength": len(item["body"]),
            "Metadata": dict(item["metadata"]),
            "ETag": item["etag"],
        }

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self.objects)
                if key.startswith(prefix)
            ],
            "IsTruncated": False,
        }

    def head_bucket(self, **kwargs):
        self.head_bucket_calls += 1
        return {}


def identity(**overrides):
    values = {
        "dataset_id": "staging-dataset",
        "production_source_id": "staging-source",
        "instance_id": "instance-1",
        "deployment_id": "deployment-1",
        "replica_id": "replica-1",
        "app_release_version": "test-release",
        "app_image_digest": "sha256:test-image",
        "is_authoritative_writer": False,
        "is_backup_writer": False,
        "is_production": False,
        "maintenance_scheduler_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def database_bytes():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "db.sqlite3"
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
            "INSERT INTO core_customuser VALUES "
            "(1, 'operator', 'operator@example.org', 'Ops', 'User', "
            "'copied-password-hash', 1, 1, 1, 'superadmin')"
        )
        connection.execute(
            "CREATE TABLE django_session (session_key TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO django_session VALUES ('secret-session')"
        )
        connection.commit()
        connection.close()
        return path.read_bytes()


class ProfileSecurityTests(SimpleTestCase):
    @override_settings(
        VAULT_ALLOWED_S3_ENDPOINTS=("https://vault.example",),
        VAULT_BLOCK_PRIVATE_S3_ENDPOINTS=True,
        VAULT_ALLOW_HTTP_S3_ENDPOINTS=False,
    )
    def test_accepts_exact_allowlisted_public_endpoint(self):
        resolver = lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]
        evidence = validate_endpoint(
            "https://vault.example", resolver=resolver
        )
        self.assertEqual(evidence["origin"], "https://vault.example")
        self.assertEqual(evidence["addresses"], ["93.184.216.34"])

    @override_settings(
        VAULT_ALLOWED_S3_ENDPOINTS=("https://vault.example",),
        VAULT_BLOCK_PRIVATE_S3_ENDPOINTS=True,
    )
    def test_rejects_private_dns_answer_and_userinfo(self):
        resolver = lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443))
        ]
        with self.assertRaisesMessage(
            VaultProfileError, "vault_endpoint_private_address"
        ):
            validate_endpoint(
                "https://vault.example", resolver=resolver
            )
        with self.assertRaisesMessage(
            VaultProfileError, "vault_endpoint_invalid"
        ):
            validate_endpoint(
                "https://user@vault.example", resolver=resolver
            )

    @override_settings(
        VAULT_CREDENTIAL_ALIASES="readonly=RESTORE_READONLY"
    )
    def test_credentials_resolve_only_from_deployed_alias(self):
        environment = {
            "RESTORE_READONLY_ACCESS_KEY": "access",
            "RESTORE_READONLY_SECRET_KEY": "secret",
        }
        self.assertEqual(
            resolve_credentials("readonly", environ=environment),
            ("access", "secret"),
        )
        with self.assertRaisesMessage(
            VaultProfileError, "credential_alias_not_approved"
        ):
            resolve_credentials("browser-supplied", environ=environment)


class RestoreCapacityTests(SimpleTestCase):
    @patch("core.artifact_cleanup.shutil.disk_usage")
    @patch("core.artifact_cleanup.os.statvfs")
    @override_settings(
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=0,
    )
    def test_unreported_inode_counts_are_not_treated_as_exhaustion(
        self, statvfs, disk_usage
    ):
        statvfs.return_value = SimpleNamespace(f_files=0, f_favail=0)
        disk_usage.return_value = SimpleNamespace(free=1024**3)
        evidence = _capacity(Path("/unused"), 10)
        self.assertIsNone(evidence["available_inodes"])
        self.assertEqual(evidence["inode_check"], "not_reported")

    @patch("core.artifact_cleanup.shutil.disk_usage")
    @patch("core.artifact_cleanup.os.statvfs")
    @override_settings(
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=10,
    )
    def test_configured_inode_reserve_fails_closed_when_unreported(
        self, statvfs, disk_usage
    ):
        statvfs.return_value = SimpleNamespace(f_files=0, f_favail=0)
        disk_usage.return_value = SimpleNamespace(free=1024**3)
        with self.assertRaisesMessage(
            RestoreError, "restore_capacity_inodes_unknown"
        ):
            _capacity(Path("/unused"), 10)


@override_settings(
    ENV_IDENTITY=identity(),
    OPENAI_EMBED_MODEL="text-embedding-3-small",
    VAULT_MAX_MANIFEST_BYTES=1024 * 1024,
    VAULT_MAX_MANIFEST_OBJECTS=100,
    VAULT_MAX_GENERATION_BYTES=1024 * 1024 * 100,
)
class InventoryAndRestoreTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = VaultConnectionProfile.objects.create(
            key="production-readonly",
            display_name="Production restore",
            source=VaultConnectionProfile.Source.STORED,
            enabled=True,
            read_only=True,
            endpoint_origin="https://vault.example",
            bucket="artifacts",
            region="test",
            dataset_id="ai-sahakar-prod",
            production_source_id="ai-sahakar-prod",
            credential_alias="readonly",
        )
        self.profile.fingerprint = profile_fingerprint(self.profile)
        self.profile.save(update_fields=["fingerprint", "updated_at"])
        self.client = FakeS3Client()
        self.vault = ArtifactVault(
            config=VaultConfig(
                enabled=True,
                endpoint="https://vault.example",
                bucket="artifacts",
                region="test",
                access_key="access",
                secret_key="secret",
            ),
            client=self.client,
        )
        self.generation_id = "generation-1"
        self._publish_fixture()

    def tearDown(self):
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def _put_json(self, key, payload):
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()
        self.client.put(key, body)
        return hashlib.sha256(body).hexdigest()

    def _publish_fixture(self, *, path="db.sqlite3"):
        keys = KeyBuilder(self.profile.dataset_id)
        database = database_bytes()
        database_digest = hashlib.sha256(database).hexdigest()
        database_key = keys.blob_db(database_digest)
        self.client.put(database_key, database)
        registration = {
            "dataset_id": self.profile.dataset_id,
            "registration_version": 1,
            "manifest_schema_range": {"min": 1, "max": 1},
            "app_identifier": "pdfsearch",
            "production_source_id": "ai-sahakar-prod",
        }
        self._put_json(keys.control_registration(), registration)
        manifest = {
            "release_id": self.generation_id,
            "manifest_version": 1,
            "read_only": True,
            "dataset_id": self.profile.dataset_id,
            "production_source_id": "ai-sahakar-prod",
            "app_release": "test-release",
            "image_digest": "sha256:test-image",
            "database": {
                "migrations": {"latest": "0019_sitesetting"}
            },
            "embedding_index": {"model": "text-embedding-3-small"},
            "faiss": {"file_count": 0, "files": []},
            "files": [
                {
                    "path": path,
                    "sha256": database_digest,
                    "bytes": len(database),
                    "object_key": database_key,
                }
            ],
        }
        manifest_key = keys.generation_manifest(self.generation_id)
        manifest_digest = self._put_json(manifest_key, manifest)
        pointer = {
            "schema_version": 1,
            "generation_id": self.generation_id,
            "manifest_object_key": manifest_key,
            "manifest_sha256": manifest_digest,
            "production_source_id": "ai-sahakar-prod",
        }
        self._put_json(keys.control_authoritative(), pointer)

    def _job(self):
        return VaultJob.objects.create(
            operation="restore_generation",
            profile=self.profile,
            profile_fingerprint=self.profile.fingerprint,
            dataset_id=self.profile.dataset_id,
            generation_id=self.generation_id,
            idempotency_key=f"restore:{uuid.uuid4()}",
        )

    @override_settings(
        VAULT_UI_PROFILE_CONFIGURATION_ENABLED=True,
        VAULT_ALLOWED_S3_ENDPOINTS=("https://alternate.example",),
        VAULT_CREDENTIAL_ALIASES="readonly=RESTORE_READONLY",
    )
    def test_stored_profile_and_probe_never_persist_secrets(self):
        resolver = lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443))
        ]
        profile = upsert_restore_profile(
            key="alternate",
            display_name="Alternate restore",
            endpoint_origin="https://alternate.example",
            bucket="alternate-artifacts",
            region="test",
            dataset_id="alternate-dataset",
            production_source_id="alternate-source",
            credential_alias="readonly",
            resolver=resolver,
        )
        alternate_client = FakeS3Client()
        alternate_vault = ArtifactVault(
            config=VaultConfig(
                enabled=True,
                endpoint=profile.endpoint_origin,
                bucket=profile.bucket,
                region=profile.region,
                access_key="not-persisted",
                secret_key="not-persisted",
            ),
            client=alternate_client,
        )
        evidence = probe_restore_profile(
            profile, resolver=resolver, vault=alternate_vault
        )
        profile.refresh_from_db()
        self.assertTrue(profile.read_only)
        self.assertEqual(profile.credential_alias, "readonly")
        self.assertNotIn("secret", json.dumps(profile.capability_evidence))
        self.assertTrue(evidence["reachable"])
        self.assertEqual(alternate_client.head_bucket_calls, 1)
        self.assertFalse(alternate_client.objects)

    def test_inventory_verifies_registration_pointer_manifest_objects(self):
        verified = verify_generation(
            self.vault, self.profile, verify_objects=True
        )
        self.assertTrue(verified.authoritative)
        self.assertEqual(verified.file_count, 1)
        self.assertGreater(verified.byte_count, 0)

    def test_inventory_rejects_traversal_before_download(self):
        self._publish_fixture(path="../db.sqlite3")
        with self.assertRaisesMessage(
            InventoryError, "manifest_path_unsafe"
        ):
            verify_generation(self.vault, self.profile)

    def test_inventory_distinguishes_pointer_digest_mismatch(self):
        pointer_key = KeyBuilder(
            self.profile.dataset_id
        ).control_authoritative()
        self.client.objects[pointer_key]["metadata"]["sha256"] = "0" * 64
        with self.assertRaisesMessage(
            InventoryError, "authoritative_pointer_digest_mismatch"
        ):
            verify_generation(self.vault, self.profile)

    @override_settings(
        VAULT_RESTORE_ENABLED=True,
        VAULT_ADMIN_MUTATIONS_ENABLED=True,
    )
    def test_queue_binds_profile_fingerprint_and_idempotency(self):
        first = queue_restore_job(
            profile=self.profile,
            generation_id=self.generation_id,
            idempotency_key="operator-request-1",
        )
        second = queue_restore_job(
            profile=self.profile,
            generation_id=self.generation_id,
            idempotency_key="operator-request-1",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            first.profile_fingerprint, self.profile.fingerprint
        )

    @override_settings(
        VAULT_RESTORE_ENABLED=True,
        VAULT_RESTORE_REQUIRE_SANITIZATION=True,
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=0,
        ACTIVATION_RECOVERY_SUPERADMIN_USERNAME="recovery",
        ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="staging-recovery-password",
    )
    def test_restore_sanitizes_prepared_copy_and_never_activates(self):
        job = self._job()
        with self.settings(
            VAULT_RESTORE_ROOT=self.root / "restore",
            RUNTIME_GENERATIONS_ROOT=self.root / "runtime",
        ):
            workspace = run_restore_job(
                job,
                vault=self.vault,
                run_rehearsal=False,
            )
        workspace.refresh_from_db()
        generation = ArtifactGeneration.objects.get(
            generation_id=self.generation_id
        )
        self.assertEqual(
            workspace.state, RestoreWorkspace.State.ACTIVATION_READY
        )
        self.assertEqual(
            generation.local_presence,
            ArtifactGeneration.LocalPresence.PREPARED,
        )
        self.assertEqual(
            generation.runtime_state,
            ArtifactGeneration.RuntimeState.UNKNOWN,
        )
        quarantine_db = Path(workspace.quarantine_path) / "db.sqlite3"
        runtime_db = Path(workspace.runtime_path) / "db.sqlite3"
        quarantine = sqlite3.connect(
            f"file:{quarantine_db}?mode=ro", uri=True
        )
        runtime = sqlite3.connect(f"file:{runtime_db}?mode=ro", uri=True)
        try:
            original_email = quarantine.execute(
                "SELECT email FROM core_customuser"
            ).fetchone()[0]
            sanitized_email = runtime.execute(
                "SELECT email FROM core_customuser"
            ).fetchone()[0]
            runtime_sessions = runtime.execute(
                "SELECT COUNT(*) FROM django_session"
            ).fetchone()[0]
            runtime_user = runtime.execute(
                "SELECT username, password, is_superuser, role "
                "FROM core_customuser"
            ).fetchone()
        finally:
            quarantine.close()
            runtime.close()
        self.assertEqual(original_email, "operator@example.org")
        self.assertTrue(sanitized_email.endswith(".internal"))
        self.assertEqual(runtime_sessions, 0)
        self.assertEqual(runtime_user[0], "recovery")
        self.assertTrue(
            check_password("staging-recovery-password", runtime_user[1])
        )
        self.assertEqual(runtime_user[2:], (1, "superadmin"))
        self.assertTrue(
            workspace.sanitization_evidence["stats"][
                "recovery_superadmin_provisioned"
            ]
        )
        self.assertTrue(workspace.sanitization_evidence["validated"])
        self.assertEqual(
            (Path(workspace.runtime_path).stat().st_mode & 0o222), 0
        )

    @override_settings(
        VAULT_RESTORE_ENABLED=True,
        VAULT_RESTORE_REQUIRE_SANITIZATION=True,
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=0,
    )
    def test_interrupted_download_pauses_and_reuses_checkpoint(self):
        job = self._job()
        original = self.client.get_object
        attempts = {"count": 0}

        def fail_once(**kwargs):
            if "/blobs/db/" in kwargs["Key"] and attempts["count"] == 0:
                attempts["count"] += 1
                raise FakeS3Error("SlowDown")
            return original(**kwargs)

        with self.settings(
            VAULT_RESTORE_ROOT=self.root / "restore",
            RUNTIME_GENERATIONS_ROOT=self.root / "runtime",
        ):
            with patch.object(
                self.client, "get_object", side_effect=fail_once
            ):
                with self.assertRaisesMessage(
                    RestoreError, "restore_object_read_failed"
                ):
                    run_restore_job(
                        job, vault=self.vault, run_rehearsal=False
                    )
            workspace = RestoreWorkspace.objects.get()
            self.assertEqual(
                workspace.state,
                RestoreWorkspace.State.DOWNLOAD_PAUSED,
            )
            workspace = run_restore_job(
                job, vault=self.vault, run_rehearsal=False
            )
        self.assertEqual(
            workspace.state, RestoreWorkspace.State.ACTIVATION_READY
        )
        step = VaultJobStep.objects.get(
            job=job, phase="restore_download"
        )
        self.assertEqual(step.completed_objects, 1)

    @override_settings(
        VAULT_RESTORE_ENABLED=True,
        VAULT_RESTORE_REQUIRE_SANITIZATION=False,
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=0,
    )
    def test_production_restore_cannot_disable_sanitization(self):
        with self.settings(
            VAULT_RESTORE_ROOT=self.root / "restore",
            RUNTIME_GENERATIONS_ROOT=self.root / "runtime",
        ):
            with self.assertRaisesMessage(
                RestoreError, "production_restore_sanitization_required"
            ):
                run_restore_job(
                    self._job(),
                    vault=self.vault,
                    run_rehearsal=False,
                )

    @override_settings(
        VAULT_RESTORE_ENABLED=True,
        VAULT_RESTORE_REQUIRE_SANITIZATION=True,
        VAULT_RESTORE_MIN_FREE_BYTES=0,
        VAULT_RESTORE_MIN_FREE_INODES=0,
    )
    def test_rehearsal_failure_preserves_quarantine(self):
        job = self._job()
        with self.settings(
            VAULT_RESTORE_ROOT=self.root / "restore",
            RUNTIME_GENERATIONS_ROOT=self.root / "runtime",
        ):
            with patch(
                "vaultops.services.restore.rehearse_migrations",
                side_effect=RehearsalError("migration_rehearsal_failed"),
            ):
                with self.assertRaisesMessage(
                    RestoreError, "migration_rehearsal_failed"
                ):
                    run_restore_job(job, vault=self.vault)
        workspace = RestoreWorkspace.objects.get()
        self.assertEqual(workspace.state, RestoreWorkspace.State.FAILED)
        self.assertTrue(
            (Path(workspace.quarantine_path) / "db.sqlite3").is_file()
        )
        self.assertFalse(workspace.runtime_path)

    @override_settings(VAULT_RESTORE_ENABLED=True)
    def test_worker_dispatches_restore_without_environment_vault(self):
        job = self._job()
        claimed, token = claim_job(job.public_id, worker_id="worker-1")
        prepared = SimpleNamespace(manifest_digest="a" * 64)
        with patch(
            "vaultops.services.sync.run_restore_job",
            return_value=prepared,
        ) as restore:
            result = execute_claimed_job(claimed, token)
        self.assertEqual(result.status, VaultJob.Status.SUCCEEDED)
        self.assertIsNone(restore.call_args.kwargs["vault"])


class RehearsalTests(SimpleTestCase):
    def test_rehearsal_runs_migrations_in_subprocess_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            source.write_bytes(database_bytes())
            workspace = Path(directory) / "workspace"
            with patch(
                "core.rehearsal.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run:
                evidence = rehearse_migrations(
                    source, workspace_path=workspace
                )
        self.assertTrue(evidence["success"])
        self.assertNotIn("source_db", evidence)
        self.assertEqual(
            run.call_args.kwargs["env"]["SQLITE_DB_PATH"],
            str(
                (workspace / "rehearsal" / "db.sqlite3").resolve()
            ),
        )
        self.assertEqual(run.call_args.kwargs["stderr"], -3)

    def test_rehearsal_maps_timeout_to_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            source.write_bytes(database_bytes())
            with patch(
                "core.rehearsal.subprocess.run",
                side_effect=__import__("subprocess").TimeoutExpired(
                    cmd="manage.py", timeout=1
                ),
            ):
                with self.assertRaisesMessage(
                    RehearsalError, "migration_rehearsal_timeout"
                ):
                    rehearse_migrations(
                        source,
                        workspace_path=Path(directory) / "workspace",
                        timeout_seconds=1,
                    )
