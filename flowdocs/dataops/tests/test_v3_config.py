"""Minimal DataOps v3 configuration contract tests."""

from __future__ import annotations

from dataclasses import replace
import json
import unittest

from dataops.v3_config import (
    ConnectionView,
    V3ConfigurationError,
    bootstrap_connection_from_environment,
    compile_runtime_config,
    default_policy,
)


class V3ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.connection = ConnectionView(
            public_id="connection-1",
            name="Primary RustFS",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            region="us-east-1",
            prefix="v3",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            capabilities={"read": True, "write": True, "conditional_write": True},
            source="control_database",
        )

    def compile(self, **overrides):
        values = {
            "environment": "stage",
            "deployment_id": "stage-2026",
            "dataset_id": "ai-sahakar-stage-2026",
            "enabled": True,
            "connection": self.connection,
        }
        values.update(overrides)
        return compile_runtime_config(**values)

    def test_standard_contract_has_one_connection_and_no_profile_selectors(self):
        config = self.compile()
        serialized = json.dumps(config.as_dict(), sort_keys=True)
        self.assertNotIn("source_profile", serialized)
        self.assertNotIn("destination_profile", serialized)
        self.assertNotIn("backup_role", serialized)
        self.assertEqual(config.connection.dataset_id, config.dataset_id)

    def test_config_digest_is_deterministic_and_secret_free(self):
        first = self.compile()
        second = self.compile()
        self.assertEqual(first, second)
        self.assertEqual(len(first.digest), 64)
        serialized = json.dumps(first.as_dict(), sort_keys=True).lower()
        self.assertNotIn("secret_key", serialized)
        self.assertNotIn("access_key", serialized)

    def test_connection_must_own_the_local_dataset(self):
        with self.assertRaisesRegex(V3ConfigurationError, "primary_connection_dataset_mismatch"):
            self.compile(connection=replace(self.connection, dataset_id="foreign"))

    def test_environment_changes_policy_strength_not_feature_existence(self):
        development = default_policy("development")
        staging = default_policy("staging")
        production = default_policy("production")
        self.assertEqual(development["backup"]["mode"], "manual")
        self.assertEqual(staging["backup"]["mode"], "manual")
        self.assertEqual(production["backup"]["mode"], "scheduled")
        self.assertTrue(production["restore"]["pre_restore_backup"])
        self.assertIn("restore", development)
        self.assertIn("restore", staging)

    def test_transition_bootstrap_reads_no_secret_values(self):
        environment = {
            "ARTIFACT_VAULT_ENDPOINT": "https://rustfs.example.invalid",
            "ARTIFACT_VAULT_BUCKET": "stage-recovery",
            "ARTIFACT_VAULT_REGION": "us-east-1",
            "ARTIFACT_VAULT_ACCESS_KEY": "do-not-copy-access",
            "ARTIFACT_VAULT_SECRET_KEY": "do-not-copy-secret",
        }
        connection = bootstrap_connection_from_environment(
            environment,
            dataset_id="ai-sahakar-stage-2026",
        )
        serialized = json.dumps(connection.redacted(), sort_keys=True)
        self.assertNotIn("do-not-copy-access", serialized)
        self.assertNotIn("do-not-copy-secret", serialized)
        self.assertEqual(connection.credential_ref, "env://ARTIFACT_VAULT")
        self.assertTrue(connection.capabilities["conditional_write"])

    def test_incomplete_transition_bootstrap_fails_with_typed_code(self):
        with self.assertRaisesRegex(V3ConfigurationError, "owned_connection_bucket_missing"):
            bootstrap_connection_from_environment(
                {"ARTIFACT_VAULT_ENDPOINT": "https://rustfs.example.invalid"},
                dataset_id="stage",
            )

    def test_absent_bootstrap_is_not_a_boot_failure(self):
        self.assertIsNone(
            bootstrap_connection_from_environment({}, dataset_id="stage")
        )


if __name__ == "__main__":
    unittest.main()
