import tempfile
from pathlib import Path

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.environment import (
    AppEnv,
    DataMode,
    EnvironmentIdentity,
    RestorePolicy,
)
from core.management.commands.startup_restore_preflight import (
    RESTORE_REQUIRED_CODE,
    evaluate_startup_restore_preflight,
)


class StartupRestoreIdentityTests(SimpleTestCase):
    def test_explicit_startup_pinned_retains_generation_outside_pinned_mode(self):
        identity = EnvironmentIdentity.from_env(
            environ={
                "APP_ENV": "development",
                "DATA_MODE": "local",
                "RESTORE_POLICY": "startup-pinned",
                "DATA_PINNED_GENERATION": "generation-2026-07-29",
            }
        )

        self.assertEqual(identity.data_mode, DataMode.LOCAL)
        self.assertEqual(
            identity.restore_policy, RestorePolicy.STARTUP_PINNED
        )
        self.assertEqual(
            identity.data_pinned_generation, "generation-2026-07-29"
        )
        self.assertFalse(
            any("DATA_PINNED_GENERATION" in error for error in identity.validate())
        )

    def test_explicit_startup_pinned_requires_generation(self):
        identity = EnvironmentIdentity.from_env(
            environ={
                "APP_ENV": "development",
                "DATA_MODE": "local",
                "RESTORE_POLICY": "startup-pinned",
            }
        )

        self.assertIn(
            "DATA_PINNED_GENERATION is required when "
            "RESTORE_POLICY=startup-pinned",
            identity.validate(),
        )


class StartupRestorePreflightTests(SimpleTestCase):
    def identity(self, policy):
        return EnvironmentIdentity(
            app_env=AppEnv.DEVELOPMENT,
            restore_policy=policy,
        )

    def test_disabled_and_manual_policies_allow_absent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "db.sqlite3"
            for policy in (RestorePolicy.DISABLED, RestorePolicy.MANUAL):
                with self.subTest(policy=policy):
                    self.assertEqual(
                        evaluate_startup_restore_preflight(
                            self.identity(policy), database
                        ),
                        "policy_allows_normal_startup",
                    )
            self.assertFalse(database.exists())

    def test_startup_policies_reject_absent_and_zero_byte_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "db.sqlite3"
            for policy in (
                RestorePolicy.STARTUP_LATEST,
                RestorePolicy.STARTUP_PINNED,
            ):
                for create_zero_byte in (False, True):
                    with self.subTest(
                        policy=policy, zero=create_zero_byte
                    ):
                        if database.exists():
                            database.unlink()
                        if create_zero_byte:
                            database.touch()
                        with self.assertRaisesRegex(
                            CommandError, RESTORE_REQUIRED_CODE
                        ):
                            evaluate_startup_restore_preflight(
                                self.identity(policy), database
                            )
                        self.assertEqual(
                            database.stat().st_size if database.exists() else None,
                            0 if create_zero_byte else None,
                        )

    def test_startup_policies_preserve_existing_database_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "db.sqlite3"
            payload = b"existing-database"
            database.write_bytes(payload)

            for policy in (
                RestorePolicy.STARTUP_LATEST,
                RestorePolicy.STARTUP_PINNED,
            ):
                with self.subTest(policy=policy):
                    self.assertEqual(
                        evaluate_startup_restore_preflight(
                            self.identity(policy), database
                        ),
                        "existing_database_preserved",
                    )
                    self.assertEqual(database.read_bytes(), payload)
