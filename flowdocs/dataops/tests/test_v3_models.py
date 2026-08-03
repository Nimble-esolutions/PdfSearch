"""Additive control-database model tests for DataOps v3."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from dataops.models import DataConnection, DataOperation, DataPolicy, RecoveryPoint


class DataOpsV3ModelTests(TestCase):
    databases = {"default", "control"}

    def connection(self, **overrides):
        values = {
            "name": "Primary RustFS",
            "provider": "rustfs",
            "endpoint": "https://rustfs.example.invalid",
            "bucket": "stage-recovery",
            "dataset_id": "ai-sahakar-stage-2026",
            "credential_ref": "secret://dataops/stage",
            "is_primary": True,
            "capabilities": {
                "read": True,
                "write": True,
                "conditional_write": True,
            },
        }
        values.update(overrides)
        return DataConnection.objects.using("control").create(**values)

    def test_connection_contains_references_and_capabilities_not_secret_values(self):
        connection = self.connection()
        field_names = {field.name for field in connection._meta.fields}
        self.assertIn("credential_ref", field_names)
        self.assertNotIn("access_key", field_names)
        self.assertNotIn("secret_key", field_names)
        self.assertTrue(connection.capabilities["conditional_write"])

    def test_only_one_primary_connection_exists_for_a_dataset(self):
        self.connection()
        with self.assertRaises(IntegrityError), transaction.atomic(using="control"):
            self.connection(name="Other", bucket="other")

    def test_foreign_readable_connection_does_not_need_a_profile_role(self):
        connection = self.connection(
            name="Legacy production source",
            bucket="prod-source",
            dataset_id="ai-sahakar-prod-v2",
            is_primary=False,
            capabilities={"read": True, "write": False},
        )
        field_names = {field.name for field in connection._meta.fields}
        self.assertNotIn("role", field_names)
        self.assertFalse(connection.capabilities["write"])

    def test_policy_is_versioned_per_deployment(self):
        policy = DataPolicy.objects.using("control").create(
            deployment_id="stage-2026",
            preset=DataPolicy.Preset.STAGING,
            values={"backup": {"mode": "manual"}},
        )
        self.assertEqual(policy.schema_version, 3)
        with self.assertRaises(IntegrityError), transaction.atomic(using="control"):
            DataPolicy.objects.using("control").create(
                deployment_id="stage-2026",
                preset=DataPolicy.Preset.STAGING,
            )

    def test_operation_persists_the_exact_secret_free_plan_digest(self):
        connection = self.connection()
        operation = DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.IMPORT,
            connection=connection,
            lifecycle_route="import_rebind_restore",
            lifecycle_plan={"contract_version": 3, "route": "import_rebind_restore"},
            lifecycle_plan_digest="d" * 64,
        )
        self.assertEqual(operation.lifecycle_plan_digest, "d" * 64)
        self.assertEqual(operation.connection, connection)

    def test_v3_recovery_point_does_not_require_a_legacy_profile_selector(self):
        connection = self.connection()
        point = RecoveryPoint.objects.using("control").create(
            connection=connection,
            dataset_id=connection.dataset_id,
            release_id="rp-20260803-001",
            format_version=3,
            prefix="v3/datasets/ai-sahakar-stage-2026/recovery-points/rp-20260803-001.json",
            manifest_digest="e" * 64,
            signature_key_id="stage-manifest-1",
            data_complete=True,
            activation_ready=False,
            state=RecoveryPoint.State.VERIFIED,
        )
        self.assertEqual(point.profile_key, "")
        self.assertTrue(point.data_complete)
