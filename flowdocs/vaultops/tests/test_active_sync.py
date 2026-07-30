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

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone

from core import utils as core_utils
from core import candidate_maintenance
from core.artifact_vault import ArtifactVault, VaultConfig
from core.media_quarantine import (
    build_unauthorized_missing_attestation,
    build_unavailable_attestation,
    storage_key_evidence,
)
from core.models import Folder
from core.global_writer import GlobalWriterConflict, release_global_writer
from core.registration import RegistrationError, get_authoritative_pointer
from vaultops.middleware import MUTATING_VIEW_NAMES, SourceMutationBarrierMiddleware
from vaultops.models import (
    ArtifactGeneration,
    ArtifactValidation,
    MutationJournalEntry,
    SourceMutationState,
    SourceSnapshot,
    SyncPolicy,
    VaultConnectionProfile,
    VaultJob,
)
from vaultops.services.mutations import (
    BarrierOwnershipLost,
    SnapshotBarrierActive,
    mutation_scope,
    release_barrier,
    request_barrier,
)
from vaultops.services.jobs import claim_job
from vaultops.services.publication import (
    PromotionError,
    PublicationError,
    promote_candidate,
    publish_snapshot_candidate,
)
from vaultops.services.snapshot import SnapshotError, create_consistent_snapshot
from vaultops.services import snapshot as snapshot_service
from vaultops.services.sync import (
    SyncPolicyError,
    evaluate_sync_scheduler,
    execute_claimed_job,
    materialize_sync_policy,
    queue_sync_job,
)


class FakeS3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.put_count = 0

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        current = self.objects.get(key)
        if kwargs.get("IfNoneMatch") == "*" and current is not None:
            raise FakeS3Error("PreconditionFailed")
        if kwargs.get("IfMatch") is not None:
            if current is None or current["etag"] != kwargs["IfMatch"]:
                raise FakeS3Error("PreconditionFailed")
        body = kwargs.get("Body", b"")
        if hasattr(body, "read"):
            body = body.read()
        body = bytes(body)
        self.put_count += 1
        etag = f'"etag-{self.put_count}"'
        self.objects[key] = {
            "body": body,
            "metadata": dict(kwargs.get("Metadata") or {}),
            "content_type": kwargs.get(
                "ContentType", "application/octet-stream"
            ),
            "etag": etag,
        }
        return {"ETag": etag}

    def get_object(self, **kwargs):
        try:
            item = self.objects[kwargs["Key"]]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {
            "Body": io.BytesIO(item["body"]),
            "Metadata": dict(item["metadata"]),
            "ContentType": item["content_type"],
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
            "ContentType": item["content_type"],
            "ETag": item["etag"],
        }

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)
        return {}


