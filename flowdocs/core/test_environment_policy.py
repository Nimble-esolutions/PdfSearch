from types import SimpleNamespace

from django.test import SimpleTestCase

from core.environment import AppEnv
from core.environment_policy import EnvironmentDirectionPolicy, Operation


def identity(**overrides):
    values = {
        "app_env": AppEnv.STAGING,
        "dataset_id": "stage-dataset",
        "authoritative_dataset_id": "prod-dataset",
        "deployment_id": "stage-deployment",
        "is_authoritative_writer": False,
        "is_production": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class EnvironmentDirectionPolicyTests(SimpleTestCase):
    def decision(self, operation, **overrides):
        remote_dataset_id = overrides.pop("remote_dataset_id", "")
        return EnvironmentDirectionPolicy.from_identity(
            identity(**overrides)
        ).decision(operation, remote_dataset_id=remote_dataset_id)

    def test_production_writer_can_publish_only_its_authority(self):
        common = {
            "app_env": AppEnv.PRODUCTION,
            "dataset_id": "prod-dataset",
            "authoritative_dataset_id": "prod-dataset",
            "is_authoritative_writer": True,
            "is_production": True,
        }
        for operation in (
            Operation.BACKUP,
            Operation.SYNC,
            Operation.PROMOTE,
        ):
            with self.subTest(operation=operation):
                self.assertTrue(self.decision(operation, **common).allowed)
                mismatch = self.decision(
                    operation,
                    remote_dataset_id="other-dataset",
                    **common,
                )
                self.assertFalse(mismatch.allowed)
                self.assertEqual(
                    mismatch.reason_code, "writer_environment_required"
                )

    def test_reader_and_nonproduction_cannot_publish(self):
        reader = self.decision(
            Operation.PROMOTE,
            app_env=AppEnv.PRODUCTION,
            dataset_id="prod-dataset",
            authoritative_dataset_id="prod-dataset",
            is_production=True,
        )
        stage = self.decision(Operation.BACKUP)
        self.assertFalse(reader.allowed)
        self.assertFalse(stage.allowed)

    def test_contradictory_staging_writer_cannot_publish(self):
        decision = self.decision(
            Operation.PROMOTE,
            app_env=AppEnv.STAGING,
            dataset_id="prod-dataset",
            authoritative_dataset_id="prod-dataset",
            is_authoritative_writer=True,
            is_production=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.reason_code, "writer_environment_required"
        )

    def test_nonproduction_restores_only_from_another_dataset(self):
        allowed = self.decision(
            Operation.RESTORE,
            remote_dataset_id="prod-dataset",
        )
        same = self.decision(
            Operation.RESTORE,
            remote_dataset_id="stage-dataset",
        )
        missing = self.decision(Operation.RESTORE)
        self.assertTrue(allowed.allowed)
        self.assertEqual(same.reason_code, "restore_dataset_mismatch")
        self.assertEqual(missing.reason_code, "restore_environment_required")

    def test_production_cannot_use_ordinary_restore(self):
        decision = self.decision(
            Operation.RESTORE,
            app_env=AppEnv.PRODUCTION,
            is_production=True,
            remote_dataset_id="prod-backup",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "restore_environment_required")

    def test_contradictory_production_identity_fails_closed(self):
        decision = self.decision(
            Operation.RESTORE,
            app_env=AppEnv.PRODUCTION,
            is_production=False,
            remote_dataset_id="prod-backup",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "restore_environment_required")

    def test_unknown_environment_cannot_restore(self):
        for app_env in (None, "", "unknown"):
            with self.subTest(app_env=app_env):
                decision = self.decision(
                    Operation.RESTORE,
                    app_env=app_env,
                    remote_dataset_id="prod-backup",
                )
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.reason_code, "restore_environment_required"
                )

    def test_only_staging_can_activate_or_rollback(self):
        for operation in (Operation.ACTIVATE, Operation.ROLLBACK):
            with self.subTest(operation=operation):
                self.assertTrue(self.decision(operation).allowed)
                development = self.decision(
                    operation, app_env=AppEnv.DEVELOPMENT
                )
                production = self.decision(
                    operation,
                    app_env=AppEnv.PRODUCTION,
                    is_production=True,
                )
                self.assertEqual(
                    development.reason_code, "staging_activation_disabled"
                )
                self.assertEqual(
                    production.reason_code, "production_activation_disabled"
                )

    def test_activation_requires_complete_deployment_identity(self):
        for override in ({"dataset_id": ""}, {"deployment_id": ""}):
            with self.subTest(override=override):
                decision = self.decision(Operation.ACTIVATE, **override)
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.reason_code, "environment_identity_incomplete"
                )

    def test_missing_local_identity_fails_closed(self):
        decision = self.decision(
            Operation.RESTORE,
            dataset_id="",
            remote_dataset_id="prod-dataset",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.reason_code, "environment_identity_incomplete"
        )

    def test_every_operation_has_a_decision(self):
        for operation in Operation:
            with self.subTest(operation=operation):
                decision = self.decision(
                    operation,
                    remote_dataset_id="prod-dataset",
                )
                self.assertIsInstance(decision.allowed, bool)
                self.assertTrue(decision.allowed or decision.reason_code)
