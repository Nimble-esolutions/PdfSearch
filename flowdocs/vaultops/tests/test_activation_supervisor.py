import io
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import Folder, PDFFile
from core.candidate_maintenance import (
    CandidateMaintenanceError,
    _verified_mutable_source_runtime_identity,
)
from core.recovery_auth import RecoveryAuthenticationError
from runtime_paths_cli import main as runtime_paths_main
from runtime_supervisor import RuntimeSupervisor, SupervisorError
from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    RestoreWorkspace,
    RuntimePointerObservation,
    VaultConnectionProfile,
)
from vaultops.runtime_control import (
    RuntimeControlError,
    atomic_write_json,
    build_runtime_pointer,
    read_runtime_pointer,
    read_signed_document,
    resolve_runtime_from_env,
    runtime_control_paths,
    sign_document,
    validate_runtime_workspace,
)
from vaultops.runtime_verification_contract import (
    write_runtime_verification_failure,
    write_runtime_verification_success,
)
from core.media_quarantine import (
    build_unavailable_attestation,
    storage_key_evidence,
)
from vaultops.services.activation import (
    ActivationCoordinatorError,
    prepare_previous_runtime_rollback,
    reconcile_activation_result,
    schedule_activation,
)
from vaultops.services.read_model import _rollback_capability


SIGNING_KEY = "test-signing-key-with-at-least-32-characters"
DEPLOYMENT_ID = "staging-deployment"
CURRENT_GENERATION = "current-generation"
TARGET_GENERATION = "target-generation"
CURRENT_DIGEST = "a" * 64
TARGET_DIGEST = "b" * 64


def staging_identity(**overrides):
    values = {
        "is_production": False,
        "app_env": SimpleNamespace(value="staging"),
        "deployment_id": DEPLOYMENT_ID,
        "app_release_version": "release-1",
        "app_image_digest": "sha256:image-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def create_runtime(
    root,
    generation_id,
    manifest_digest,
    *,
    username="recovery",
    password="recovery-password",
):
    runtime = root / f"{generation_id}-{manifest_digest[:12]}"
    for directory in (
        "media/pdfs",
        "pdf_cache",
        "faiss_indexes",
        "chroma_db",
    ):
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    database = runtime / "db.sqlite3"
    connection = sqlite3.connect(database)
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
        "id INTEGER PRIMARY KEY, username TEXT, password TEXT, "
        "is_active INTEGER, "
        "is_superuser INTEGER, role TEXT)"
    )
    connection.execute(
        "INSERT INTO core_customuser "
        "VALUES (1, ?, ?, 1, 1, 'superadmin')",
        (username, make_password(password)),
    )
    connection.commit()
    connection.close()
    evidence = {
        "generation_id": generation_id,
        "manifest_digest": manifest_digest,
        "profile_fingerprint": "f" * 64,
        "sanitized": True,
        "static_assets_posture": "custody_only",
    }
    (runtime / "runtime-evidence.json").write_text(
        json.dumps(evidence), encoding="utf-8"
    )
    for directory, directories, files in os.walk(runtime, topdown=False):
        for name in files:
            os.chmod(Path(directory) / name, 0o440)
        for name in directories:
            os.chmod(Path(directory) / name, 0o550)
    os.chmod(runtime, 0o550)
    return runtime


def make_pointer(runtime, generation_id, manifest_digest, intent_digest):
    return build_runtime_pointer(
        deployment_id=DEPLOYMENT_ID,
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        runtime_path=runtime,
        intent_digest=intent_digest,
        state_version=1,
        signing_key=SIGNING_KEY,
    )


class RuntimeControlTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.runtime_root = self.root / "runtime"
        self.current = create_runtime(
            self.runtime_root, CURRENT_GENERATION, CURRENT_DIGEST
        )
        self.paths = runtime_control_paths(self.control)
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.current,
                CURRENT_GENERATION,
                CURRENT_DIGEST,
                "bootstrap-intent",
            ),
        )

    def tearDown(self):
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def test_signed_pointer_resolves_every_runtime_path(self):
        runtime = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(runtime.generation_id, CURRENT_GENERATION)
        self.assertEqual(
            runtime.database_path, self.current.resolve() / "db.sqlite3"
        )
        self.assertEqual(
            runtime.media_root, self.current.resolve() / "media"
        )

    def test_tampered_pointer_and_mutable_runtime_fail_closed(self):
        document = json.loads(
            self.paths["active"].read_text(encoding="utf-8")
        )
        document["generation_id"] = "tampered"
        self.paths["active"].write_text(
            json.dumps(document), encoding="utf-8"
        )
        with self.assertRaisesMessage(
            RuntimeControlError, "control_document_signature_invalid"
        ):
            read_runtime_pointer(
                self.paths["active"],
                deployment_id=DEPLOYMENT_ID,
                signing_key=SIGNING_KEY,
                runtime_root=self.runtime_root,
            )
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.current,
                CURRENT_GENERATION,
                CURRENT_DIGEST,
                "bootstrap-intent",
            ),
        )
        os.chmod(self.current / "db.sqlite3", 0o640)
        runtime = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(runtime.generation_id, CURRENT_GENERATION)
        with self.assertRaisesMessage(
            RuntimeControlError, "runtime_workspace_mutable"
        ):
            validate_runtime_workspace(
                self.current,
                runtime_root=self.runtime_root,
                generation_id=CURRENT_GENERATION,
                manifest_digest=CURRENT_DIGEST,
            )

    def test_production_activation_is_rejected_before_pointer_read(self):
        environment = {
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "1",
            "APP_ENV": "production",
            "DATA_CONTROL_ROOT": str(self.control),
            "RUNTIME_GENERATIONS_ROOT": str(self.runtime_root),
            "DEPLOYMENT_ID": DEPLOYMENT_ID,
            "ACTIVATION_INTENT_SIGNING_KEY": SIGNING_KEY,
        }
        with self.assertRaisesMessage(
            RuntimeControlError, "production_activation_disabled"
        ):
            resolve_runtime_from_env(environment)

    def test_initial_bootstrap_requires_both_runtime_pointers_to_be_absent(self):
        self.paths["active"].unlink()
        environment = {
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "1",
            "STAGING_INITIAL_ACTIVATION_ENABLED": "1",
            "APP_ENV": "staging",
            "DATA_CONTROL_ROOT": str(self.control),
            "RUNTIME_GENERATIONS_ROOT": str(self.runtime_root),
            "DEPLOYMENT_ID": DEPLOYMENT_ID,
            "ACTIVATION_INTENT_SIGNING_KEY": SIGNING_KEY,
        }

        self.assertIsNone(resolve_runtime_from_env(environment))

        atomic_write_json(
            self.paths["previous"],
            make_pointer(
                self.current,
                CURRENT_GENERATION,
                CURRENT_DIGEST,
                "bootstrap-intent",
            ),
        )
        with self.assertRaisesMessage(
            RuntimeControlError, "control_document_missing"
        ):
            resolve_runtime_from_env(environment)

    def test_runtime_path_cli_marks_only_safe_initial_bootstrap(self):
        self.paths["active"].unlink()
        environment = {
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "1",
            "STAGING_INITIAL_ACTIVATION_ENABLED": "1",
            "APP_ENV": "staging",
            "DATA_CONTROL_ROOT": str(self.control),
            "RUNTIME_GENERATIONS_ROOT": str(self.runtime_root),
            "DEPLOYMENT_ID": DEPLOYMENT_ID,
            "ACTIVATION_INTENT_SIGNING_KEY": SIGNING_KEY,
        }
        output = io.StringIO()

        with patch.dict(os.environ, environment, clear=True), patch(
            "sys.stdout", output
        ):
            self.assertEqual(
                runtime_paths_main(
                    ["generation", "--allow-initial-bootstrap"]
                ),
                0,
            )

        self.assertEqual(output.getvalue().strip(), "initial-bootstrap")


