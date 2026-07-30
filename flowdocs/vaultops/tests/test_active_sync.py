import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
import weakref
from copy import deepcopy
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
from core.maintenance import (
    bind_unavailable_recovery_evidence,
    mark_pdf_unavailable,
    restore_unavailable_pdf,
)
from core.media_quarantine import (
    build_unauthorized_missing_attestation,
    build_unavailable_attestation,
    storage_key_evidence,
)
from core.models import Folder, MaintenanceAuditEvent, PDFFile
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
    VaultJobStep,
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
from vaultops.services import publication as publication_service
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
                "mark_pdf_unavailable",
                "bind_pdf_recovery_evidence",
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
    VAULT_SYNC_MODE="manual",
    VAULT_SYNC_PROMOTION_MODE="manual",
    VAULT_SYNC_QUIET_PERIOD_SECONDS=120,
    VAULT_SYNC_INTERVAL_SECONDS=900,
    VAULT_SYNC_MAX_LAG_SECONDS=3600,
    VAULT_DEFAULT_PROFILE="production",
    ENV_IDENTITY=fake_identity(),
)
class MediaLifecycleMutationTrackingTests(TransactionTestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.media_root = Path(self.temporary.name) / "media"
        (self.media_root / "pdfs").mkdir(parents=True)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="media-mutation-operator",
            password="test-only-password",
        )
        self.folder = Folder.objects.create(
            name="Media mutation",
            created_by=self.user,
        )
        self.pdf = PDFFile.objects.create(
            title="Tracked media",
            folder=self.folder,
            uploaded_by=self.user,
            file="pdfs/tracked.pdf",
            indexed=True,
        )
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

    def tearDown(self):
        self.settings_override.disable()
        self.temporary.cleanup()
        super().tearDown()

    def _request(self, path, transition):
        def response(_request):
            transition()
            return HttpResponse(status=200)

        middleware = SourceMutationBarrierMiddleware(response)
        return middleware(self.factory.post(path))

    def _mark(self, *, exact_evidence=False):
        content = b"%PDF-1.7\nrestored"
        return mark_pdf_unavailable(
            self.pdf,
            requested_by=self.user,
            expected_sha256=(
                hashlib.sha256(content).hexdigest() if exact_evidence else ""
            ),
            expected_size=len(content) if exact_evidence else "",
            reason="missing_after_inventory",
            case_reference="CASE-TRACKED",
        )

    def _assert_epoch(self, expected):
        state = SourceMutationState.objects.filter(
            deployment_id="deployment-1"
        ).first()
        self.assertEqual(state.current_epoch if state else 0, expected)
        self.assertEqual(state.active_mutations if state else 0, 0)
        self.assertEqual(MutationJournalEntry.objects.count(), expected)

    def _assert_no_transition(self, *, lifecycle, event_type):
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, lifecycle)
        self.assertFalse(
            MaintenanceAuditEvent.objects.filter(event_type=event_type).exists()
        )
        self._assert_epoch(0)

    def test_production_database_does_not_wrap_requests_atomically(self):
        self.assertIs(
            settings.DATABASES["default"].get("ATOMIC_REQUESTS", False),
            False,
        )

    def test_outer_atomic_rejects_unavailable_before_commit_or_rollback(self):
        for should_commit in (True, False):
            with self.subTest(outer_commit=should_commit):
                try:
                    with transaction.atomic(using="default"):
                        with self.assertRaisesRegex(
                            core_utils.SearchDataIntegrityError,
                            "media_transition_outer_atomic_unsupported",
                        ):
                            self._mark()
                        if not should_commit:
                            raise RuntimeError("roll back outer transaction")
                except RuntimeError:
                    pass
                self._assert_no_transition(
                    lifecycle="uploaded",
                    event_type="media_unavailable",
                )

    def test_outer_atomic_rollback_rejects_evidence_binding(self):
        PDFFile.objects.filter(pk=self.pdf.pk).update(
            lifecycle="unavailable",
            indexed=False,
            media_prior_lifecycle="uploaded",
            media_quarantine_reason="missing_after_inventory",
            media_case_reference="CASE-PREEXISTING",
            media_observed_at=timezone.now(),
        )

        try:
            with transaction.atomic(using="default"):
                with self.assertRaisesRegex(
                    core_utils.SearchDataIntegrityError,
                    "media_transition_outer_atomic_unsupported",
                ):
                    bind_unavailable_recovery_evidence(
                        self.pdf,
                        requested_by=self.user,
                        expected_sha256="a" * 64,
                        expected_size=17,
                        binding_reason="source_recovery_case",
                        case_reference="RECOVERY-ROLLBACK",
                    )
                raise RuntimeError("roll back outer transaction")
        except RuntimeError:
            pass

        self._assert_no_transition(
            lifecycle="unavailable",
            event_type="media_evidence_bound",
        )
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.media_expected_sha256, "")

    def test_outer_atomic_rollback_rejects_verified_restore(self):
        content = b"%PDF-1.7\nrestored"
        (self.media_root / "pdfs" / "tracked.pdf").write_bytes(content)
        PDFFile.objects.filter(pk=self.pdf.pk).update(
            lifecycle="unavailable",
            indexed=False,
            media_prior_lifecycle="uploaded",
            media_expected_sha256=hashlib.sha256(content).hexdigest(),
            media_expected_size=len(content),
            media_quarantine_reason="missing_after_inventory",
            media_case_reference="CASE-PREEXISTING",
            media_observed_at=timezone.now(),
        )

        try:
            with transaction.atomic(using="default"):
                with self.assertRaisesRegex(
                    core_utils.SearchDataIntegrityError,
                    "media_transition_outer_atomic_unsupported",
                ):
                    restore_unavailable_pdf(self.pdf, requested_by=self.user)
                raise RuntimeError("roll back outer transaction")
        except RuntimeError:
            pass

        self._assert_no_transition(
            lifecycle="unavailable",
            event_type="media_restored",
        )

    def test_committed_unavailable_transition_advances_exactly_once(self):
        response = self._request(
            f"/pdf/{self.pdf.pk}/unavailable/",
            self._mark,
        )

        self.assertEqual(response.status_code, 200)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "unavailable")
        self._assert_epoch(1)

    def test_idempotent_unavailable_transition_does_not_advance_again(self):
        self._mark()
        outcome = self._mark()

        self.assertFalse(outcome.changed)
        self._assert_epoch(1)

    def test_audit_rollback_does_not_advance_epoch(self):
        with patch(
            "core.maintenance._audit",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self._mark()

        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "uploaded")
        self._assert_epoch(0)

    def test_binding_rollback_and_failed_restore_do_not_advance(self):
        self._mark()
        with patch(
            "core.maintenance._audit",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                bind_unavailable_recovery_evidence(
                    self.pdf,
                    requested_by=self.user,
                    expected_sha256="a" * 64,
                    expected_size=17,
                    binding_reason="source_recovery_case",
                    case_reference="RECOVERY-ROLLBACK",
                )
        self._assert_epoch(1)

        with self.assertRaises(core_utils.SearchDataIntegrityError):
            restore_unavailable_pdf(self.pdf, requested_by=self.user)

        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "unavailable")
        self.assertEqual(self.pdf.media_expected_sha256, "")
        self._assert_epoch(1)

    def test_evidence_binding_and_restore_each_advance_once(self):
        self._mark()

        def bind():
            return bind_unavailable_recovery_evidence(
                self.pdf,
                requested_by=self.user,
                expected_sha256=hashlib.sha256(b"%PDF-1.7\nrestored").hexdigest(),
                expected_size=len(b"%PDF-1.7\nrestored"),
                binding_reason="source_recovery_case",
                case_reference="RECOVERY-TRACKED",
            )

        bind()
        second = bind()
        self.assertFalse(second.changed)
        (self.media_root / "pdfs" / "tracked.pdf").write_bytes(
            b"%PDF-1.7\nrestored"
        )
        restore_unavailable_pdf(self.pdf, requested_by=self.user)

        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "uploaded")
        self._assert_epoch(3)

    @patch("vaultops.services.sync.materialize_environment_profile")
    def test_committed_transition_makes_manual_sync_queue_eligible(
        self, profile_factory
    ):
        profile_factory.return_value = self.profile
        SourceMutationState.objects.create(
            deployment_id="deployment-1",
            current_epoch=0,
        )
        policy = materialize_sync_policy(self.profile)
        policy.last_run_at = timezone.now()
        policy.last_completed_epoch = 0
        policy.save(update_fields=["last_run_at", "last_completed_epoch"])
        self.assertIsNone(queue_sync_job(trigger="manual"))

        self._mark()
        job = queue_sync_job(trigger="manual")

        self.assertIsNotNone(job)
        self.assertEqual(job.progress["source_epoch"], 1)
        self.assertEqual(
            SyncPolicy.objects.get(pk=policy.pk).pending_epoch,
            1,
        )


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

    def _add_lifecycle_columns(self):
        connection = sqlite3.connect(self.database)
        for definition in (
            "lifecycle TEXT",
            "media_expected_sha256 TEXT",
            "media_expected_size INTEGER",
            "media_prior_lifecycle TEXT",
        ):
            connection.execute(
                f"ALTER TABLE core_pdffile ADD COLUMN {definition}"
            )
        connection.commit()
        connection.close()

    def _insert_pdf(
        self,
        *,
        pdf_id,
        folder_id,
        lifecycle,
        chunks,
        embeddings,
        file_name="pdfs/example.pdf",
        indexed=True,
    ):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "INSERT INTO core_pdffile "
            "(id, folder_id, file, lifecycle, page_chunks, chunk_embeddings, "
            "indexed, media_expected_sha256, media_expected_size, "
            "media_prior_lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '', NULL, ?)",
            (
                pdf_id,
                folder_id,
                file_name,
                lifecycle,
                json.dumps(chunks),
                json.dumps(embeddings),
                indexed,
                "uploaded" if lifecycle == "unavailable" else "",
            ),
        )
        connection.commit()
        connection.close()

    def _snapshot(self, **kwargs):
        return create_consistent_snapshot(
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
            **kwargs,
        )

    def test_healthy_faiss_is_copied_without_rewrite(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 2.0]],
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        index = core_utils.faiss.IndexFlatIP(2)
        index.add(core_utils.np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))
        core_utils.faiss.write_index(index, str(source_path))
        source_bytes = source_path.read_bytes()
        database_bytes = self.database.read_bytes()

        snapshot = self._snapshot()

        workspace = Path(snapshot.workspace_path)
        self.assertEqual(
            (workspace / "faiss_indexes/folder_7.index").read_bytes(),
            source_bytes,
        )
        self.assertEqual(source_path.read_bytes(), source_bytes)
        self.assertEqual(self.database.read_bytes(), database_bytes)
        evidence = json.loads(
            (workspace / "snapshot-evidence.json").read_text()
        )
        expected_fingerprint = (
            snapshot_service.snapshot_configuration_fingerprint()
        )
        self.assertEqual(
            evidence["configuration_fingerprint"],
            expected_fingerprint,
        )
        self.assertEqual(
            json.loads(
                (workspace / "snapshot-configuration.json").read_text()
            ),
            expected_fingerprint,
        )
        self.assertEqual(
            snapshot.evidence["configuration_fingerprint"],
            expected_fingerprint,
        )
        folder = evidence["faiss_reconciliation"]["folders"]["7"]
        self.assertEqual(folder["disposition"], "copied")
        self.assertEqual(folder["vector_count"], 2)

    def test_unindexed_empty_document_remains_debt_not_faiss_input(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["searchable"],
            embeddings=[[1.0, 0.0]],
        )
        (self.data / "media/pdfs/debt.pdf").write_bytes(b"%PDF-debt")
        self._insert_pdf(
            pdf_id=2,
            folder_id=7,
            lifecycle="uploaded",
            chunks=[],
            embeddings=[],
            file_name="pdfs/debt.pdf",
            indexed=False,
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        index = core_utils.faiss.IndexFlatIP(2)
        index.add(
            core_utils.np.asarray([[1.0, 0.0]], dtype="float32")
        )
        core_utils.faiss.write_index(index, str(source_path))
        source_index_bytes = source_path.read_bytes()
        source_database_bytes = self.database.read_bytes()

        snapshot = self._snapshot()

        workspace = Path(snapshot.workspace_path)
        with sqlite3.connect(workspace / "db.sqlite3") as connection:
            rows = connection.execute(
                "SELECT id, indexed, page_chunks, chunk_embeddings "
                "FROM core_pdffile ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1], (2, 0, "[]", "[]"))
        self.assertEqual(source_path.read_bytes(), source_index_bytes)
        self.assertEqual(self.database.read_bytes(), source_database_bytes)
        evidence = json.loads(
            (workspace / "snapshot-evidence.json").read_text()
        )
        reconciliation = evidence["faiss_reconciliation"]
        self.assertEqual(reconciliation["pdf_count"], 1)
        self.assertEqual(reconciliation["vector_count"], 1)
        self.assertEqual(
            reconciliation["folders"]["7"]["pdf_count"],
            1,
        )
        self.assertEqual(
            reconciliation["folders"]["7"]["vector_count"],
            1,
        )
        self.assertEqual(evidence["inventory"]["counts"]["pdf_rows"], 2)
        self.assertEqual(
            publication_service._validate_faiss_reconciliation_evidence(
                evidence,
                trusted_reconciliation=reconciliation,
            ),
            reconciliation,
        )

    def test_indexed_empty_document_still_fails_closed(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="uploaded",
            chunks=[],
            embeddings=[],
            indexed=True,
        )
        source_database_bytes = self.database.read_bytes()

        with self.assertRaisesRegex(
            SnapshotError,
            "snapshot_searchable_embeddings_invalid",
        ):
            self._snapshot()

        self.assertEqual(self.database.read_bytes(), source_database_bytes)

    def test_legacy_schema_without_indexed_column_uses_lifecycle(self):
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE core_pdffile ("
                "id INTEGER PRIMARY KEY, folder_id INTEGER, lifecycle TEXT, "
                "page_chunks TEXT, chunk_embeddings TEXT)"
            )
            connection.execute(
                "INSERT INTO core_pdffile VALUES "
                "(1, 7, 'ready', '[\"one\"]', '[[1.0, 0.0]]')"
            )
            clause = snapshot_service._searchable_sql_contract(connection)
            folder_ids, totals = (
                snapshot_service._preflight_searchable_embeddings(
                    connection,
                    clause,
                )
            )
        finally:
            connection.close()

        self.assertIn("lifecycle IN", clause)
        self.assertNotIn("indexed", clause)
        self.assertEqual(folder_ids, [7])
        self.assertEqual(totals["pdf_count"], 1)

    def test_same_shape_wrong_faiss_values_are_rebuilt(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 2.0]],
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        wrong = core_utils.faiss.IndexFlatIP(2)
        wrong.add(
            core_utils.np.asarray(
                [[0.5, 0.5], [0.25, 0.75]],
                dtype="float32",
            )
        )
        core_utils.faiss.write_index(wrong, str(source_path))
        source_bytes = source_path.read_bytes()

        snapshot = self._snapshot()

        evidence = json.loads(
            (
                Path(snapshot.workspace_path) / "snapshot-evidence.json"
            ).read_text()
        )
        self.assertEqual(
            evidence["faiss_reconciliation"]["folders"]["7"]["disposition"],
            "rebuilt",
        )
        self.assertEqual(source_path.read_bytes(), source_bytes)

    def test_near_tolerance_faiss_values_are_rebuilt_exactly(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 1.0]],
        )
        expected = core_utils.np.asarray(
            [[2**-0.5, 2**-0.5]], dtype="float32"
        )
        near = expected.copy()
        near[0, 0] = core_utils.np.nextafter(
            near[0, 0],
            core_utils.np.float32(1.0),
        )
        self.assertTrue(core_utils.np.allclose(near, expected))
        self.assertNotEqual(near.tobytes(), expected.tobytes())
        source_path = self.data / "faiss_indexes/folder_7.index"
        index = core_utils.faiss.IndexFlatIP(2)
        index.add(near)
        core_utils.faiss.write_index(index, str(source_path))

        snapshot = self._snapshot()

        evidence = json.loads(
            (
                Path(snapshot.workspace_path) / "snapshot-evidence.json"
            ).read_text()
        )
        self.assertEqual(
            evidence["faiss_reconciliation"]["folders"]["7"]["disposition"],
            "rebuilt",
        )

    def test_same_shape_reordered_faiss_values_are_rebuilt(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 2.0]],
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        reordered = core_utils.faiss.IndexFlatIP(2)
        reordered.add(
            core_utils.np.asarray(
                [[0.0, 1.0], [1.0, 0.0]],
                dtype="float32",
            )
        )
        core_utils.faiss.write_index(reordered, str(source_path))

        snapshot = self._snapshot()

        candidate = core_utils.faiss.read_index(
            str(
                Path(snapshot.workspace_path)
                / "faiss_indexes/folder_7.index"
            )
        )
        self.assertTrue(
            core_utils.np.allclose(
                candidate.reconstruct_n(0, 2),
                core_utils.np.asarray(
                    [[1.0, 0.0], [0.0, 1.0]],
                    dtype="float32",
                ),
            )
        )

    def test_stale_unavailable_vectors_are_rebuilt_only_in_snapshot(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 2.0]],
        )
        self._insert_pdf(
            pdf_id=2,
            folder_id=7,
            lifecycle="unavailable",
            chunks=["removed"],
            embeddings=[[1.0, 1.0]],
            file_name="pdfs/missing.pdf",
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        stale = core_utils.faiss.IndexFlatIP(2)
        stale.add(
            core_utils.np.asarray(
                [[1.0, 0.0], [0.0, 1.0], [0.707, 0.707]],
                dtype="float32",
            )
        )
        core_utils.faiss.write_index(stale, str(source_path))
        source_bytes = source_path.read_bytes()
        database_bytes = self.database.read_bytes()

        with patch(
            "core.utils.create_embeddings_for_texts",
            side_effect=AssertionError("external embeddings are forbidden"),
        ):
            snapshot = self._snapshot()

        workspace = Path(snapshot.workspace_path)
        rebuilt_path = workspace / "faiss_indexes/folder_7.index"
        rebuilt = core_utils.faiss.read_index(str(rebuilt_path))
        self.assertEqual(int(rebuilt.ntotal), 2)
        self.assertEqual(int(rebuilt.d), 2)
        self.assertEqual(source_path.read_bytes(), source_bytes)
        self.assertEqual(self.database.read_bytes(), database_bytes)
        evidence = json.loads(
            (workspace / "snapshot-evidence.json").read_text()
        )
        folder = evidence["faiss_reconciliation"]["folders"]["7"]
        self.assertEqual(folder["disposition"], "rebuilt")
        self.assertEqual(folder["previous"]["vector_count"], 3)
        self.assertEqual(folder["vector_count"], 2)
        self.assertEqual(
            folder["sha256"],
            hashlib.sha256(rebuilt_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            snapshot.evidence["faiss_reconciliation"]["folders"]["7"][
                "disposition"
            ],
            "rebuilt",
        )

    def test_corrupt_searchable_embeddings_fail_without_source_writes(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[float("nan"), 0.0]],
        )
        database_bytes = self.database.read_bytes()

        with self.assertRaisesRegex(
            SnapshotError,
            "snapshot_searchable_embeddings_invalid",
        ):
            self._snapshot()

        self.assertEqual(self.database.read_bytes(), database_bytes)
        failed = SourceSnapshot.objects.latest("created_at")
        self.assertEqual(failed.state, SourceSnapshot.State.FAILED)
        self.assertEqual(
            failed.safe_error_code,
            "snapshot_searchable_embeddings_invalid",
        )

    def test_huge_finite_embeddings_normalize_without_overflow(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1e308, -1e308]],
        )

        snapshot = self._snapshot()

        candidate = core_utils.faiss.read_index(
            str(
                Path(snapshot.workspace_path)
                / "faiss_indexes/folder_7.index"
            )
        )
        vector = candidate.reconstruct_n(0, 1)
        self.assertTrue(core_utils.np.isfinite(vector).all())
        self.assertGreater(float(core_utils.np.linalg.norm(vector)), 0)

    def test_zero_searchable_folder_index_is_removed_only_from_candidate(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="unavailable",
            chunks=["removed"],
            embeddings=[[1.0, 0.0]],
            file_name="pdfs/missing.pdf",
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        stale = core_utils.faiss.IndexFlatIP(2)
        stale.add(
            core_utils.np.asarray([[1.0, 0.0]], dtype="float32")
        )
        core_utils.faiss.write_index(stale, str(source_path))
        source_bytes = source_path.read_bytes()

        snapshot = self._snapshot()

        workspace = Path(snapshot.workspace_path)
        self.assertFalse(
            (workspace / "faiss_indexes/folder_7.index").exists()
        )
        self.assertEqual(source_path.read_bytes(), source_bytes)
        evidence = json.loads(
            (workspace / "snapshot-evidence.json").read_text()
        )
        self.assertEqual(
            evidence["faiss_reconciliation"]["folders"]["7"],
            {
                "disposition": "removed",
                "pdf_count": 0,
                "vector_count": 0,
            },
        )
        self.assertEqual(evidence["inventory"]["faiss"]["files"], [])
        self.assertNotIn(
            "faiss_indexes/folder_7.index",
            {item["path"] for item in evidence["files"]},
        )
        self.assertEqual(
            publication_service._validate_faiss_reconciliation_evidence(
                evidence,
                trusted_reconciliation=evidence["faiss_reconciliation"],
            ),
            evidence["faiss_reconciliation"],
        )

    @override_settings(VAULT_SNAPSHOT_FAISS_MAX_VECTORS=1)
    def test_candidate_rebuild_vector_bound_fails_closed(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
        )

        with self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_vector_limit_exceeded",
        ):
            self._snapshot()

    def test_pdf_batch_budget_rejects_before_numpy_materialization(self):
        with patch.object(
            snapshot_service.np,
            "asarray",
            side_effect=AssertionError("vectors must not be materialized"),
        ), patch.object(
            snapshot_service.np,
            "vstack",
            side_effect=AssertionError("batch must not be allocated"),
        ), self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_vector_limit_exceeded",
        ):
            snapshot_service._normalized_pdf_batch(
                json.dumps(["one", "two"]),
                json.dumps([[1.0, 0.0], [0.0, 1.0]]),
                cancellation_check=lambda: False,
                remaining_vectors=1,
                remaining_bytes=1024,
            )

    @override_settings(VAULT_SNAPSHOT_FAISS_MAX_BYTES=1)
    def test_candidate_index_file_bound_precedes_faiss_read(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )
        candidate_root = self.control / "bounded-candidate-faiss"
        candidate_root.mkdir(parents=True)
        (candidate_root / "folder_7.index").write_bytes(b"oversized")

        with patch.object(
            core_utils.faiss,
            "read_index",
            side_effect=AssertionError("index must not be loaded"),
        ), self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_byte_limit_exceeded",
        ):
            snapshot_service._reconcile_candidate_faiss(
                self.database,
                candidate_root,
                cancellation_check=lambda: False,
            )

    @override_settings(VAULT_SNAPSHOT_FAISS_MAX_VECTORS=1)
    def test_candidate_vector_bound_precedes_faiss_add(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one", "two"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
        )
        candidate_root = self.control / "bounded-add-faiss"
        candidate_root.mkdir(parents=True)

        with patch.object(
            core_utils.faiss,
            "IndexFlatIP",
            side_effect=AssertionError("index must not be allocated"),
        ), self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_vector_limit_exceeded",
        ):
            snapshot_service._reconcile_candidate_faiss(
                self.database,
                candidate_root,
                cancellation_check=lambda: False,
            )

    @override_settings(VAULT_SNAPSHOT_FAISS_MAX_PDFS=0)
    def test_candidate_rebuild_row_bound_precedes_json_parse(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )

        with patch(
            "vaultops.services.snapshot.json.loads",
            side_effect=AssertionError("JSON must not be parsed"),
        ), self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_pdf_limit_exceeded",
        ):
            self._snapshot()

    @override_settings(VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES=2)
    def test_candidate_rebuild_cell_bound_precedes_json_parse(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )

        with patch(
            "vaultops.services.snapshot.json.loads",
            side_effect=AssertionError("JSON must not be parsed"),
        ), self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_cell_limit_exceeded",
        ):
            self._snapshot()

    def test_production_sized_pdf_json_is_within_default_preflight_cap(self):
        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0]

            def __iter__(self):
                return iter(self.rows)

        class PreflightConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql):
                self.calls.append(sql)
                if "COUNT(*)" in sql:
                    # Observed legitimate 85,794,946-byte embedding cell.
                    return Cursor([(1, 85_800_000, 85_794_946)])
                return Cursor([(7,)])

        connection = PreflightConnection()

        folder_ids, totals = snapshot_service._preflight_searchable_embeddings(
            connection,
            "AND lifecycle IN ('uploaded', 'processing', 'ready') ",
        )

        self.assertEqual(
            settings.VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES,
            134_217_728,
        )
        self.assertEqual(folder_ids, [7])
        self.assertEqual(totals["pdf_count"], 1)
        self.assertEqual(totals["source_json_bytes"], 85_800_000)
        self.assertEqual(len(connection.calls), 2)

    def test_pdf_json_above_default_cap_fails_before_row_iteration(self):
        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0]

        class PreflightConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql):
                self.calls.append(sql)
                if "COUNT(*)" in sql:
                    return Cursor([(1, 134_217_729, 134_217_729)])
                raise AssertionError("folder rows must not be requested")

        connection = PreflightConnection()

        with self.assertRaisesRegex(
            SnapshotError,
            "snapshot_faiss_rebuild_cell_limit_exceeded",
        ):
            snapshot_service._preflight_searchable_embeddings(
                connection,
                "AND lifecycle IN ('uploaded', 'processing', 'ready') ",
            )

        self.assertEqual(len(connection.calls), 1)

    def test_candidate_rebuild_cancellation_removes_partial_file(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )
        candidate_root = self.control / "candidate-faiss"
        candidate_root.mkdir(parents=True)

        with self.assertRaisesRegex(SnapshotError, "snapshot_cancelled"):
            snapshot_service._reconcile_candidate_faiss(
                self.database,
                candidate_root,
                cancellation_check=lambda: True,
            )

        self.assertEqual(list(candidate_root.glob("*.partial")), [])

    def test_candidate_rebuild_write_failure_never_changes_source(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )
        source_path = self.data / "faiss_indexes/folder_7.index"
        stale = core_utils.faiss.IndexFlatIP(2)
        stale.add(
            core_utils.np.asarray(
                [[1.0, 0.0], [0.0, 1.0]],
                dtype="float32",
            )
        )
        core_utils.faiss.write_index(stale, str(source_path))
        source_bytes = source_path.read_bytes()

        with patch.object(
            core_utils.faiss,
            "write_index",
            side_effect=RuntimeError("worker stopped"),
        ):
            with self.assertRaisesRegex(
                SnapshotError,
                "snapshot_faiss_rebuild_failed",
            ):
                self._snapshot()

        self.assertEqual(source_path.read_bytes(), source_bytes)
        failed = SourceSnapshot.objects.latest("created_at")
        self.assertEqual(failed.state, SourceSnapshot.State.FAILED)
        incomplete = Path(failed.workspace_path)
        self.assertEqual(list(incomplete.rglob("*.partial")), [])

    def test_rebuilt_index_is_released_before_readback_verification(self):
        self._add_lifecycle_columns()
        self._insert_pdf(
            pdf_id=1,
            folder_id=7,
            lifecycle="ready",
            chunks=["one"],
            embeddings=[[1.0, 0.0]],
        )
        candidate_root = self.control / "resident-faiss"
        candidate_root.mkdir(parents=True)
        original_write = core_utils.faiss.write_index
        original_read = core_utils.faiss.read_index
        captured = {}

        def recording_write(index, path):
            captured["rebuilt"] = weakref.ref(index)
            return original_write(index, path)

        def bounded_read(path):
            if str(path).endswith(".partial"):
                self.assertIsNone(captured["rebuilt"]())
            return original_read(path)

        with patch.object(
            core_utils.faiss,
            "write_index",
            new=recording_write,
        ), patch.object(
            core_utils.faiss,
            "read_index",
            new=bounded_read,
        ):
            snapshot_service._reconcile_candidate_faiss(
                self.database,
                candidate_root,
                cancellation_check=lambda: False,
            )

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
        size_bytes=None,
        unavailable_documents=None,
    ):
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        storage = storage_key_evidence(key)
        pdf = {
            "db_id": 1,
            "exists": exists,
            "file_status": file_status,
            "size_bytes": size_bytes,
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
    def test_publication_accepts_verified_legacy_media_within_custody_cap(
        self, *_mocks
    ):
        media = self.workspace / "media" / "pdfs" / "legacy.pdf"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"%PDF-1.7\ncanonical legacy fixture")
        media_digest = hashlib.sha256(media.read_bytes()).hexdigest()
        self._write_media_inventory(
            lifecycle="ready",
            key="pdfs/legacy.pdf",
            file_status="verified",
            exists=True,
            size_bytes=media.stat().st_size,
        )
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["files"].append(
            {
                "path": "media/pdfs/legacy.pdf",
                "size_bytes": media.stat().st_size,
                "sha256": media_digest,
            }
        )
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

        candidate = publish_snapshot_candidate(
            snapshot=self.snapshot,
            profile=self.profile,
            job=self.job,
            vault=self.vault,
        )

        self.assertEqual(
            candidate.vault_state,
            ArtifactGeneration.VaultState.CANDIDATE,
        )
        self.assertTrue(
            ArtifactValidation.objects.filter(
                generation=candidate,
                validation_type="publication",
            ).exists()
        )
        published_pdf = [
            item
            for item in self.client.objects.values()
            if item["content_type"] == "application/pdf"
        ]
        self.assertEqual(len(published_pdf), 1)
        self.assertEqual(published_pdf[0]["body"], media.read_bytes())

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

    def test_reconciliation_evidence_requires_exact_inventory_bijection(self):
        digest = "a" * 64
        valid = {
            "inventory": {
                "faiss": {
                    "files": [
                        {
                            "path": "faiss_indexes/folder_7.index",
                            "sha256": digest,
                            "faiss": {
                                "loadable": True,
                                "vector_count": 2,
                                "dimensions": 2,
                            },
                        }
                    ]
                }
            },
            "faiss_reconciliation": {
                "schema": 1,
                "source": "stored_embeddings",
                "source_folders": [],
                "source_folders_digest": hashlib.sha256(b"[]").hexdigest(),
                "pdf_count": 1,
                "vector_count": 2,
                "vector_bytes": 16,
                "source_json_bytes": 40,
                "folders": {
                    "7": {
                        "disposition": "rebuilt",
                        "pdf_count": 1,
                        "vector_count": 2,
                        "dimensions": 2,
                        "sha256": digest,
                    }
                },
            },
        }
        self.assertEqual(
            publication_service._validate_faiss_reconciliation_evidence(
                valid,
                trusted_reconciliation=valid["faiss_reconciliation"],
            ),
            valid["faiss_reconciliation"],
        )

        duplicate = deepcopy(valid)
        duplicate["inventory"]["faiss"]["files"].append(
            deepcopy(duplicate["inventory"]["faiss"]["files"][0])
        )
        missing = deepcopy(valid)
        missing["inventory"]["faiss"]["files"] = []
        arbitrary_removal = deepcopy(valid)
        arbitrary_removal["faiss_reconciliation"]["folders"] = {
            "folder-seven": {
                "disposition": "removed",
                "pdf_count": 0,
                "vector_count": 0,
            }
        }
        forged_canonical_removal = deepcopy(valid)
        forged_canonical_removal["faiss_reconciliation"]["folders"]["999"] = {
            "disposition": "removed",
            "pdf_count": 0,
            "vector_count": 0,
        }
        duplicate_source = deepcopy(valid)
        duplicate_source["faiss_reconciliation"]["source_folders"] = [
            {"folder_id": "999", "sha256": "b" * 64},
            {"folder_id": "999", "sha256": "b" * 64},
        ]
        forged_source_and_removal = deepcopy(valid)
        forged_source_and_removal["faiss_reconciliation"][
            "source_folders"
        ] = [{"folder_id": "999", "sha256": "b" * 64}]
        forged_source_and_removal["faiss_reconciliation"][
            "source_folders_digest"
        ] = hashlib.sha256(
            json.dumps(
                forged_source_and_removal["faiss_reconciliation"][
                    "source_folders"
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        forged_source_and_removal["faiss_reconciliation"]["folders"]["999"] = {
            "disposition": "removed",
            "pdf_count": 0,
            "vector_count": 0,
        }
        altered_source_digest = deepcopy(valid)
        altered_source_digest["faiss_reconciliation"][
            "source_folders_digest"
        ] = "b" * 64
        wrong_totals = deepcopy(valid)
        wrong_totals["faiss_reconciliation"]["vector_bytes"] = 8
        for label, evidence in (
            ("duplicate", duplicate),
            ("missing", missing),
            ("arbitrary_removal", arbitrary_removal),
            ("forged_canonical_removal", forged_canonical_removal),
            ("duplicate_source", duplicate_source),
            ("forged_source_and_removal", forged_source_and_removal),
            ("altered_source_digest", altered_source_digest),
            ("wrong_totals", wrong_totals),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                PublicationError,
                "snapshot_faiss_reconciliation_invalid",
            ):
                publication_service._validate_faiss_reconciliation_evidence(
                    evidence,
                    trusted_reconciliation=valid["faiss_reconciliation"],
                )
        with override_settings(VAULT_SNAPSHOT_FAISS_MAX_VECTORS=1):
            with self.assertRaisesRegex(
                PublicationError,
                "snapshot_faiss_reconciliation_invalid",
            ):
                publication_service._validate_faiss_reconciliation_evidence(
                    valid,
                    trusted_reconciliation=valid["faiss_reconciliation"],
                )

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
        fingerprint = snapshot_service.snapshot_configuration_fingerprint()
        canonical_workspace = (
            self.workspace
            / (
                f"{scheduled_snapshot.public_id}-"
                f"{scheduled_snapshot.snapshot_digest[:12]}"
            )
        )
        canonical_workspace.mkdir()
        (canonical_workspace / "db.sqlite3").write_bytes(
            (self.workspace / "db.sqlite3").read_bytes()
        )
        evidence["configuration_fingerprint"] = fingerprint
        (canonical_workspace / "snapshot-evidence.json").write_text(
            json.dumps(evidence),
            encoding="utf-8",
        )
        (canonical_workspace / "snapshot-configuration.json").write_text(
            json.dumps(fingerprint, sort_keys=True),
            encoding="utf-8",
        )
        scheduled_snapshot.workspace_path = str(canonical_workspace)
        scheduled_snapshot.evidence = {
            "snapshot_schema": 1,
            "evidence_path": "snapshot-evidence.json",
            "configuration_path": "snapshot-configuration.json",
            "configuration_fingerprint": fingerprint,
        }
        scheduled_snapshot.save(
            update_fields=["workspace_path", "evidence", "updated_at"]
        )
        VaultJobStep.objects.create(
            job=first,
            phase="snapshot",
            status=VaultJobStep.Status.COMPLETED,
            checkpoint={
                "snapshot_id": str(scheduled_snapshot.public_id),
                "snapshot_digest": scheduled_snapshot.snapshot_digest,
                "included_epoch": scheduled_snapshot.included_epoch,
                "configuration_fingerprint_sha256": fingerprint["sha256"],
            },
            finished_at=timezone.now(),
        )
        claimed, token = claim_job(first.public_id, worker_id="test-worker")
        with override_settings(VAULT_SNAPSHOT_ROOT=self.workspace):
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
        faiss_path = self.workspace / "faiss_indexes/folder_7.index"
        faiss_path.parent.mkdir()
        index = core_utils.faiss.IndexFlatIP(2)
        index.add(
            core_utils.np.asarray(
                [[1.0, 0.0], [0.0, 1.0]],
                dtype="float32",
            )
        )
        core_utils.faiss.write_index(index, str(faiss_path))
        faiss_digest = hashlib.sha256(faiss_path.read_bytes()).hexdigest()
        evidence_path = self.workspace / "snapshot-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["files"].append(
            {
                "path": "faiss_indexes/folder_7.index",
                "size_bytes": faiss_path.stat().st_size,
                "sha256": faiss_digest,
            }
        )
        evidence["inventory"]["faiss"] = {
            "files": [
                {
                    "path": "faiss_indexes/folder_7.index",
                    "size_bytes": faiss_path.stat().st_size,
                    "sha256": faiss_digest,
                    "faiss": {
                        "loadable": True,
                        "vector_count": 2,
                        "dimensions": 2,
                    },
                }
            ]
        }
        evidence["faiss_reconciliation"] = {
            "schema": 1,
            "source": "stored_embeddings",
            "source_folders": [],
            "source_folders_digest": hashlib.sha256(b"[]").hexdigest(),
            "pdf_count": 1,
            "vector_count": 2,
            "vector_bytes": 16,
            "source_json_bytes": 64,
            "folders": {
                "7": {
                    "disposition": "rebuilt",
                    "pdf_count": 1,
                    "vector_count": 2,
                    "dimensions": 2,
                    "sha256": faiss_digest,
                }
            },
        }
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        self.snapshot.evidence = {
            **self.snapshot.evidence,
            "faiss_reconciliation": evidence["faiss_reconciliation"],
        }
        self.snapshot.save(update_fields=["evidence", "updated_at"])
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
        self.assertEqual(
            candidate.manifest["faiss_reconciliation"],
            evidence["faiss_reconciliation"],
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
                and validation.evidence["faiss_reconciliation"]
                == candidate.manifest["faiss_reconciliation"]
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
