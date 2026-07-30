import io
import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.media_quarantine import (
    build_unavailable_attestation,
    storage_key_evidence,
)
from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    RuntimePointerObservation,
    VaultConnectionProfile,
)
from vaultops.runtime_control import (
    atomic_write_json,
    build_runtime_pointer,
    runtime_control_paths,
    sign_document,
)
from vaultops.services.certification import (
    RecoveryCertificationError,
    verify_recovery_certification,
)

from .test_activation_supervisor import (
    DEPLOYMENT_ID,
    SIGNING_KEY,
    TARGET_DIGEST,
    TARGET_GENERATION,
    create_runtime,
    staging_identity,
)


class RecoveryCertificationTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.control = self.root / "control"
        self.runtime_root = self.root / "runtime"
        self.runtime = create_runtime(
            self.runtime_root, TARGET_GENERATION, TARGET_DIGEST
        )
        self.intent_id = str(uuid.uuid4())
        self.paths = runtime_control_paths(self.control)
        self.intent_document = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_intent",
                "intent_id": self.intent_id,
                "deployment_id": DEPLOYMENT_ID,
                "target_generation_id": TARGET_GENERATION,
                "target_manifest_digest": TARGET_DIGEST,
                "target_runtime_path": str(self.runtime),
                "previous_generation_id": "",
                "previous_pointer_digest": "",
                "activation_mode": "initial",
                "smoke_queries_digest": "c" * 64,
                "expires_at_unix": int(timezone.now().timestamp()) + 300,
                "state_version": 1,
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["intents"] / f"{self.intent_id}.json",
            self.intent_document,
        )
        self.pointer_document = build_runtime_pointer(
            deployment_id=DEPLOYMENT_ID,
            generation_id=TARGET_GENERATION,
            manifest_digest=TARGET_DIGEST,
            runtime_path=self.runtime,
            intent_digest=self.intent_document["document_digest"],
            state_version=1,
            signing_key=SIGNING_KEY,
        )
        atomic_write_json(self.paths["active"], self.pointer_document)
        self.readiness = {
            "livez": "ok",
            "readyz": "ready",
            "runtime_generation_id": TARGET_GENERATION,
            "runtime_manifest_digest": TARGET_DIGEST,
            "runtime_smoke": "passed",
            "initial_activation": True,
        }
        self.process_identity = {
            "supervisor_pid": 10,
            "child_pid": 11,
            "role": "web",
        }
        self.result_document = sign_document(
            {
                "schema_version": 1,
                "kind": "activation_result",
                "deployment_id": DEPLOYMENT_ID,
                "intent_id": self.intent_id,
                "intent_digest": self.intent_document["document_digest"],
                "status": "committed",
                "active_generation_id": TARGET_GENERATION,
                "active_manifest_digest": TARGET_DIGEST,
                "previous_generation_id": "",
                "active_pointer_digest": self.pointer_document[
                    "document_digest"
                ],
                "readiness_evidence": self.readiness,
                "process_identity": self.process_identity,
                "safe_error_code": "",
                "observed_at_unix": int(timezone.now().timestamp()),
            },
            SIGNING_KEY,
        )
        atomic_write_json(
            self.paths["results"] / f"{self.intent_id}.json",
            self.result_document,
        )
        profile = VaultConnectionProfile.objects.using("control").create(
            key="certification",
            display_name="Certification",
            dataset_id="ai-sahakar-prod",
        )
        self.generation = ArtifactGeneration.objects.using("control").create(
            profile=profile,
            dataset_id="ai-sahakar-prod",
            generation_id=TARGET_GENERATION,
            manifest_digest=TARGET_DIGEST,
            deployment_id=DEPLOYMENT_ID,
            runtime_state=ArtifactGeneration.RuntimeState.ACTIVE,
        )
        now = timezone.now()
        self.intent = ActivationIntent.objects.using("control").create(
            public_id=self.intent_id,
            deployment_id=DEPLOYMENT_ID,
            target_generation_id=TARGET_GENERATION,
            previous_generation_id="",
            manifest_digest=TARGET_DIGEST,
            intent_digest=self.intent_document["document_digest"],
            state=ActivationIntent.State.COMMITTED,
            checkpoint={
                "initial_activation": True,
                "protocol_state": "committed",
                "active_pointer_digest": self.pointer_document[
                    "document_digest"
                ],
            },
            applied_at=now,
            committed_at=now,
            expires_at=now,
        )
        self.observation = RuntimePointerObservation.objects.using(
            "control"
        ).create(
            deployment_id=DEPLOYMENT_ID,
            active_generation_id=TARGET_GENERATION,
            previous_generation_id="",
            pointer_digest=self.pointer_document["document_digest"],
            process_identity=self.process_identity,
            readiness_evidence=self.readiness,
            status="committed",
            observed_at=now,
        )
        self.settings_override = override_settings(
            DATA_CONTROL_ROOT=self.control,
            RUNTIME_GENERATIONS_ROOT=self.runtime_root,
            ACTIVATION_INTENT_SIGNING_KEY=SIGNING_KEY,
            ENV_IDENTITY=staging_identity(),
        )
        self.settings_override.enable()
        self.runtime_patch = patch(
            "vaultops.services.certification.verify_activation_runtime",
            return_value={
                "schema_version": 1,
                "generation_id": TARGET_GENERATION,
                "manifest_digest": TARGET_DIGEST,
                "executed_locales": ["en", "mr"],
                "queries": [],
                "unavailable_documents": build_unavailable_attestation(()),
            },
        )
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        self.settings_override.disable()
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.exists():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()
        super().tearDown()

    def verify(self):
        return verify_recovery_certification(
            intent_id=self.intent_id,
            expected_generation_id=TARGET_GENERATION,
            expected_manifest_digest=TARGET_DIGEST,
            require_initial=True,
        )

    def assert_reason(self, reason_code):
        with self.assertRaisesMessage(
            RecoveryCertificationError, reason_code
        ):
            self.verify()

    def test_accepts_signed_committed_initial_activation(self):
        evidence = self.verify()

        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["activation_result"], "committed")
        self.assertEqual(evidence["control_projection"], "committed")
        self.assertEqual(evidence["runtime"]["executed_locales"], ["en", "mr"])

    def test_management_command_emits_bounded_json(self):
        output = io.StringIO()
        call_command(
            "verify_recovery_certification",
            intent_id=self.intent_id,
            generation_id=TARGET_GENERATION,
            manifest_digest=TARGET_DIGEST,
            require_initial=True,
            stdout=output,
        )

        evidence = json.loads(output.getvalue())
        self.assertEqual(evidence["intent_id"], self.intent_id)
        self.assertNotIn("readiness_evidence", evidence)

    def test_rejects_missing_or_failed_signed_result(self):
        self.paths["results"].joinpath(f"{self.intent_id}.json").unlink()
        self.assert_reason("recovery_certification_signed_evidence_invalid")

        atomic_write_json(
            self.paths["results"] / f"{self.intent_id}.json",
            sign_document(
                {
                    **{
                        key: value
                        for key, value in self.result_document.items()
                        if key not in {"document_digest", "signature"}
                    },
                    "status": "failed",
                    "safe_error_code": "activation_failed",
                },
                SIGNING_KEY,
            ),
        )
        self.assert_reason("recovery_certification_result_uncommitted")

    def test_rejects_pending_intent_or_inactive_generation(self):
        self.intent.state = ActivationIntent.State.PENDING
        self.intent.save(update_fields=["state", "updated_at"])
        self.assert_reason(
            "recovery_certification_intent_projection_mismatch"
        )

        self.intent.state = ActivationIntent.State.COMMITTED
        self.intent.save(update_fields=["state", "updated_at"])
        self.generation.runtime_state = ArtifactGeneration.RuntimeState.PENDING
        self.generation.save(update_fields=["runtime_state", "updated_at"])
        self.assert_reason("recovery_certification_generation_not_active")

    def test_rejects_observation_not_bound_to_signed_result(self):
        self.observation.readiness_evidence = {
            **self.readiness,
            "runtime_manifest_digest": "f" * 64,
        }
        self.observation.save(update_fields=["readiness_evidence"])
        self.assert_reason("recovery_certification_observation_mismatch")

    def test_rejects_fabricated_previous_authority_for_fresh_drill(self):
        atomic_write_json(self.paths["previous"], self.pointer_document)
        self.assert_reason(
            "recovery_certification_initial_authority_invalid"
        )

    def test_rejects_incomplete_bilingual_runtime_smoke(self):
        with patch(
            "vaultops.services.certification.verify_activation_runtime",
            return_value={
                "executed_locales": ["en"],
                "unavailable_documents": build_unavailable_attestation(()),
            },
        ):
            self.assert_reason(
                "recovery_certification_bilingual_smoke_incomplete"
            )

    def test_missing_legacy_intent_rejects_nonempty_runtime_attestation(self):
        storage = storage_key_evidence("pdfs/unavailable.pdf")
        actual = build_unavailable_attestation(
            (
                {
                    "id": 1,
                    "lifecycle": "unavailable",
                    "storage_key_status": storage["status"],
                    "storage_key_token_sha256": storage["token_sha256"],
                    "expected_sha256": "a" * 64,
                    "expected_size": 10,
                    "prior_lifecycle": "uploaded",
                },
            )
        )
        with patch(
            "vaultops.services.certification.verify_activation_runtime",
            return_value={
                "executed_locales": ["en", "mr"],
                "unavailable_documents": actual,
            },
        ):
            self.assert_reason(
                "recovery_certification_runtime_attestation_mismatch"
            )

    def test_command_fails_closed_with_stable_reason(self):
        self.intent.state = ActivationIntent.State.FAILED
        self.intent.save(update_fields=["state", "updated_at"])
        with self.assertRaisesRegex(
            CommandError,
            "recovery_certification_intent_projection_mismatch",
        ):
            call_command(
                "verify_recovery_certification",
                intent_id=self.intent_id,
                generation_id=TARGET_GENERATION,
                manifest_digest=TARGET_DIGEST,
                require_initial=True,
            )