@override_settings(
    ENV_IDENTITY=staging_identity(),
    STAGING_RUNTIME_ACTIVATION_ENABLED=True,
    VAULT_ADMIN_MUTATIONS_ENABLED=True,
    ACTIVATION_INTENT_SIGNING_KEY=SIGNING_KEY,
    ACTIVATION_RECOVERY_SUPERADMIN_USERNAME="recovery",
    ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="recovery-password",
    VAULT_VALIDATION_MAX_AGE_SECONDS=1800,
)
class ActivationCoordinatorTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.runtime_root = self.root / "runtime"
        self.smoke_file = self.root / "activation-smoke.json"
        self.smoke_file.write_text(
            json.dumps(
                {
                    "queries": [
                        {
                            "locale": "en",
                            "query": "English test",
                            "allow_empty": True,
                        },
                        {
                            "locale": "mr",
                            "query": "मराठी चाचणी",
                            "allow_empty": True,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.current_runtime = create_runtime(
            self.runtime_root, CURRENT_GENERATION, CURRENT_DIGEST
        )
        self.target_runtime = create_runtime(
            self.runtime_root, TARGET_GENERATION, TARGET_DIGEST
        )
        self.paths = runtime_control_paths(self.control)
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.current_runtime,
                CURRENT_GENERATION,
                CURRENT_DIGEST,
                "bootstrap-intent",
            ),
        )
        self.profile = VaultConnectionProfile.objects.create(
            key="activation-test",
            display_name="Activation test",
            dataset_id="test-dataset",
            fingerprint="f" * 64,
        )
        self.current_generation = ArtifactGeneration.objects.create(
            profile=self.profile,
            dataset_id="test-dataset",
            generation_id=CURRENT_GENERATION,
            manifest_digest=CURRENT_DIGEST,
            runtime_state=ArtifactGeneration.RuntimeState.ACTIVE,
            local_presence=ArtifactGeneration.LocalPresence.PREPARED,
            deployment_id=DEPLOYMENT_ID,
        )
        self.target_generation = ArtifactGeneration.objects.create(
            profile=self.profile,
            dataset_id="test-dataset",
            generation_id=TARGET_GENERATION,
            manifest_digest=TARGET_DIGEST,
            runtime_state=ArtifactGeneration.RuntimeState.UNKNOWN,
            local_presence=ArtifactGeneration.LocalPresence.PREPARED,
        )
        self.workspace = RestoreWorkspace.objects.create(
            generation=self.target_generation,
            state=RestoreWorkspace.State.ACTIVATION_READY,
            manifest_digest=TARGET_DIGEST,
            runtime_path=str(self.target_runtime),
            validation_evidence={"database_integrity": "ok"},
            rehearsal_evidence={
                "success": True,
                "app_release": "release-1",
                "image_digest": "sha256:image-1",
            },
            prepared_at=timezone.now(),
        )
        self.settings_override = override_settings(
            DATA_CONTROL_ROOT=self.control,
            RUNTIME_GENERATIONS_ROOT=self.runtime_root,
            ACTIVATION_SMOKE_QUERIES_FILE=self.smoke_file,
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def test_schedule_writes_signed_intent_and_marks_target_pending(self):
        intent = schedule_activation(
            self.workspace,
            actor_id=7,
            actor_name="operator",
            confirmed=True,
        )
        intent_document = read_signed_document(
            self.paths["intents"] / f"{intent.public_id}.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_intent",
            deployment_id=DEPLOYMENT_ID,
        )
        self.target_generation.refresh_from_db()
        self.assertEqual(intent.state, ActivationIntent.State.PENDING)
        self.assertEqual(
            self.target_generation.runtime_state,
            ArtifactGeneration.RuntimeState.PENDING,
        )
        self.assertEqual(
            intent_document["previous_generation_id"],
            CURRENT_GENERATION,
        )
        self.assertEqual(
            intent_document["target_generation_id"], TARGET_GENERATION
        )

    def test_initial_schedule_requires_opt_in_and_no_active_authority(self):
        self.paths["active"].unlink()
        self.current_generation.runtime_state = (
            ArtifactGeneration.RuntimeState.UNKNOWN
        )
        self.current_generation.save(update_fields=["runtime_state"])

        with override_settings(STAGING_INITIAL_ACTIVATION_ENABLED=True):
            intent = schedule_activation(self.workspace, confirmed=True)

        document = read_signed_document(
            self.paths["intents"] / f"{intent.public_id}.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_intent",
            deployment_id=DEPLOYMENT_ID,
        )
        self.assertEqual(document["activation_mode"], "initial")
        self.assertEqual(document["previous_generation_id"], "")
        self.assertEqual(document["previous_pointer_digest"], "")
        self.assertTrue(intent.checkpoint["initial_activation"])

    def test_initial_schedule_rejects_unprojected_active_generation(self):
        self.paths["active"].unlink()

        with override_settings(STAGING_INITIAL_ACTIVATION_ENABLED=True):
            with self.assertRaisesMessage(
                ActivationCoordinatorError,
                "activation_previous_runtime_invalid",
            ):
                schedule_activation(self.workspace, confirmed=True)

    def _observe_current_source_pointer(self):
        pointer = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        RuntimePointerObservation.objects.create(
            deployment_id=DEPLOYMENT_ID,
            active_generation_id=CURRENT_GENERATION,
            pointer_digest=pointer.pointer_digest,
            status="ready",
            observed_at=timezone.now(),
        )
        return pointer

    def test_mutable_writer_source_identity_uses_verified_signed_pointer(self):
        pointer = self._observe_current_source_pointer()
        database = self.current_runtime / "db.sqlite3"
        before = hashlib.sha256(database.read_bytes()).hexdigest()

        with override_settings(
            ACTIVE_RUNTIME=None,
            MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=True,
        ):
            identity = _verified_mutable_source_runtime_identity()

        self.assertEqual(identity, (CURRENT_GENERATION, CURRENT_DIGEST))
        self.assertEqual(
            hashlib.sha256(database.read_bytes()).hexdigest(), before
        )
        self.assertEqual(pointer.generation_id, identity[0])

    def test_mutable_writer_source_rejects_tampered_pointer(self):
        self._observe_current_source_pointer()
        document = json.loads(
            self.paths["active"].read_text(encoding="utf-8")
        )
        document["generation_id"] = "tampered-generation"
        self.paths["active"].write_text(
            json.dumps(document), encoding="utf-8"
        )

        with override_settings(
            ACTIVE_RUNTIME=None,
            MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=True,
        ):
            with self.assertRaisesMessage(
                CandidateMaintenanceError,
                "maintenance_source_pointer_unverified",
            ):
                _verified_mutable_source_runtime_identity()

    def test_mutable_writer_source_rejects_stale_observation(self):
        self._observe_current_source_pointer()
        RuntimePointerObservation.objects.update(pointer_digest="0" * 64)

        with override_settings(
            ACTIVE_RUNTIME=None,
            MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=True,
        ):
            with self.assertRaisesMessage(
                CandidateMaintenanceError,
                "maintenance_source_observation_stale",
            ):
                _verified_mutable_source_runtime_identity()

    def test_exact_activation_request_reuses_the_same_intent(self):
        first = schedule_activation(
            self.workspace,
            confirmed=True,
            idempotency_key="activation-request-1",
            request_state_digest="d" * 64,
        )
        second = schedule_activation(
            self.workspace,
            confirmed=True,
            idempotency_key="activation-request-1",
            request_state_digest="d" * 64,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ActivationIntent.objects.count(), 1)

    def test_activation_request_key_rejects_changed_state(self):
        schedule_activation(
            self.workspace,
            confirmed=True,
            idempotency_key="activation-request-1",
            request_state_digest="d" * 64,
        )

        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "idempotency_conflict",
        ):
            schedule_activation(
                self.workspace,
                confirmed=True,
                idempotency_key="activation-request-1",
                request_state_digest="e" * 64,
            )

    def test_production_is_hard_blocked_before_filesystem_mutation(self):
        with override_settings(
            ENV_IDENTITY=staging_identity(
                is_production=True,
                app_env=SimpleNamespace(value="production"),
            )
        ):
            with self.assertRaisesMessage(
                ActivationCoordinatorError,
                "production_activation_disabled",
            ):
                schedule_activation(self.workspace, confirmed=True)
        self.assertFalse(self.paths["intents"].exists())

    def test_schedule_requires_working_recovery_login(self):
        with override_settings(
            ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="wrong-password"
        ):
            with self.assertRaisesMessage(
                ActivationCoordinatorError,
                "activation_recovery_superadmin_unproven",
            ):
                schedule_activation(self.workspace, confirmed=True)
        self.assertFalse(self.paths["intents"].exists())

    def _configure_signed_local_candidate_as_active(self):
        lineage_job_id = uuid.uuid4()
        self.current_generation.runtime_state = (
            ArtifactGeneration.RuntimeState.PREVIOUS
        )
        self.current_generation.save(update_fields=["runtime_state"])
        self.target_generation.origin = (
            ArtifactGeneration.Origin.LOCAL_MAINTENANCE
        )
        self.target_generation.runtime_state = (
            ArtifactGeneration.RuntimeState.ACTIVE
        )
        self.target_generation.deployment_id = DEPLOYMENT_ID
        self.target_generation.lineage_job_public_id = lineage_job_id
        self.target_generation.parent_generation_id = CURRENT_GENERATION
        self.target_generation.parent_manifest_digest = CURRENT_DIGEST
        self.target_generation.save(
            update_fields=[
                "origin",
                "runtime_state",
                "deployment_id",
                "lineage_job_public_id",
                "parent_generation_id",
                "parent_manifest_digest",
            ]
        )
        active_document = make_pointer(
            self.target_runtime,
            TARGET_GENERATION,
            TARGET_DIGEST,
            "local-maintenance-activation",
        )
        previous_document = make_pointer(
            self.current_runtime,
            CURRENT_GENERATION,
            CURRENT_DIGEST,
            "bootstrap-intent",
        )
        atomic_write_json(self.paths["active"], active_document)
        atomic_write_json(self.paths["previous"], previous_document)
        RuntimePointerObservation.objects.create(
            deployment_id=DEPLOYMENT_ID,
            active_generation_id=TARGET_GENERATION,
            previous_generation_id=CURRENT_GENERATION,
            pointer_digest=active_document["document_digest"],
            status="ready",
            observed_at=timezone.now(),
        )
        return active_document

    def test_prepares_exact_previous_signed_runtime_for_rollback(self):
        active_document = self._configure_signed_local_candidate_as_active()

        workspace = prepare_previous_runtime_rollback()

        self.assertEqual(workspace.generation, self.current_generation)
        self.assertEqual(
            Path(workspace.runtime_path), self.current_runtime.resolve()
        )
        self.assertEqual(
            workspace.state, RestoreWorkspace.State.ACTIVATION_READY
        )
        self.assertEqual(
            workspace.validation_evidence["active_generation_id"],
            TARGET_GENERATION,
        )
        self.assertEqual(
            workspace.validation_evidence["active_pointer_digest"],
            active_document["document_digest"],
        )
        self.assertFalse(
            workspace.validation_evidence["vault_authority_changed"]
        )

    def test_prepared_rollback_uses_existing_signed_activation_protocol(self):
        storage_evidence = storage_key_evidence("pdfs/missing.pdf")
        unavailable_documents = build_unavailable_attestation(
            (
                {
                    "id": 1,
                    "lifecycle": "unavailable",
                    "storage_key_status": storage_evidence["status"],
                    "storage_key_token_sha256": storage_evidence[
                        "token_sha256"
                    ],
                    "expected_sha256": "",
                    "expected_size": None,
                    "prior_lifecycle": "uploaded",
                },
            )
        )
        self.current_generation.manifest = {
            **self.current_generation.manifest,
            "unavailable_documents": unavailable_documents,
        }
        self.current_generation.save(update_fields=["manifest"])
        self._configure_signed_local_candidate_as_active()
        workspace = prepare_previous_runtime_rollback()

        intent = schedule_activation(
            workspace, confirmed=True, rollback=True
        )
        intent_document = read_signed_document(
            self.paths["intents"] / f"{intent.public_id}.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_intent",
            deployment_id=DEPLOYMENT_ID,
        )

        self.assertEqual(
            intent_document["target_generation_id"], CURRENT_GENERATION
        )
        self.assertEqual(
            intent_document["previous_generation_id"], TARGET_GENERATION
        )
        self.assertEqual(intent_document["activation_mode"], "rollback")
        self.assertEqual(
            intent_document["unavailable_documents"],
            unavailable_documents,
        )
        self.assertEqual(
            intent_document["rollback_previous_generation_id"],
            CURRENT_GENERATION,
        )
        self.assertEqual(
            intent_document["rollback_previous_manifest_digest"],
            CURRENT_DIGEST,
        )
        self.assertEqual(
            intent.checkpoint["rollback_previous_pointer_digest"],
            intent_document["rollback_previous_pointer_digest"],
        )

    def test_scheduling_reauthorizes_previous_pointer_after_capacity_work(self):
        self._configure_signed_local_candidate_as_active()
        workspace = prepare_previous_runtime_rollback()

        def change_previous_authority(**kwargs):
            atomic_write_json(
                self.paths["previous"],
                make_pointer(
                    self.target_runtime,
                    TARGET_GENERATION,
                    TARGET_DIGEST,
                    "concurrent-authority-change",
                ),
            )
            return {
                "byte_capacity_ok": True,
                "inode_capacity_ok": True,
            }

        with patch(
            "core.artifact_cleanup.capacity_report",
            side_effect=change_previous_authority,
        ):
            with self.assertRaisesMessage(
                ActivationCoordinatorError,
                "rollback_authority_changed",
            ):
                schedule_activation(
                    workspace, confirmed=True, rollback=True
                )

        self.assertFalse(self.paths["intents"].exists())

    def test_rollback_rejects_stale_runtime_observation(self):
        self._configure_signed_local_candidate_as_active()
        RuntimePointerObservation.objects.update(pointer_digest="0" * 64)

        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "rollback_pointer_observation_stale",
        ):
            prepare_previous_runtime_rollback()

    def test_scheduling_rejects_pointer_change_after_rollback_preparation(self):
        self._configure_signed_local_candidate_as_active()
        workspace = prepare_previous_runtime_rollback()
        changed_active = make_pointer(
            self.target_runtime,
            TARGET_GENERATION,
            TARGET_DIGEST,
            "changed-active-pointer",
        )
        atomic_write_json(self.paths["active"], changed_active)

        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "rollback_authority_changed",
        ):
            schedule_activation(
                workspace, confirmed=True, rollback=True
            )
        self.assertFalse(self.paths["intents"].exists())

    def test_consecutive_local_candidates_can_rollback_exactly_one_step(self):
        self._configure_signed_local_candidate_as_active()
        self.current_generation.origin = (
            ArtifactGeneration.Origin.LOCAL_MAINTENANCE
        )
        self.current_generation.vault_state = (
            ArtifactGeneration.VaultState.UNKNOWN
        )
        self.current_generation.lineage_job_public_id = uuid.uuid4()
        self.current_generation.parent_generation_id = "older-generation"
        self.current_generation.parent_manifest_digest = "c" * 64
        self.current_generation.save(
            update_fields=[
                "origin",
                "vault_state",
                "lineage_job_public_id",
                "parent_generation_id",
                "parent_manifest_digest",
            ]
        )
        workspace = prepare_previous_runtime_rollback()

        intent = schedule_activation(
            workspace, confirmed=True, rollback=True
        )

        self.assertEqual(
            intent.target_generation_id, CURRENT_GENERATION
        )
        self.assertEqual(
            intent.previous_generation_id, TARGET_GENERATION
        )

    def test_rollback_rejects_non_maintenance_active_runtime(self):
        self._configure_signed_local_candidate_as_active()
        self.target_generation.origin = (
            ArtifactGeneration.Origin.VAULT_GENERATION
        )
        self.target_generation.lineage_job_public_id = None
        self.target_generation.parent_generation_id = ""
        self.target_generation.parent_manifest_digest = ""
        self.target_generation.save(
            update_fields=[
                "origin",
                "lineage_job_public_id",
                "parent_generation_id",
                "parent_manifest_digest",
            ]
        )

        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "rollback_active_generation_ineligible",
        ):
            prepare_previous_runtime_rollback()

    def test_rollback_capability_projects_verified_exact_parent(self):
        self._configure_signed_local_candidate_as_active()

        capability = _rollback_capability()

        self.assertTrue(capability["enabled"])
        self.assertEqual(capability["reason_code"], "")
        self.assertEqual(
            capability["target_generation_id"], CURRENT_GENERATION
        )

    def test_rollback_capability_reports_bounded_pointer_reasons(self):
        capability = _rollback_capability()
        self.assertFalse(capability["enabled"])
        self.assertEqual(
            capability["reason_code"], "rollback_pointer_missing"
        )

        atomic_write_json(self.paths["previous"], {"not": "signed"})
        capability = _rollback_capability()
        self.assertEqual(
            capability["reason_code"], "rollback_pointer_unverified"
        )

    def test_rollback_capability_reports_unprojected_generation(self):
        self._configure_signed_local_candidate_as_active()
        absent_digest = "9" * 64
        absent_runtime = create_runtime(
            self.runtime_root, "unprojected-generation", absent_digest
        )
        atomic_write_json(
            self.paths["previous"],
            make_pointer(
                absent_runtime,
                "unprojected-generation",
                absent_digest,
                "unprojected-previous",
            ),
        )

        self.assertEqual(
            _rollback_capability()["reason_code"],
            "rollback_generation_unprojected",
        )

    def test_rollback_capability_reports_ineligible_lineage_and_staleness(self):
        self._configure_signed_local_candidate_as_active()
        self.target_generation.origin = (
            ArtifactGeneration.Origin.VAULT_GENERATION
        )
        self.target_generation.lineage_job_public_id = None
        self.target_generation.parent_generation_id = ""
        self.target_generation.parent_manifest_digest = ""
        self.target_generation.save(
            update_fields=[
                "origin",
                "lineage_job_public_id",
                "parent_generation_id",
                "parent_manifest_digest",
            ]
        )
        self.assertEqual(
            _rollback_capability()["reason_code"],
            "rollback_active_generation_ineligible",
        )

        self.target_generation.origin = (
            ArtifactGeneration.Origin.LOCAL_MAINTENANCE
        )
        self.target_generation.lineage_job_public_id = uuid.uuid4()
        self.target_generation.parent_generation_id = CURRENT_GENERATION
        self.target_generation.parent_manifest_digest = "0" * 64
        self.target_generation.save(
            update_fields=[
                "origin",
                "lineage_job_public_id",
                "parent_generation_id",
                "parent_manifest_digest",
            ]
        )
        self.assertEqual(
            _rollback_capability()["reason_code"],
            "rollback_lineage_invalid",
        )

        self.target_generation.parent_manifest_digest = CURRENT_DIGEST
        self.target_generation.save(
            update_fields=["parent_manifest_digest"]
        )
        RuntimePointerObservation.objects.update(
            observed_at=timezone.now()
            - timedelta(
                seconds=settings.VAULT_VALIDATION_MAX_AGE_SECONDS + 1
            )
        )
        self.assertEqual(
            _rollback_capability()["reason_code"],
            "rollback_pointer_observation_stale",
        )

    def test_rollback_capability_reports_recovery_auth_and_active_intent(self):
        self._configure_signed_local_candidate_as_active()
        with patch(
            "vaultops.services.read_model."
            "verify_recovery_superadmin_database",
            side_effect=RecoveryAuthenticationError,
        ):
            self.assertEqual(
                _rollback_capability()["reason_code"],
                "activation_recovery_superadmin_unproven",
            )
        self.assertEqual(
            _rollback_capability(
                pending_activation=SimpleNamespace()
            )["reason_code"],
            "runtime_activation_in_progress",
        )

    def test_signed_commit_result_updates_independent_projections(self):
        intent = schedule_activation(self.workspace, confirmed=True)
        target_pointer_document = make_pointer(
            self.target_runtime,
            TARGET_GENERATION,
            TARGET_DIGEST,
            intent.intent_digest,
        )
        atomic_write_json(self.paths["active"], target_pointer_document)
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        result = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_result",
                "deployment_id": DEPLOYMENT_ID,
                "intent_id": str(intent.public_id),
                "intent_digest": intent.intent_digest,
                "status": "committed",
                "active_generation_id": TARGET_GENERATION,
                "active_manifest_digest": TARGET_DIGEST,
                "previous_generation_id": CURRENT_GENERATION,
                "active_pointer_digest": active.pointer_digest,
                "readiness_evidence": {
                    "readyz": "ready",
                    "runtime_generation_id": TARGET_GENERATION,
                    "runtime_manifest_digest": TARGET_DIGEST,
                },
                "process_identity": {"child_pid": 12},
                "safe_error_code": "",
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["results"] / f"{intent.public_id}.json",
            result,
        )
        reconciled = reconcile_activation_result(intent)
        self.current_generation.refresh_from_db()
        self.target_generation.refresh_from_db()
        observation = RuntimePointerObservation.objects.get()
        self.assertEqual(
            reconciled.state, ActivationIntent.State.COMMITTED
        )
        self.assertEqual(
            self.current_generation.runtime_state,
            ArtifactGeneration.RuntimeState.PREVIOUS,
        )
        self.assertEqual(
            self.target_generation.runtime_state,
            ArtifactGeneration.RuntimeState.ACTIVE,
        )
        self.assertEqual(
            observation.active_generation_id, TARGET_GENERATION
        )

    def test_signed_commit_result_rejects_wrong_manifest_identity(self):
        intent = schedule_activation(self.workspace, confirmed=True)
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.target_runtime,
                TARGET_GENERATION,
                TARGET_DIGEST,
                intent.intent_digest,
            ),
        )
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        result = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_result",
                "deployment_id": DEPLOYMENT_ID,
                "intent_id": str(intent.public_id),
                "intent_digest": intent.intent_digest,
                "status": "committed",
                "active_generation_id": TARGET_GENERATION,
                "active_manifest_digest": "f" * 64,
                "previous_generation_id": CURRENT_GENERATION,
                "active_pointer_digest": active.pointer_digest,
                "readiness_evidence": {
                    "runtime_generation_id": TARGET_GENERATION,
                    "runtime_manifest_digest": TARGET_DIGEST,
                },
                "process_identity": {"child_pid": 12},
                "safe_error_code": "",
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["results"] / f"{intent.public_id}.json",
            result,
        )
        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "activation_result_runtime_mismatch",
        ):
            reconcile_activation_result(intent)


