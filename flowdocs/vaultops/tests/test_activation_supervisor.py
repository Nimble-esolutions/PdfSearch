import io
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from runtime_supervisor import RuntimeSupervisor
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
from vaultops.services.activation import (
    ActivationCoordinatorError,
    prepare_previous_runtime_rollback,
    reconcile_activation_result,
    schedule_activation,
)


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
        self._configure_signed_local_candidate_as_active()
        workspace = prepare_previous_runtime_rollback()

        intent = schedule_activation(workspace, confirmed=True)
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

    def test_rollback_rejects_stale_runtime_observation(self):
        self._configure_signed_local_candidate_as_active()
        RuntimePointerObservation.objects.update(pointer_digest="0" * 64)

        with self.assertRaisesMessage(
            ActivationCoordinatorError,
            "rollback_pointer_observation_stale",
        ):
            prepare_previous_runtime_rollback()

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
                "previous_generation_id": CURRENT_GENERATION,
                "active_pointer_digest": active.pointer_digest,
                "readiness_evidence": {"readyz": "ready"},
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
            }
        )

    def _supervisor(self, role, run_command=None, maintenance=None):
        supervisor = RuntimeSupervisor(
            role,
            environment=self.environment,
            run_command=run_command
            or (lambda *args, **kwargs: SimpleNamespace(returncode=0)),
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
        self.assertEqual(checkpoint["state"], "committed")

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
            "vaultops.management.commands."
            "verify_activation_runtime._validate_faiss_coherence"
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