def fake_identity(**overrides):
    values = {
        "dataset_id": "ai-sahakar-test",
        "production_source_id": "source-1",
        "instance_id": "instance-1",
        "deployment_id": "deployment-1",
        "replica_id": "replica-1",
        "app_release_version": "test-release",
        "app_image_digest": "sha256:test",
        "is_authoritative_writer": True,
        "is_backup_writer": True,
        "is_production": True,
        "maintenance_scheduler_enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fake_capabilities():
    return SimpleNamespace(authoritative_publication_allowed=True)


def fake_writer():
    return {
        "writer_epoch": 7,
        "owner_token_hash": hashlib.sha256(b"token").hexdigest(),
        "_token": "token",
        "_etag": '"writer-etag"',
    }


class ActiveSyncTestCase(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.profile = VaultConnectionProfile.objects.create(
            key="production",
            display_name="Environment",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            enabled=True,
            read_only=False,
            environment_locked=True,
            endpoint_origin="https://vault.example",
            bucket="artifacts",
            region="test",
            dataset_id="ai-sahakar-test",
            production_source_id="source-1",
            credential_alias="environment:ARTIFACT_VAULT",
            fingerprint="f" * 64,
        )

    def make_job(self, operation="sync_publish"):
        return VaultJob.objects.create(
            operation=operation,
            profile=self.profile,
            profile_fingerprint=self.profile.fingerprint,
            dataset_id=self.profile.dataset_id,
            idempotency_key=f"{operation}:{uuid.uuid4()}",
        )


@override_settings(
    VAULT_SYNC_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    ENV_IDENTITY=fake_identity(),
)
class MutationBarrierTests(ActiveSyncTestCase):
    def test_mutation_scope_increments_durable_epoch(self):
        with mutation_scope(
            category="media",
            relative_path="pdfs/example.pdf",
            operation="upload",
        ) as outcome:
            pass

        self.assertTrue(outcome.changed)
        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(state.current_epoch, 1)
        self.assertEqual(state.active_mutations, 0)
        entry = MutationJournalEntry.objects.get(
            deployment_id="deployment-1", epoch=1
        )
        self.assertEqual(entry.relative_path, "pdfs/example.pdf")

    def test_active_barrier_blocks_new_mutations_and_checks_owner(self):
        owner = uuid.uuid4()
        state = request_barrier(owner_job_id=owner)
        self.assertEqual(
            state.barrier_state, SourceMutationState.BarrierState.ACTIVE
        )

        with self.assertRaises(SnapshotBarrierActive):
            with mutation_scope(category="media", operation="upload"):
                pass
        with self.assertRaises(BarrierOwnershipLost):
            release_barrier(owner_job_id=uuid.uuid4())

        self.assertTrue(release_barrier(owner_job_id=owner))

    def test_faiss_reads_do_not_create_epochs_but_promotion_does(self):
        folder = SimpleNamespace(pk=9)
        with patch.object(
            core_utils,
            "_build_or_load_faiss_index_for_folder",
            return_value=(None, [], None),
        ) as build:
            core_utils.build_or_load_faiss_index_for_folder(folder)
        self.assertFalse(SourceMutationState.objects.exists())

        promote = build.call_args.kwargs["promote_index"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "candidate.index"
            target = Path(directory) / "folder_9.index"
            source.write_bytes(b"index")
            promote(str(source), str(target))
            self.assertEqual(target.read_bytes(), b"index")

        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(state.current_epoch, 1)

    def test_dashboard_upload_route_is_blocked_during_barrier(self):
        owner = uuid.uuid4()
        request_barrier(owner_job_id=owner)
        try:
            response = self.client.post("/dashboard/")
        finally:
            release_barrier(owner_job_id=owner)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"],
            "snapshot_barrier_active",
        )

    def test_candidate_snapshot_barrier_blocks_concurrent_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            control = root / "control"
            data.mkdir()
            control.mkdir()
            for name in ("media", "faiss", "chroma"):
                (data / name).mkdir()
            database = data / "db.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()

            class Items:
                def exclude(self, **_kwargs):
                    return self

                def values_list(self, field, flat=False):
                    return [1] if field == "pdf_id" else [7]

            job = SimpleNamespace(
                public_id=uuid.uuid4(),
                kind="reindex_selected",
                options={"recovery_set_id": "rs-test"},
                items=Items(),
            )
            parent = SimpleNamespace(
                generation_id="parent",
                manifest_digest="a" * 64,
                pointer_digest="b" * 64,
                runtime_path=str(data),
            )
            original_copy = candidate_maintenance._copy_tree
            mutation_blocked = []

            def copy_while_mutation_attempts(source, target):
                if not mutation_blocked:
                    with self.assertRaises(SnapshotBarrierActive):
                        with mutation_scope(
                            category="media",
                            operation="upload",
                        ):
                            pass
                    mutation_blocked.append(True)
                return original_copy(source, target)

            with (
                override_settings(
                    DATABASES={
                        **settings.DATABASES,
                        "default": {
                            "ENGINE": "django.db.backends.sqlite3",
                            "NAME": str(database),
                        },
                    },
                    DATA_ROOT=data,
                    DATA_CONTROL_ROOT=control,
                    MEDIA_ROOT=data / "media",
                    FAISS_INDEX_DIR=data / "faiss",
                    CHROMA_DIR=data / "chroma",
                    MAINTENANCE_WORKSPACE_ROOT=control / "workspaces",
                ),
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
                    "core.candidate_maintenance._copy_tree",
                    side_effect=copy_while_mutation_attempts,
                ),
            ):
                workspace = candidate_maintenance.create_workspace(job)

            manifest = json.loads(
                (workspace / candidate_maintenance.WORKSPACE_MANIFEST).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(mutation_blocked, [True])
            self.assertEqual(manifest["source"]["mutation_epoch"], 0)
            state = SourceMutationState.objects.get(
                deployment_id="deployment-1"
            )
            self.assertEqual(
                state.barrier_state,
                SourceMutationState.BarrierState.OPEN,
            )


@override_settings(
    VAULT_SYNC_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    ENV_IDENTITY=fake_identity(),
)
class ChangeAwareWebMutationTests(TransactionTestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="mutation-test-operator",
            password="test-only-password",
        )

    def _request(self, response_callable):
        middleware = SourceMutationBarrierMiddleware(response_callable)
        return middleware(self.factory.post("/dashboard/"))

    def _assert_epoch(self, expected):
        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(state.current_epoch, expected)
        self.assertEqual(state.active_mutations, 0)
        self.assertEqual(MutationJournalEntry.objects.count(), expected)

    def test_source_mutating_route_inventory_is_explicit(self):
        self.assertEqual(
            MUTATING_VIEW_NAMES,
            {
                "dashboard",
                "dashboard_folder",
                "save_settings",
                "register",
                "edit_user",
                "toggle_user_status",
                "delete_user",
                "create_folder",
                "rename_folder",
                "delete_folder",
                "update_folder_keywords",
                "add_subcategory",
                "rename_pdf",
                "assign_pdf_owner",
                "delete_pdf",
                "deprecate_pdf",
                "archive_pdf",
                "restore_pdf",
            },
        )

    def test_committed_core_model_save_increments_epoch(self):
        def save_folder(_request):
            Folder.objects.create(name="committed", created_by=self.user)
            return HttpResponse(status=200)

        response = self._request(save_folder)

        self.assertEqual(response.status_code, 200)
        self._assert_epoch(1)

    def test_successful_no_op_request_does_not_increment_epoch(self):
        response = self._request(lambda _request: HttpResponse(status=200))

        self.assertEqual(response.status_code, 200)
        self._assert_epoch(0)

    def test_error_response_retains_committed_change_epoch(self):
        def save_then_reject(_request):
            Folder.objects.create(name="discarded", created_by=self.user)
            return HttpResponse(status=400)

        response = self._request(save_then_reject)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Folder.objects.filter(name="discarded").exists())
        self._assert_epoch(1)

    def test_exception_retains_committed_change_epoch(self):
        def save_then_raise(_request):
            Folder.objects.create(name="errored", created_by=self.user)
            raise RuntimeError("test-only request failure")

        with self.assertRaisesRegex(RuntimeError, "test-only request failure"):
            self._request(save_then_raise)

        self.assertTrue(Folder.objects.filter(name="errored").exists())
        self._assert_epoch(1)

    def test_rolled_back_core_model_save_does_not_increment_epoch(self):
        def rollback_save(_request):
            try:
                with transaction.atomic():
                    Folder.objects.create(name="rolled-back", created_by=self.user)
                    raise RuntimeError("force rollback")
            except RuntimeError:
                pass
            return HttpResponse(status=200)

        response = self._request(rollback_save)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Folder.objects.filter(name="rolled-back").exists())
        self._assert_epoch(0)


