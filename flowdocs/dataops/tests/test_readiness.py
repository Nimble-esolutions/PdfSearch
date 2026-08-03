import json
from types import SimpleNamespace

from django.test import TestCase, override_settings

from dataops.models import DataConnection, DataOperation, DataPolicy
from dataops.readiness import readiness_payload


GENERATION = "dataops-stage-recovery-1"
MANIFEST_DIGEST = "a" * 64
POINTER_DIGEST = "b" * 64


def _identity(environment="staging"):
    return SimpleNamespace(
        app_env=SimpleNamespace(value=environment),
        deployment_id=f"{environment}-2026",
        dataset_id=f"ai-sahakar-{environment}-2026",
    )


def _runtime(*, generation=GENERATION, manifest=MANIFEST_DIGEST):
    return SimpleNamespace(
        generation_id=generation,
        manifest_digest=manifest,
        pointer_digest=POINTER_DIGEST,
    )


@override_settings(
    DATAOPS_ENABLED=True,
    APP_ENV="staging",
    DEPLOYMENT_ID="staging-2026",
    DATASET_ID="ai-sahakar-staging-2026",
    ENV_IDENTITY=_identity(),
    RUNTIME_GENERATION_ID=GENERATION,
    RUNTIME_MANIFEST_DIGEST=MANIFEST_DIGEST,
    ACTIVE_RUNTIME=_runtime(),
    STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED=False,
)
class DataOpsReadinessTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.connection = DataConnection.objects.using("control").create(
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="private-bucket-name",
            dataset_id="ai-sahakar-staging-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
            capabilities={
                "probed": True,
                "read": True,
                "write": True,
                "conditional_write": True,
            },
        )

    def _operation(self, kind, *, activation=None):
        return DataOperation.objects.using("control").create(
            kind=kind,
            state=DataOperation.State.SUCCEEDED,
            connection=self.connection,
            lifecycle_route="backup" if kind == DataOperation.Kind.BACKUP else "restore",
            lifecycle_plan={"contract_version": 3},
            lifecycle_plan_digest="c" * 64,
            result={
                "contract_version": 3,
                "manifest_digest": MANIFEST_DIGEST,
                "activation": activation,
            },
        )

    def test_manual_disposable_stage_needs_no_backup_receipt(self):
        payload = readiness_payload()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["contract_version"], 3)
        self.assertEqual(payload["actions"]["backup"], "ready")
        self.assertEqual(payload["actions"]["restore"], "ready")
        self.assertEqual(payload["receipts"]["backup"]["status"], "not_required")
        self.assertEqual(payload["active_generation"], GENERATION)
        self.assertEqual(payload["manifest_digest"], MANIFEST_DIGEST)
        serialized = json.dumps(payload, default=str)
        self.assertNotIn("rustfs.example.invalid", serialized)
        self.assertNotIn("private-bucket-name", serialized)
        self.assertNotIn("secret://dataops/stage", serialized)

    @override_settings(ACTIVE_RUNTIME=None)
    def test_digest_shape_without_verified_runtime_is_not_signed_evidence(self):
        payload = readiness_payload()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["signed_active_generation"])
        self.assertEqual(payload["active_generation"], "")
        self.assertEqual(payload["manifest_digest"], "")
        self.assertEqual(
            payload["runtime_evidence"]["reason_code"],
            "signed_runtime_pointer_missing",
        )

    @override_settings(
        ACTIVE_RUNTIME=_runtime(generation="another-generation"),
    )
    def test_signed_runtime_must_match_configured_generation_and_manifest(self):
        payload = readiness_payload()

        self.assertFalse(payload["signed_active_generation"])
        self.assertEqual(
            payload["runtime_evidence"]["reason_code"],
            "signed_runtime_identity_mismatch",
        )

    @override_settings(
        APP_ENV="production",
        DEPLOYMENT_ID="production-2026",
        DATASET_ID="ai-sahakar-production-2026",
        ENV_IDENTITY=_identity("production"),
    )
    def test_scheduled_production_policy_requires_v3_backup_receipt(self):
        self.connection.dataset_id = "ai-sahakar-production-2026"
        self.connection.save(using="control", update_fields=["dataset_id", "updated_at"])
        missing = readiness_payload()
        self.assertEqual(missing["status"], "degraded")
        self.assertEqual(missing["receipts"]["backup"]["status"], "missing")

        self._operation(DataOperation.Kind.BACKUP)
        present = readiness_payload()
        self.assertEqual(present["status"], "ok")
        self.assertEqual(present["receipts"]["backup"]["status"], "present")

    def test_explicit_restore_receipt_policy_requires_exact_active_binding(self):
        DataPolicy.objects.using("control").create(
            deployment_id="staging-2026",
            preset=DataPolicy.Preset.STAGING,
            values={
                "backup": {"mode": "manual"},
                "restore": {"require_receipt": True},
            },
        )
        self._operation(
            DataOperation.Kind.RESTORE,
            activation={
                "generation_id": "wrong-generation",
                "manifest_digest": MANIFEST_DIGEST,
            },
        )
        mismatched = readiness_payload()
        self.assertEqual(mismatched["status"], "degraded")
        self.assertEqual(mismatched["receipts"]["restore"]["status"], "missing")

        self._operation(
            DataOperation.Kind.IMPORT,
            activation={
                "generation_id": GENERATION,
                "manifest_digest": MANIFEST_DIGEST,
            },
        )
        matched = readiness_payload()
        self.assertEqual(matched["status"], "ok")
        self.assertEqual(matched["receipts"]["restore"]["status"], "present")
        self.assertEqual(matched["last_restore"]["kind"], DataOperation.Kind.IMPORT)