class FakeChild:
    pid = 4242
    returncode = None

    def poll(self):
        return None


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class SupervisorProtocolTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.runtime_root = self.root / "runtime"
        self.current_runtime = create_runtime(
            self.runtime_root, CURRENT_GENERATION, CURRENT_DIGEST
        )
        self.target_runtime = create_runtime(
            self.runtime_root, TARGET_GENERATION, TARGET_DIGEST
        )
        self.paths = runtime_control_paths(self.control)
        self.current_pointer_document = make_pointer(
            self.current_runtime,
            CURRENT_GENERATION,
            CURRENT_DIGEST,
            "bootstrap-intent",
        )
        atomic_write_json(
            self.paths["active"], self.current_pointer_document
        )
        self.environment = {
            "APP_ENV": "staging",
            "DEPLOYMENT_ID": DEPLOYMENT_ID,
            "DATA_CONTROL_ROOT": str(self.control),
            "RUNTIME_GENERATIONS_ROOT": str(self.runtime_root),
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "1",
            "STAGING_ACTIVATION_APPLY_MODE": "auto",
            "ACTIVATION_INTENT_SIGNING_KEY": SIGNING_KEY,
            "ACTIVATION_READINESS_TIMEOUT_SECONDS": "3",
            "ACTIVATION_SUPERVISOR_POLL_SECONDS": "1",
        }
        self.intent = self._write_intent()

    def tearDown(self):
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def _write_intent(self):
        intent_id = str(uuid.uuid4())
        document = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": intent_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": TARGET_GENERATION,
                "target_manifest_digest": TARGET_DIGEST,
                "target_runtime_path": str(self.target_runtime),
                "previous_generation_id": CURRENT_GENERATION,
                "previous_pointer_digest": self.current_pointer_document[
                    "document_digest"
                ],
                "smoke_queries_digest": "c" * 64,
                "expires_at_unix": int(time.time()) + 300,
                "state_version": 1,
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["intents"] / f"{intent_id}.json", document
        )
        return document

    def _replace_with_rollback_intent(self):
        self.paths["intents"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).unlink()
        active_document = make_pointer(
            self.target_runtime,
            TARGET_GENERATION,
            TARGET_DIGEST,
            "active-local-candidate",
        )
        atomic_write_json(self.paths["active"], active_document)
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )
        intent_id = str(uuid.uuid4())
        document = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": intent_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": CURRENT_GENERATION,
                "target_manifest_digest": CURRENT_DIGEST,
                "target_runtime_path": str(self.current_runtime),
                "previous_generation_id": TARGET_GENERATION,
                "previous_pointer_digest": active_document[
                    "document_digest"
                ],
                "activation_mode": "rollback",
                "rollback_previous_generation_id": CURRENT_GENERATION,
                "rollback_previous_manifest_digest": CURRENT_DIGEST,
                "rollback_previous_pointer_digest": (
                    self.current_pointer_document["document_digest"]
                ),
                "smoke_queries_digest": "c" * 64,
                "expires_at_unix": int(time.time()) + 300,
                "state_version": 1,
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["intents"] / f"{intent_id}.json", document
        )
        self.intent = document
        return active_document

    def _replace_with_initial_intent(self):
        self.paths["intents"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).unlink()
        self.paths["active"].unlink()
        self.environment["STAGING_INITIAL_ACTIVATION_ENABLED"] = "1"
        intent_id = str(uuid.uuid4())
        document = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": intent_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": TARGET_GENERATION,
                "target_manifest_digest": TARGET_DIGEST,
                "target_runtime_path": str(self.target_runtime),
                "previous_generation_id": "",
                "previous_pointer_digest": "",
                "activation_mode": "initial",
                "smoke_queries_digest": "c" * 64,
                "expires_at_unix": int(time.time()) + 300,
                "state_version": 1,
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["intents"] / f"{intent_id}.json", document
        )
        self.intent = document

    def _urlopen(self, request, timeout=5):
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        if request.full_url.endswith("/livez"):
            return FakeResponse({"status": "ok"})
        return FakeResponse(
            {
                "status": "ready",
                "runtime_generation_id": active.generation_id,
                "runtime_manifest_digest": active.manifest_digest,
            }
        )

    def _supervisor(self, role, run_command=None, maintenance=None):
        def successful_command(*_args, **kwargs):
            success_path = kwargs.get("env", {}).get(
                "ACTIVATION_VERIFICATION_SUCCESS_PATH"
            )
            if success_path:
                write_runtime_verification_success(
                    success_path,
                    build_unavailable_attestation(()),
                )
            return SimpleNamespace(returncode=0)

        supervisor = RuntimeSupervisor(
            role,
            environment=self.environment,
            run_command=run_command
            or successful_command,
            urlopen=self._urlopen,
            sleep=(
                (lambda seconds: maintenance.maintenance_tick())
                if maintenance is not None
                else (lambda seconds: None)
            ),
        )
        supervisor.child = FakeChild()
        supervisor.stop_child = lambda timeout=30: None
        supervisor.start_child = lambda: supervisor.child
        return supervisor

    def _quiesce(self):
        maintenance = self._supervisor("maintenance")
        maintenance.maintenance_tick()
        ack = read_signed_document(
            self.paths["acks"]
            / f"{self.intent['intent_id']}.maintenance.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_ack",
            deployment_id=DEPLOYMENT_ID,
        )
        self.assertEqual(ack["state"], "quiesced")
        return maintenance

    def _result(self):
        return read_signed_document(
            self.paths["results"] / f"{self.intent['intent_id']}.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_result",
            deployment_id=DEPLOYMENT_ID,
        )

    def test_web_cutover_requires_maintenance_ack_and_commits(self):
        web = self._supervisor("web")
        web.web_tick()
        self.assertFalse(
            self.paths["results"].joinpath(
                f"{self.intent['intent_id']}.json"
            ).exists()
        )
        maintenance = self._quiesce()
        web.sleep = lambda seconds: maintenance.maintenance_tick()
        web.web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertEqual(self._result()["status"], "committed")
        self.assertNotEqual(
            self.target_runtime.stat().st_mode & 0o200, 0
        )
        self.assertEqual(
            self.current_runtime.stat().st_mode & 0o222, 0
        )
        checkpoint = read_signed_document(
            self.paths["acks"]
            / f"{self.intent['intent_id']}.web.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_ack",
            deployment_id=DEPLOYMENT_ID,
        )
        self.assertEqual(checkpoint["state"], "reconciled")

    def test_initial_cutover_commits_without_creating_previous_pointer(self):
        self._replace_with_initial_intent()
        maintenance = self._quiesce()
        web = self._supervisor("web", maintenance=maintenance)

        web.web_tick()

        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertFalse(self.paths["previous"].exists())
        self.assertEqual(self._result()["status"], "committed")
        self.assertEqual(self._result()["previous_generation_id"], "")

    def test_initial_intent_is_ignored_without_explicit_supervisor_opt_in(self):
        self._replace_with_initial_intent()
        self.environment["STAGING_INITIAL_ACTIVATION_ENABLED"] = "0"
        self._quiesce()
        web = self._supervisor("web")

        web.web_tick()

        self.assertFalse(self.paths["active"].exists())
        self.assertFalse(
            self.paths["results"]
            .joinpath(f"{self.intent['intent_id']}.json")
            .exists()
        )

    def test_initial_pre_pointer_validation_failure_releases_maintenance(self):
        self._replace_with_initial_intent()
        maintenance = self._quiesce()
        self.target_runtime.chmod(0o750)
        web = self._supervisor("web", maintenance=maintenance)

        web.web_tick()
        maintenance.maintenance_tick()

        self.assertFalse(self.paths["active"].exists())
        self.assertFalse(self.paths["previous"].exists())
        self.assertEqual(self._result()["status"], "failed")
        self.assertEqual(
            self._result()["safe_error_code"],
            "runtime_workspace_mutable",
        )
        self.assertEqual(maintenance.paused_for_intent, "")

    def test_initial_post_pointer_failure_quiesces_target_and_bootstraps(self):
        self._replace_with_initial_intent()
        maintenance = self._quiesce()

        def run_command(command, **_kwargs):
            if "verify_activation_runtime" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        web = self._supervisor(
            "web",
            run_command=run_command,
            maintenance=maintenance,
        )

        web.web_tick()
        maintenance.maintenance_tick()

        self.assertFalse(self.paths["active"].exists())
        self.assertFalse(self.paths["previous"].exists())
        self.assertEqual(self._result()["status"], "failed")
        self.assertEqual(
            self._result()["safe_error_code"],
            "activation_runtime_command_failed",
        )
        self.assertEqual(
            self.target_runtime.stat().st_mode & 0o222,
            0,
        )
        self.assertEqual(maintenance.paused_for_intent, "")

    def test_process_environment_follows_signed_pointer(self):
        self._replace_with_initial_intent()
        supervisor = self._supervisor("web")

        bootstrap = supervisor._resolved_process_environment()
        self.assertNotIn("RUNTIME_GENERATION_ID", bootstrap)
        self.assertNotIn("RUNTIME_MANIFEST_DIGEST", bootstrap)

        atomic_write_json(
            self.paths["active"],
            build_runtime_pointer(
                deployment_id=DEPLOYMENT_ID,
                generation_id=TARGET_GENERATION,
                manifest_digest=TARGET_DIGEST,
                runtime_path=self.target_runtime,
                intent_digest=self.intent["document_digest"],
                state_version=1,
                signing_key=SIGNING_KEY,
            ),
        )
        target = supervisor._resolved_process_environment()

        self.assertEqual(
            target["SQLITE_DB_PATH"],
            str(self.target_runtime / "db.sqlite3"),
        )
        self.assertEqual(
            target["MEDIA_ROOT"],
            str(self.target_runtime / "media"),
        )
        self.assertEqual(
            target["FAISS_INDEX_DIR"],
            str(self.target_runtime / "faiss_indexes"),
        )
        self.assertEqual(
            target["RUNTIME_GENERATION_ID"],
            TARGET_GENERATION,
        )
        self.assertEqual(
            target["RUNTIME_MANIFEST_DIGEST"],
            TARGET_DIGEST,
        )

    def test_child_restart_receives_current_pointer_environment(self):
        captured = {}

        def popen_factory(_command, **kwargs):
            captured.update(kwargs["env"])
            return FakeChild()

        supervisor = RuntimeSupervisor(
            "web",
            environment=self.environment,
            popen_factory=popen_factory,
        )
        supervisor.start_child()

        self.assertEqual(
            captured["SQLITE_DB_PATH"],
            str(self.current_runtime / "db.sqlite3"),
        )
        self.assertEqual(
            captured["RUNTIME_GENERATION_ID"],
            CURRENT_GENERATION,
        )
        self.assertEqual(captured["FLOWDOCS_SUPERVISOR_CHILD"], "1")

    def test_manage_command_receives_current_pointer_environment(self):
        captured = {}

        def run_command(_command, **kwargs):
            captured.update(kwargs["env"])
            return SimpleNamespace(returncode=0)

        supervisor = self._supervisor("web", run_command=run_command)
        supervisor._run_manage(["check"], timeout=3)

        self.assertEqual(
            captured["SQLITE_DB_PATH"],
            str(self.current_runtime / "db.sqlite3"),
        )
        self.assertEqual(
            captured["RUNTIME_GENERATION_ID"],
            CURRENT_GENERATION,
        )

    def test_runtime_verification_propagates_allowlisted_reason_only(self):
        captured = {}

        def run_command(_command, **kwargs):
            captured.update(kwargs)
            write_runtime_verification_failure(
                kwargs["env"]["ACTIVATION_VERIFICATION_FAILURE_PATH"],
                "activation_pdf_missing",
            )
            return SimpleNamespace(returncode=1)

        supervisor = self._supervisor("web", run_command=run_command)

        with self.assertRaisesRegex(
            SupervisorError,
            "activation_pdf_missing",
        ):
            supervisor._run_manage(
                ["verify_activation_runtime", "--intent-id", "bounded"],
                timeout=3,
            )

        self.assertIs(captured["stdout"], subprocess.DEVNULL)
        self.assertIs(captured["stderr"], subprocess.DEVNULL)
        self.assertFalse(
            Path(
                captured["env"][
                    "ACTIVATION_VERIFICATION_FAILURE_PATH"
                ]
            ).exists()
        )

    def test_runtime_verification_rejects_unapproved_failure_evidence(self):
        def run_command(_command, **kwargs):
            failure_path = Path(
                kwargs["env"]["ACTIVATION_VERIFICATION_FAILURE_PATH"]
            )
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reason_code": "secret_exception_detail",
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1)

        supervisor = self._supervisor("web", run_command=run_command)

        with self.assertRaisesRegex(
            SupervisorError,
            "activation_runtime_command_failed",
        ):
            supervisor._run_manage(
                ["verify_activation_runtime", "--intent-id", "bounded"],
                timeout=3,
            )

    def test_runtime_verification_rejects_non_string_reason(self):
        def run_command(_command, **kwargs):
            failure_path = Path(
                kwargs["env"]["ACTIVATION_VERIFICATION_FAILURE_PATH"]
            )
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "reason_code": ["activation_pdf_missing"],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1)

        supervisor = self._supervisor("web", run_command=run_command)

        with self.assertRaisesRegex(
            SupervisorError,
            "activation_runtime_command_failed",
        ):
            supervisor._run_manage(
                ["verify_activation_runtime", "--intent-id", "bounded"],
                timeout=3,
            )

    def test_initial_cutover_resumes_from_owned_target_pointer(self):
        self._replace_with_initial_intent()
        maintenance = self._quiesce()
        atomic_write_json(
            self.paths["active"],
            build_runtime_pointer(
                deployment_id=DEPLOYMENT_ID,
                generation_id=TARGET_GENERATION,
                manifest_digest=TARGET_DIGEST,
                runtime_path=self.target_runtime,
                intent_digest=self.intent["document_digest"],
                state_version=1,
                signing_key=SIGNING_KEY,
            ),
        )
        web = self._supervisor("web", maintenance=maintenance)
        web._write_ack(self.intent, "applying")
        atomic_write_json(
            self.paths["lock"],
            {
                "intent_id": self.intent["intent_id"],
                "process_id": 1,
            },
        )

        web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        self.assertFalse(self.paths["previous"].exists())

    def test_supervisor_run_starts_initial_bootstrap_without_a_pointer(self):
        self._replace_with_initial_intent()
        supervisor = self._supervisor("web")
        supervisor.web_tick = lambda: None
        supervisor.sleep = lambda _seconds: setattr(
            supervisor, "shutdown_requested", True
        )

        self.assertEqual(supervisor.run(), 0)
        self.assertFalse(self.paths["active"].exists())
        self.assertFalse(self.paths["previous"].exists())

    def test_supervisor_run_rejects_partial_initial_authority(self):
        self._replace_with_initial_intent()
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )
        supervisor = self._supervisor("web")

        with self.assertRaisesMessage(
            RuntimeControlError, "control_document_missing"
        ):
            supervisor.run()

    def test_readiness_rejects_matching_generation_with_wrong_manifest(self):
        def urlopen(request, timeout=5):
            if request.full_url.endswith("/livez"):
                return FakeResponse({"status": "ok"})
            return FakeResponse(
                {
                    "status": "ready",
                    "runtime_generation_id": TARGET_GENERATION,
                    "runtime_manifest_digest": "f" * 64,
                }
            )

        web = self._supervisor("web")
        web.urlopen = urlopen
        web.readiness_timeout = 1
        web.monotonic = iter((0, 0, 2)).__next__

        with self.assertRaisesRegex(
            SupervisorError, "activation_readiness_mismatch"
        ):
            web._wait_ready(TARGET_GENERATION, TARGET_DIGEST)

    def test_transient_projection_failure_is_retried_idempotently(self):
        maintenance = self._quiesce()
        reconciliation_calls = {"count": 0}

        def run_command(command, **kwargs):
            if "reconcile_activation_result" in command:
                reconciliation_calls["count"] += 1
                return SimpleNamespace(
                    returncode=1
                    if reconciliation_calls["count"] == 1
                    else 0
                )
            success_path = kwargs["env"].get(
                "ACTIVATION_VERIFICATION_SUCCESS_PATH"
            )
            if success_path:
                write_runtime_verification_success(
                    success_path,
                    build_unavailable_attestation(()),
                )
            return SimpleNamespace(returncode=0)

        web = self._supervisor(
            "web",
            run_command=run_command,
            maintenance=maintenance,
        )
        web.web_tick()
        checkpoint = web._read_ack(self.intent, "web")
        self.assertEqual(checkpoint["state"], "committed")

        web.web_tick()

        self.assertEqual(reconciliation_calls["count"], 2)
        self.assertEqual(web._read_ack(self.intent, "web")["state"], "reconciled")
        self.assertEqual(self._result()["status"], "committed")

    def test_stale_maintenance_ack_cannot_release_barrier(self):
        stale = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_ack",
                "deployment_id": DEPLOYMENT_ID,
                "intent_id": self.intent["intent_id"],
                "intent_digest": "0" * 64,
                "role": "maintenance",
                "state": "quiesced",
                "process_id": 77,
                "observed_at_unix": int(time.time()),
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["acks"]
            / f"{self.intent['intent_id']}.maintenance.json",
            stale,
        )
        self._supervisor("web").web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, CURRENT_GENERATION)
        self.assertFalse(self.paths["lock"].exists())

    def test_restart_resumes_same_intent_before_pointer_switch(self):
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._write_ack(self.intent, "applying")
        first_web._acquire_lock(self.intent)

        restarted_web = self._supervisor("web", maintenance=maintenance)
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        self.assertFalse(self.paths["lock"].exists())

    def test_restart_acquires_after_signed_checkpoint_before_lock(self):
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._write_ack(self.intent, "applying")

        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        self.assertFalse(self.paths["lock"].exists())

    def test_restart_recovers_legacy_lock_before_signed_checkpoint(self):
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._acquire_lock(self.intent)

        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        self.assertFalse(self.paths["lock"].exists())

    def test_forward_restart_after_previous_pointer_write_is_idempotent(self):
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._write_ack(self.intent, "applying")
        first_web._acquire_lock(self.intent)
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )

        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()
        active_after = self.paths["active"].read_bytes()
        result_after = self.paths["results"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).read_bytes()
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        self.assertEqual(self.paths["active"].read_bytes(), active_after)
        self.assertEqual(
            self.paths["results"].joinpath(
                f"{self.intent['intent_id']}.json"
            ).read_bytes(),
            result_after,
        )

    def test_rollback_rechecks_exact_authority_under_acquired_lock(self):
        active_document = self._replace_with_rollback_intent()
        maintenance = self._quiesce()
        web = self._supervisor("web", maintenance=maintenance)
        acquire = web._acquire_or_resume_pre_cutover_lock

        def acquire_then_change_previous(intent):
            resumed = acquire(intent)
            atomic_write_json(
                self.paths["previous"],
                active_document,
            )
            return resumed

        web._acquire_or_resume_pre_cutover_lock = (
            acquire_then_change_previous
        )
        web.web_tick()

        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertEqual(
            self._result()["safe_error_code"],
            "rollback_previous_pointer_changed",
        )
        self.assertFalse(self.paths["lock"].exists())

    def test_rollback_restart_after_previous_write_completes_once(self):
        active_document = self._replace_with_rollback_intent()
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._write_ack(self.intent, "applying")
        first_web._acquire_lock(self.intent)
        atomic_write_json(self.paths["previous"], active_document)

        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()
        active_after = self.paths["active"].read_bytes()
        previous_after = self.paths["previous"].read_bytes()
        result_after = self.paths["results"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).read_bytes()
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "committed")
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        previous = read_runtime_pointer(
            self.paths["previous"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, CURRENT_GENERATION)
        self.assertEqual(previous.generation_id, TARGET_GENERATION)
        self.assertEqual(self.paths["active"].read_bytes(), active_after)
        self.assertEqual(
            self.paths["previous"].read_bytes(), previous_after
        )
        self.assertEqual(
            self.paths["results"].joinpath(
                f"{self.intent['intent_id']}.json"
            ).read_bytes(),
            result_after,
        )

    def test_rollback_restart_after_active_write_recovers_once(self):
        active_document = self._replace_with_rollback_intent()
        maintenance = self._quiesce()
        first_web = self._supervisor("web")
        first_web._write_ack(self.intent, "applying")
        first_web._acquire_lock(self.intent)
        atomic_write_json(self.paths["previous"], active_document)
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.current_runtime,
                CURRENT_GENERATION,
                CURRENT_DIGEST,
                self.intent["document_digest"],
            ),
        )

        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()
        active_after = self.paths["active"].read_bytes()
        result_after = self.paths["results"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).read_bytes()
        restarted_web.web_tick()

        self.assertEqual(self._result()["status"], "rolled_back")
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertEqual(self.paths["active"].read_bytes(), active_after)
        self.assertEqual(
            self.paths["results"].joinpath(
                f"{self.intent['intent_id']}.json"
            ).read_bytes(),
            result_after,
        )

    def test_committed_intent_replay_is_idempotent(self):
        maintenance = self._quiesce()
        web = self._supervisor("web", maintenance=maintenance)
        web.web_tick()
        pointer_before = self.paths["active"].read_bytes()
        result_before = self.paths["results"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).read_bytes()

        self._supervisor("web", maintenance=maintenance).web_tick()

        self.assertEqual(self.paths["active"].read_bytes(), pointer_before)
        self.assertEqual(
            self.paths["results"].joinpath(
                f"{self.intent['intent_id']}.json"
            ).read_bytes(),
            result_before,
        )

    def test_failed_prestart_verification_rolls_back_and_verifies(self):
        maintenance = self._quiesce()
        calls = {"count": 0}

        def run_command(*args, **kwargs):
            calls["count"] += 1
            return SimpleNamespace(
                returncode=1 if calls["count"] == 1 else 0
            )

        web = self._supervisor(
            "web",
            run_command=run_command,
            maintenance=maintenance,
        )
        web.web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        result = self._result()
        self.assertEqual(active.generation_id, CURRENT_GENERATION)
        self.assertEqual(result["status"], "rolled_back")
        self.assertNotEqual(
            self.current_runtime.stat().st_mode & 0o200, 0
        )
        self.assertEqual(
            self.target_runtime.stat().st_mode & 0o222, 0
        )
        self.assertEqual(
            result["safe_error_code"],
            "activation_runtime_command_failed",
        )

    def test_restart_after_pointer_switch_conservatively_rolls_back(self):
        maintenance = self._quiesce()
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.target_runtime,
                TARGET_GENERATION,
                TARGET_DIGEST,
                self.intent["document_digest"],
            ),
        )
        atomic_write_json(
            self.paths["lock"],
            {
                "intent_id": self.intent["intent_id"],
                "process_id": 999999,
            },
        )
        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, CURRENT_GENERATION)
        self.assertEqual(self._result()["status"], "rolled_back")
        self.assertEqual(
            self._result()["safe_error_code"],
            "activation_incomplete_recovered",
        )
        self.assertFalse(self.paths["lock"].exists())

    def test_restart_does_not_reconcile_through_another_intent_lock(self):
        maintenance = self._quiesce()
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )
        atomic_write_json(
            self.paths["active"],
            make_pointer(
                self.target_runtime,
                TARGET_GENERATION,
                TARGET_DIGEST,
                self.intent["document_digest"],
            ),
        )
        atomic_write_json(
            self.paths["lock"],
            {"intent_id": str(uuid.uuid4()), "process_id": 5150},
        )
        restarted_web = self._supervisor(
            "web", maintenance=maintenance
        )
        restarted_web.web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertFalse(
            self.paths["results"]
            .joinpath(f"{self.intent['intent_id']}.json")
            .exists()
        )

    def test_production_supervisor_never_touches_pointer(self):
        production_environment = {
            **self.environment,
            "APP_ENV": "production",
        }
        web = RuntimeSupervisor(
            "web",
            environment=production_environment,
            run_command=lambda *args, **kwargs: SimpleNamespace(
                returncode=0
            ),
            urlopen=self._urlopen,
            sleep=lambda seconds: None,
        )
        web.child = FakeChild()
        web.stop_child = lambda timeout=30: self.fail(
            "production supervisor attempted to stop child"
        )
        web.web_tick()
        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        self.assertEqual(active.generation_id, CURRENT_GENERATION)
        self.assertEqual(
            self._result()["safe_error_code"],
            "production_activation_disabled",
        )

    def test_rollback_supervisor_rejects_changed_previous_pointer(self):
        self.paths["intents"].joinpath(
            f"{self.intent['intent_id']}.json"
        ).unlink()
        active_document = make_pointer(
            self.target_runtime,
            TARGET_GENERATION,
            TARGET_DIGEST,
            "active-local-candidate",
        )
        atomic_write_json(self.paths["active"], active_document)
        atomic_write_json(
            self.paths["previous"], self.current_pointer_document
        )
        rollback_id = str(uuid.uuid4())
        rollback_intent = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": rollback_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": CURRENT_GENERATION,
                "target_manifest_digest": CURRENT_DIGEST,
                "target_runtime_path": str(self.current_runtime),
                "previous_generation_id": TARGET_GENERATION,
                "previous_pointer_digest": active_document[
                    "document_digest"
                ],
                "activation_mode": "rollback",
                "rollback_previous_generation_id": CURRENT_GENERATION,
                "rollback_previous_manifest_digest": CURRENT_DIGEST,
                "rollback_previous_pointer_digest": (
                    self.current_pointer_document["document_digest"]
                ),
                "smoke_queries_digest": "c" * 64,
                "expires_at_unix": int(time.time()) + 300,
                "state_version": 1,
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["intents"] / f"{rollback_id}.json",
            rollback_intent,
        )
        atomic_write_json(
            self.paths["previous"],
            make_pointer(
                self.target_runtime,
                TARGET_GENERATION,
                TARGET_DIGEST,
                "changed-previous-authority",
            ),
        )
        web = self._supervisor("web")
        web.stop_child = lambda timeout=30: self.fail(
            "invalid rollback stopped the application"
        )

        web.web_tick()

        active = read_runtime_pointer(
            self.paths["active"],
            deployment_id=DEPLOYMENT_ID,
            signing_key=SIGNING_KEY,
            runtime_root=self.runtime_root,
        )
        result = read_signed_document(
            self.paths["results"] / f"{rollback_id}.json",
            signing_key=SIGNING_KEY,
            expected_kind="activation_result",
            deployment_id=DEPLOYMENT_ID,
        )
        self.assertEqual(active.generation_id, TARGET_GENERATION)
        self.assertEqual(
            result["safe_error_code"],
            "rollback_previous_pointer_changed",
        )


class ActivationRuntimeVerificationTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.runtime_root = self.root / "runtime"
        self.runtime = create_runtime(
            self.runtime_root, TARGET_GENERATION, TARGET_DIGEST
        )
        self.smoke_file = self.root / "activation-smoke.json"
        smoke_payload = json.dumps(
            {
                "queries": [
                    {
                        "locale": "en",
                        "query": "English verification",
                        "allow_empty": True,
                    },
                    {
                        "locale": "mr",
                        "query": "मराठी पडताळणी",
                        "allow_empty": True,
                    },
                ]
            },
            separators=(",", ":"),
        ).encode()
        self.smoke_file.write_bytes(smoke_payload)
        self.intent_id = str(uuid.uuid4())
        intent = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": self.intent_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": TARGET_GENERATION,
                "target_manifest_digest": TARGET_DIGEST,
                "smoke_queries_digest": hashlib.sha256(
                    smoke_payload
                ).hexdigest(),
            },
            SIGNING_KEY,
        )
        paths = runtime_control_paths(self.control)
        atomic_write_json(
            paths["intents"] / f"{self.intent_id}.json", intent
        )
        atomic_write_json(
            paths["active"],
            make_pointer(
                self.runtime,
                TARGET_GENERATION,
                TARGET_DIGEST,
                intent["document_digest"],
            ),
        )
        get_user_model().objects.create_superuser(
            username="recovery",
            password="recovery-password",
            role="superadmin",
        )
        self.settings_override = override_settings(
            DATA_CONTROL_ROOT=self.control,
            RUNTIME_GENERATIONS_ROOT=self.runtime_root,
            ACTIVATION_INTENT_SIGNING_KEY=SIGNING_KEY,
            ACTIVATION_SMOKE_QUERIES_FILE=self.smoke_file,
            ACTIVATION_RECOVERY_SUPERADMIN_USERNAME="recovery",
            ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="recovery-password",
            ENV_IDENTITY=staging_identity(),
            RUNTIME_GENERATION_ID=TARGET_GENERATION,
            RUNTIME_MANIFEST_DIGEST=TARGET_DIGEST,
        )
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def test_management_command_proves_runtime_and_recovery_login(self):
        output = io.StringIO()
        with patch(
            "vaultops.services.runtime_verification."
            "_validate_faiss_coherence"
        ):
            call_command(
                "verify_activation_runtime",
                intent_id=self.intent_id,
                stdout=output,
            )
        self.assertIn(
            f"Activation runtime verified: {TARGET_GENERATION}",
            output.getvalue(),
        )

    def test_management_command_emits_bounded_bilingual_json(self):
        operator = get_user_model().objects.get(username="recovery")
        Folder.objects.create(name="Certification", created_by=operator)
        output = io.StringIO()
        with (
            patch(
                "vaultops.services.runtime_verification."
                "_validate_faiss_coherence"
            ),
            patch(
                "vaultops.services.runtime_verification.search_pdfs_fast",
                return_value=("bounded answer", [{"source": "bounded"}]),
            ),
        ):
            call_command(
                "verify_activation_runtime",
                intent_id=self.intent_id,
                json=True,
                stdout=output,
            )
        evidence = json.loads(output.getvalue())
        self.assertEqual(evidence["executed_locales"], ["en", "mr"])
        self.assertEqual(
            [item["locale"] for item in evidence["queries"]],
            ["en", "mr"],
        )
        self.assertTrue(
            all(
                len(item["query_sha256"]) == 64
                and item["answer_present"]
                and item["reference_count"] == 1
                and "query" not in item
                for item in evidence["queries"]
            )
        )

    def test_management_command_rejects_wrong_runtime_manifest(self):
        with self.settings(RUNTIME_MANIFEST_DIGEST="f" * 64):
            with self.assertRaisesRegex(
                CommandError, "activation_runtime_identity_mismatch"
            ):
                call_command(
                    "verify_activation_runtime",
                    intent_id=self.intent_id,
                )

    def test_runtime_rejects_archived_blank_unsafe_and_missing_media(self):
        operator = get_user_model().objects.get(username="recovery")
        folder = Folder.objects.create(name="Custody", created_by=operator)
        for label, value, expected_reason in (
            ("blank", "", "activation_pdf_path_invalid"),
            ("unsafe", "../outside.pdf", "activation_pdf_path_invalid"),
            ("missing", "pdfs/missing.pdf", "activation_pdf_missing"),
        ):
            with self.subTest(label=label):
                pdf = PDFFile.objects.create(
                    title=f"Archived {label}",
                    file=value,
                    folder=folder,
                    uploaded_by=operator,
                    lifecycle="archived",
                )
                with (
                    patch(
                        "vaultops.services.runtime_verification."
                        "_validate_faiss_coherence"
                    ),
                    self.assertRaisesRegex(CommandError, expected_reason),
                ):
                    call_command(
                        "verify_activation_runtime",
                        intent_id=self.intent_id,
                    )
                PDFFile.objects.filter(pk=pdf.pk).delete()

    def test_management_command_writes_bounded_failure_evidence(self):
        failure_path = self.root / "activation-failure.json"
        with (
            self.settings(RUNTIME_MANIFEST_DIGEST="f" * 64),
            patch.dict(
                os.environ,
                {
                    "ACTIVATION_VERIFICATION_FAILURE_PATH": str(
                        failure_path
                    )
                },
            ),
            self.assertRaisesRegex(
                CommandError,
                "activation_runtime_identity_mismatch",
            ),
        ):
            call_command(
                "verify_activation_runtime",
                intent_id=self.intent_id,
            )

        self.assertEqual(
            json.loads(failure_path.read_text(encoding="utf-8")),
            {
                "schema_version": 1,
                "reason_code": "activation_runtime_identity_mismatch",
            },
        )