@override_settings(
    VAULT_SYNC_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    ENV_IDENTITY=fake_identity(),
)
class SnapshotServiceTests(ActiveSyncTestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.control = self.root / "control"
        for name in (
            "media/pdfs",
            "pdf_cache",
            "faiss_indexes",
            "chroma_db",
            "staticfiles",
        ):
            (self.data / name).mkdir(parents=True, exist_ok=True)
        (self.data / "media/pdfs/example.pdf").write_bytes(b"%PDF-example")
        (self.data / "pdf_cache/cache.bin").write_bytes(b"custody")
        (self.data / "chroma_db/chroma.bin").write_bytes(b"chroma")
        (self.data / "staticfiles/legacy.txt").write_text(
            "custody", encoding="utf-8"
        )
        self.database = self.data / "db.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute(
            "CREATE TABLE core_pdffile ("
            "id INTEGER PRIMARY KEY, folder_id INTEGER, title TEXT, file TEXT, "
            "file_path TEXT, page_chunks TEXT, chunk_embeddings TEXT, "
            "indexed INTEGER, extracted_text TEXT, text_content TEXT, "
            "category TEXT, subject TEXT)"
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def test_snapshot_is_reconciled_frozen_and_evidenced(self):
        job = self.make_job()
        snapshot = create_consistent_snapshot(
            job,
            source_roots={
                "media": self.data / "media",
                "pdf_cache": self.data / "pdf_cache",
                "faiss_indexes": self.data / "faiss_indexes",
                "chroma_db": self.data / "chroma_db",
                "staticfiles": self.data / "staticfiles",
            },
            database_path=self.database,
            snapshot_root=self.control / "snapshots",
        )

        self.assertEqual(snapshot.state, SourceSnapshot.State.FINALIZED)
        workspace = Path(snapshot.workspace_path)
        self.assertTrue((workspace / "db.sqlite3").is_file())
        self.assertTrue((workspace / "media/pdfs/example.pdf").is_file())
        self.assertTrue((workspace / "pdf_cache/cache.bin").is_file())
        self.assertTrue((workspace / "snapshot-evidence.json").is_file())
        self.assertFalse(os.stat(workspace / "db.sqlite3").st_mode & 0o222)
        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(
            state.barrier_state, SourceMutationState.BarrierState.OPEN
        )
        check = sqlite3.connect(
            f"file:{workspace / 'db.sqlite3'}?mode=ro", uri=True
        )
        try:
            self.assertEqual(
                check.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        finally:
            check.close()

    def test_snapshot_attests_unknown_evidence_with_preserved_lifecycle(self):
        connection = sqlite3.connect(self.database)
        for definition in (
            "lifecycle TEXT",
            "media_expected_sha256 TEXT",
            "media_expected_size INTEGER",
            "media_prior_lifecycle TEXT",
        ):
            connection.execute(f"ALTER TABLE core_pdffile ADD COLUMN {definition}")
        connection.execute(
            "INSERT INTO core_pdffile "
            "(id, file, lifecycle, media_expected_sha256, "
            "media_expected_size, media_prior_lifecycle) "
            "VALUES (1, 'pdfs/missing.pdf', 'unavailable', '', NULL, 'uploaded')"
        )
        connection.commit()
        connection.close()

        snapshot = create_consistent_snapshot(
            self.make_job(),
            source_roots={
                "media": self.data / "media",
                "pdf_cache": self.data / "pdf_cache",
                "faiss_indexes": self.data / "faiss_indexes",
                "chroma_db": self.data / "chroma_db",
                "staticfiles": self.data / "staticfiles",
            },
            database_path=self.database,
            snapshot_root=self.control / "snapshots",
        )

        evidence = json.loads(
            (Path(snapshot.workspace_path) / "snapshot-evidence.json").read_text()
        )
        self.assertEqual(evidence["faiss"]["unavailable_documents"]["count"], 1)

    def test_untracked_source_mutation_fails_before_finalization(self):
        job = self.make_job()
        reconcile = snapshot_service._reconcile_tree
        changed = False

        def mutate_after_reconcile(
            category, source_root, workspace, records
        ):
            nonlocal changed
            result = reconcile(category, source_root, workspace, records)
            if category == "media" and not changed:
                changed = True
                (self.data / "media/pdfs/example.pdf").write_bytes(
                    b"%PDF-mutated-outside-barrier"
                )
            return result

        with patch.object(
            snapshot_service,
            "_reconcile_tree",
            side_effect=mutate_after_reconcile,
        ):
            with self.assertRaisesRegex(
                SnapshotError,
                "snapshot_untracked_source_mutation",
            ):
                create_consistent_snapshot(
                    job,
                    source_roots={
                        "media": self.data / "media",
                        "pdf_cache": self.data / "pdf_cache",
                        "faiss_indexes": self.data / "faiss_indexes",
                        "chroma_db": self.data / "chroma_db",
                        "staticfiles": self.data / "staticfiles",
                    },
                    database_path=self.database,
                    snapshot_root=self.control / "snapshots",
                )

        snapshot = SourceSnapshot.objects.get(job=job)
        self.assertEqual(snapshot.state, SourceSnapshot.State.FAILED)
        self.assertEqual(
            snapshot.safe_error_code,
            "snapshot_untracked_source_mutation",
        )
        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(
            state.barrier_state, SourceMutationState.BarrierState.OPEN
        )


@override_settings(
    VAULT_SYNC_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    VAULT_VALIDATION_MAX_AGE_SECONDS=1800,
    ENV_IDENTITY=fake_identity(),
)
class CandidatePublicationTests(ActiveSyncTestCase):
    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        payload = b"database snapshot"
        (self.workspace / "db.sqlite3").write_bytes(payload)
        self.job = self.make_job()
        self.snapshot = SourceSnapshot.objects.create(
            job=self.job,
            deployment_id="deployment-1",
            state=SourceSnapshot.State.FINALIZED,
            initial_epoch=1,
            included_epoch=2,
            snapshot_digest="a" * 64,
            workspace_path=str(self.workspace),
            file_count=1,
            byte_count=len(payload),
            finalized_at=timezone.now(),
        )
        evidence = {
            "snapshot_id": str(self.snapshot.public_id),
            "files": [
                {
                    "path": "db.sqlite3",
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
            "inventory": {
                "schema": {"inventory_schema": "test/v1"},
                "database": {"migrations": {"latest": "0019_sitesetting"}},
                "counts": {"pdf_rows": 0},
            },
            "faiss": {
                "unavailable_documents": build_unavailable_attestation(()),
            },
        }
        (self.workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )
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

    def tearDown(self):
        self.temporary.cleanup()
        super().tearDown()

    def _write_media_inventory(
        self,
        *,
        lifecycle,
        key,
        file_status,
        exists=False,
        unavailable_documents=None,
    ):
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        storage = storage_key_evidence(key)
        pdf = {
            "db_id": 1,
            "exists": exists,
            "file_status": file_status,
            "metadata": {
                "lifecycle": lifecycle,
                "storage_key_status": storage["status"],
                "storage_key_token_sha256": storage["token_sha256"],
                "media_expected_sha256": "",
                "media_expected_size": None,
                "media_prior_lifecycle": (
                    "uploaded" if lifecycle == "unavailable" else ""
                ),
            },
        }
        evidence["inventory"]["pdfs"] = [pdf]
        evidence["inventory"]["counts"]["pdf_rows"] = 1
        evidence["inventory"]["unavailable_documents"] = (
            unavailable_documents or build_unavailable_attestation(())
        )
        evidence["inventory"]["unauthorized_missing_documents"] = (
            build_unauthorized_missing_attestation(
                (
                    {
                        "id": 1,
                        "lifecycle": lifecycle,
                        "file_status": file_status,
                        "storage_key_token_sha256": storage["token_sha256"],
                    },
                )
            )
            if lifecycle != "unavailable" and not exists
            else build_unauthorized_missing_attestation(())
        )
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    def test_publication_rejects_every_non_unavailable_missing_posture_pre_upload(self):
        for label, lifecycle, key, file_status in (
            ("archived", "archived", "pdfs/missing.pdf", "missing"),
            ("deprecated", "deprecated", "pdfs/missing.pdf", "missing"),
            ("blank", "ready", "", "missing-path"),
            ("null", "ready", None, "missing-path"),
            ("unsafe", "ready", "../outside.pdf", "unsafe-path"),
        ):
            with self.subTest(label=label):
                self._write_media_inventory(
                    lifecycle=lifecycle,
                    key=key,
                    file_status=file_status,
                )
                before = self.client.put_count

                with self.assertRaisesMessage(
                    PublicationError,
                    "snapshot_media_missing",
                ):
                    publish_snapshot_candidate(
                        snapshot=self.snapshot,
                        profile=self.profile,
                        job=self.job,
                        vault=self.vault,
                    )

                self.assertEqual(self.client.put_count, before)
                self.assertFalse(ArtifactValidation.objects.exists())

    def test_publication_rejects_inventory_faiss_attestation_drift_pre_upload(self):
        storage = storage_key_evidence("pdfs/missing.pdf")
        unavailable = build_unavailable_attestation(
            (
                {
                    "id": 1,
                    "lifecycle": "unavailable",
                    "storage_key_status": storage["status"],
                    "storage_key_token_sha256": storage["token_sha256"],
                    "expected_sha256": "",
                    "expected_size": None,
                    "prior_lifecycle": "uploaded",
                },
            )
        )
        self._write_media_inventory(
            lifecycle="unavailable",
            key="pdfs/missing.pdf",
            file_status="missing",
            unavailable_documents=unavailable,
        )
        before = self.client.put_count

        with self.assertRaisesMessage(
            PublicationError,
            "snapshot_unavailable_attestation_mismatch",
        ):
            publish_snapshot_candidate(
                snapshot=self.snapshot,
                profile=self.profile,
                job=self.job,
                vault=self.vault,
            )

        self.assertEqual(self.client.put_count, before)
        self.assertFalse(ArtifactValidation.objects.exists())

    @override_settings(
        VAULT_SYNC_MODE="continuous_coalesced",
        VAULT_SYNC_PROMOTION_MODE="manual",
        VAULT_SYNC_QUIET_PERIOD_SECONDS=0,
    )
    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.validate_writer_for_publication",
        side_effect=lambda *args, writer_record=None, **kwargs: writer_record,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    @patch("vaultops.services.sync.materialize_environment_profile")
    def test_real_route_epoch_coalesces_to_one_completed_publication(
        self,
        profile_factory,
        *_publication_mocks,
    ):
        profile_factory.return_value = self.profile
        actor = get_user_model().objects.create_user(
            username="scheduler-route-actor",
            password="test-only-password",
        )

        def create_folder(_request):
            Folder.objects.create(name="Scheduler source change", created_by=actor)
            return HttpResponse(status=200)

        middleware = SourceMutationBarrierMiddleware(create_folder)
        # TestCase wraps each test in an outer transaction, while production
        # autocommit runs this callback before the middleware scope exits.
        with patch(
            "vaultops.middleware.transaction.on_commit",
            side_effect=lambda callback, using=None: callback(),
        ):
            response = middleware(RequestFactory().post("/dashboard/"))

        self.assertEqual(response.status_code, 200)
        state = SourceMutationState.objects.get(deployment_id="deployment-1")
        self.assertEqual(state.current_epoch, 1)

        first = evaluate_sync_scheduler()
        second = evaluate_sync_scheduler()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            VaultJob.objects.filter(operation="sync_publish")
            .exclude(pk=self.job.pk)
            .count(),
            1,
        )

        scheduled_snapshot = SourceSnapshot.objects.create(
            job=first,
            deployment_id="deployment-1",
            state=SourceSnapshot.State.FINALIZED,
            initial_epoch=0,
            included_epoch=state.current_epoch,
            snapshot_digest="b" * 64,
            workspace_path=str(self.workspace),
            file_count=1,
            byte_count=(self.workspace / "db.sqlite3").stat().st_size,
            finalized_at=timezone.now(),
        )
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["snapshot_id"] = str(scheduled_snapshot.public_id)
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        claimed, token = claim_job(first.public_id, worker_id="test-worker")
        completed = execute_claimed_job(claimed, token, vault=self.vault)

        self.assertEqual(
            completed.status,
            VaultJob.Status.SUCCEEDED,
            (completed.safe_error_code, completed.progress),
        )
        self.assertEqual(
            sum(key.endswith("/manifest.json") for key in self.client.objects),
            1,
        )
        policy = SyncPolicy.objects.get(profile=self.profile)
        self.assertEqual(policy.last_completed_epoch, state.current_epoch)

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.validate_writer_for_publication",
        side_effect=lambda *args, writer_record=None, **kwargs: writer_record,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_publication_is_idempotent_candidate_and_never_moves_pointer(
        self, *_mocks
    ):
        candidate = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=self.job,
            vault=self.vault,
        )
        first_put_count = self.client.put_count
        candidate_again = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=self.job,
            vault=self.vault,
        )

        self.assertEqual(
            candidate.vault_state, ArtifactGeneration.VaultState.CANDIDATE
        )
        self.assertEqual(candidate.pk, candidate_again.pk)
        self.assertEqual(self.client.put_count, first_put_count)
        self.assertEqual(
            candidate.manifest["unavailable_documents"],
            build_unavailable_attestation(()),
        )
        publication_validations = ArtifactValidation.objects.filter(
            generation=candidate,
            validation_type="publication",
        )
        self.assertTrue(publication_validations.exists())
        self.assertTrue(
            all(
                validation.evidence["unavailable_documents"]
                == candidate.manifest["unavailable_documents"]
                for validation in publication_validations
            )
        )
        pointer_key = (
            f"datasets/{self.profile.dataset_id}/control/authoritative.json"
        )
        self.assertNotIn(pointer_key, self.client.objects)

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_cancellation_before_upload_does_not_publish_manifest(self, *_mocks):
        with self.assertRaises(PublicationError):
            publish_snapshot_candidate(
                snapshot=self.snapshot,
                profile=self.profile,
                job=self.job,
                vault=self.vault,
                cancellation_check=lambda: True,
            )
        self.assertFalse(
            any(key.endswith("/manifest.json") for key in self.client.objects)
        )

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.validate_writer_for_publication",
        side_effect=lambda *args, writer_record=None, **kwargs: writer_record,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_promotion_requires_a_separate_confirmation(self, *_mocks):
        candidate = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=self.job,
            vault=self.vault,
        )
        promote_job = self.make_job(operation="promote_generation")

        with self.assertRaises(PromotionError) as raised:
            promote_candidate(
                generation=candidate,
                job=promote_job,
                profile=self.profile,
                confirmed=False,
                vault=self.vault,
            )
        self.assertEqual(
            raised.exception.reason_code, "typed_confirmation_required"
        )

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.validate_writer_for_publication",
        side_effect=lambda *args, writer_record=None, **kwargs: writer_record,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_confirmed_promotion_moves_only_the_pointer_by_cas(self, *_mocks):
        candidate = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=self.job,
            vault=self.vault,
        )
        promote_job = self.make_job(operation="promote_generation")

        promoted, pointer = promote_candidate(
            generation=candidate,
            job=promote_job,
            profile=self.profile,
            confirmed=True,
            vault=self.vault,
        )

        self.assertEqual(
            promoted.vault_state,
            ArtifactGeneration.VaultState.AUTHORITATIVE,
        )
        self.assertEqual(pointer["generation_id"], candidate.generation_id)
        pointer_key = (
            f"datasets/{self.profile.dataset_id}/control/authoritative.json"
        )
        self.assertIn(pointer_key, self.client.objects)

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_snapshot_digest_mismatch_stops_before_object_upload(
        self, *_mocks
    ):
        (self.workspace / "db.sqlite3").write_bytes(b"changed after evidence")

        with self.assertRaises(PublicationError) as raised:
            publish_snapshot_candidate(
                snapshot=self.snapshot,
                profile=self.profile,
                job=self.job,
                vault=self.vault,
            )

        self.assertEqual(
            raised.exception.reason_code,
            "snapshot_artifact_digest_mismatch",
        )
        self.assertFalse(
            any("/blobs/" in key for key in self.client.objects)
        )
        self.assertFalse(
            any(key.endswith("/manifest.json") for key in self.client.objects)
        )

    def test_profile_reconfiguration_cannot_redirect_queued_job(self):
        self.profile.fingerprint = "e" * 64
        self.profile.endpoint_origin = "https://replacement.example"
        self.profile.save(
            update_fields=["fingerprint", "endpoint_origin", "updated_at"]
        )

        with (
            patch(
                "vaultops.services.publication.probe_capabilities",
                return_value=fake_capabilities(),
            ),
            patch(
                "vaultops.services.publication.acquire_global_writer",
                return_value=fake_writer(),
            ),
            patch(
                "vaultops.services.publication.release_global_writer",
                return_value=None,
            ),
            self.assertRaises(PublicationError) as raised,
        ):
            publish_snapshot_candidate(
                snapshot=self.snapshot,
                profile=self.profile,
                job=self.job,
                vault=self.vault,
            )

        self.assertEqual(
            raised.exception.reason_code,
            "profile_fingerprint_changed",
        )

    def test_snapshot_symlink_is_rejected_before_vault_access(self):
        database = self.workspace / "db.sqlite3"
        target = self.workspace / "database-real.sqlite3"
        database.rename(target)
        database.symlink_to(target.name)

        with (
            patch(
                "vaultops.services.publication.probe_capabilities",
                return_value=fake_capabilities(),
            ),
            patch(
                "vaultops.services.publication.acquire_global_writer",
                return_value=fake_writer(),
            ),
            patch(
                "vaultops.services.publication.release_global_writer",
                return_value=None,
            ),
            self.assertRaises(PublicationError) as raised,
        ):
            publish_snapshot_candidate(
                snapshot=self.snapshot,
                profile=self.profile,
                job=self.job,
                vault=self.vault,
            )

        self.assertEqual(raised.exception.reason_code, "snapshot_path_unsafe")

    @patch(
        "vaultops.services.publication.release_global_writer",
        return_value=None,
    )
    @patch(
        "vaultops.services.publication.validate_writer_for_publication",
        side_effect=lambda *args, writer_record=None, **kwargs: writer_record,
    )
    @patch(
        "vaultops.services.publication.acquire_global_writer",
        return_value=fake_writer(),
    )
    @patch(
        "vaultops.services.publication.probe_capabilities",
        return_value=fake_capabilities(),
    )
    def test_interrupted_upload_resumes_from_verified_objects(self, *_mocks):
        pdf = self.workspace / "media/example.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-resume")
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["files"].append(
            {
                "path": "media/example.pdf",
                "size_bytes": pdf.stat().st_size,
                "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            }
        )
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        original_put = self.client.put_object
        data_puts = 0

        def interrupt_second_object(**kwargs):
            nonlocal data_puts
            if "/blobs/" in kwargs["Key"]:
                data_puts += 1
                if data_puts == 2:
                    raise FakeS3Error("SlowDown")
            return original_put(**kwargs)

        with patch.object(
            self.client,
            "put_object",
            side_effect=interrupt_second_object,
        ):
            with self.assertRaises(PublicationError):
                publish_snapshot_candidate(
                    snapshot=self.snapshot,
                    profile=self.profile,
                    job=self.job,
                    vault=self.vault,
                )

        checkpoint = self.job.steps.get(phase="uploading").checkpoint
        self.assertEqual(len(checkpoint["objects"]), 1)
        generation_id = VaultJob.objects.get(pk=self.job.pk).generation_id

        candidate = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=VaultJob.objects.get(pk=self.job.pk),
            vault=self.vault,
        )

        self.assertEqual(candidate.generation_id, generation_id)
        self.assertEqual(
            len(self.job.steps.get(phase="uploading").checkpoint["objects"]),
            2,
        )
        self.assertEqual(
            sum(
                key.endswith("/manifest.json")
                for key in self.client.objects
            ),
            1,
        )


@override_settings(
    VAULT_SYNC_ENABLED=True,
    VAULT_MUTATION_TRACKING_ENABLED=True,
    VAULT_SYNC_MODE="continuous_coalesced",
    VAULT_SYNC_PROMOTION_MODE="manual",
    VAULT_SYNC_QUIET_PERIOD_SECONDS=120,
    VAULT_SYNC_INTERVAL_SECONDS=900,
    VAULT_SYNC_MAX_LAG_SECONDS=3600,
    VAULT_DEFAULT_PROFILE="production",
    ENV_IDENTITY=fake_identity(),
)
class SchedulerTests(ActiveSyncTestCase):
    @patch("vaultops.services.sync.materialize_environment_profile")
    def test_same_epoch_coalesces_to_one_job(self, profile_factory):
        profile_factory.return_value = self.profile
        SourceMutationState.objects.create(
            deployment_id="deployment-1",
            current_epoch=4,
            last_mutation_at=timezone.now()
            - timezone.timedelta(minutes=5),
        )

        first = queue_sync_job(trigger="manual")
        second = queue_sync_job(trigger="manual")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            VaultJob.objects.filter(operation="sync_publish").count(), 1
        )

    @patch("vaultops.services.sync.materialize_environment_profile")
    def test_continuous_mode_waits_for_quiet_period(self, profile_factory):
        profile_factory.return_value = self.profile
        SourceMutationState.objects.create(
            deployment_id="deployment-1",
            current_epoch=2,
            last_mutation_at=timezone.now(),
        )

        self.assertIsNone(evaluate_sync_scheduler())
        policy = materialize_sync_policy(self.profile)
        self.assertEqual(policy.pending_epoch, 2)

    @override_settings(VAULT_SYNC_MODE="disabled")
    @patch("vaultops.services.sync.materialize_environment_profile")
    def test_disabled_policy_rejects_manual_queue(self, profile_factory):
        profile_factory.return_value = self.profile

        with self.assertRaises(SyncPolicyError) as raised:
            queue_sync_job(trigger="manual")

        self.assertEqual(
            raised.exception.reason_code,
            "vault_sync_policy_disabled",
        )

    def test_metrics_expose_control_plane_progress(self):
        SourceMutationState.objects.create(
            deployment_id="deployment-1",
            current_epoch=6,
            last_mutation_at=timezone.now(),
        )
        self.make_job()

        response = self.client.get("/health/metrics/")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("pdfsearch_vault_source_mutation_epoch 6.0", body)
        self.assertIn("pdfsearch_vault_job_queue_depth 1.0", body)


class GlobalWriterReleaseTests(ActiveSyncTestCase):
    def test_release_is_conditional_and_cannot_delete_successor(self):
        client = FakeS3Client()
        vault = ArtifactVault(
            config=VaultConfig(
                enabled=True,
                endpoint="https://vault.example",
                bucket="artifacts",
                region="test",
                access_key="access",
                secret_key="secret",
            ),
            client=client,
        )
        key = "datasets/ai-sahakar-test/control/writer.json"
        token = "owner-token"
        record = {
            "dataset_id": "ai-sahakar-test",
            "owner_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "writer_epoch": 3,
            "expires_at": timezone.now().timestamp() + 60,
        }
        data = json.dumps(record).encode()
        response = client.put_object(
            Bucket="artifacts",
            Key=key,
            Body=data,
            Metadata={"sha256": hashlib.sha256(data).hexdigest()},
        )
        owner = {
            **record,
            "_token": token,
            "_etag": response["ETag"],
        }

        release_global_writer(
            vault,
            "ai-sahakar-test",
            writer_record=owner,
        )
        released = json.loads(client.objects[key]["body"])
        self.assertEqual(released["expires_at"], 0)

        successor = {
            **record,
            "owner_token_hash": hashlib.sha256(b"successor").hexdigest(),
            "writer_epoch": 4,
        }
        successor_data = json.dumps(successor).encode()
        client.put_object(
            Bucket="artifacts",
            Key=key,
            Body=successor_data,
            Metadata={"sha256": hashlib.sha256(successor_data).hexdigest()},
        )
        with self.assertRaises(GlobalWriterConflict):
            release_global_writer(
                vault,
                "ai-sahakar-test",
                writer_record=owner,
            )

    def test_release_fails_closed_when_writer_cannot_be_read(self):
        client = FakeS3Client()
        vault = ArtifactVault(
            config=VaultConfig(
                enabled=True,
                endpoint="https://vault.example",
                bucket="artifacts",
                region="test",
                access_key="access",
                secret_key="secret",
            ),
            client=client,
        )
        with patch.object(
            client,
            "get_object",
            side_effect=FakeS3Error("AccessDenied"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "global_writer_read_failed"
            ):
                release_global_writer(
                    vault,
                    "ai-sahakar-test",
                    writer_record={
                        "_token": "owner",
                        "writer_epoch": 1,
                    },
                )

    def test_pointer_read_distinguishes_missing_from_access_denied(self):
        client = FakeS3Client()
        vault = ArtifactVault(
            config=VaultConfig(
                enabled=True,
                endpoint="https://vault.example",
                bucket="artifacts",
                region="test",
                access_key="access",
                secret_key="secret",
            ),
            client=client,
        )
        self.assertIsNone(
            get_authoritative_pointer(vault, "ai-sahakar-test")
        )

        with patch.object(
            client,
            "get_object",
            side_effect=FakeS3Error("AccessDenied"),
        ):
            with self.assertRaisesRegex(
                RegistrationError,
                "authoritative_pointer_read_failed",
            ):
                get_authoritative_pointer(vault, "ai-sahakar-test")
