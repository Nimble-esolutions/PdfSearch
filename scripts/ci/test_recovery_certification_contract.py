#!/usr/bin/env python3
"""Static safety contract for the isolated recovery-certification runner."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.recovery-cert.yml").read_text(encoding="utf-8")
RUNNER = (
    ROOT / "scripts/ops/run_recovery_certification.sh"
).read_text(encoding="utf-8")


class RecoveryCertificationContractTests(unittest.TestCase):
    def test_startup_superuser_is_explicitly_disabled_for_both_roles(self):
        self.assertEqual(COMPOSE.count('CREATE_SUPERUSER: "0"'), 2)
        self.assertNotIn("CREATE_SUPERUSER: \"1\"", COMPOSE)

    def test_activation_is_phase_controlled_and_ephemeral_inputs_are_required(self):
        self.assertEqual(
            COMPOSE.count(
                "STAGING_RUNTIME_ACTIVATION_ENABLED: "
                "${CERT_ACTIVATION_ENABLED:-0}"
            ),
            2,
        )
        self.assertEqual(
            COMPOSE.count(
                "STAGING_INITIAL_ACTIVATION_ENABLED: "
                "${CERT_ACTIVATION_ENABLED:-0}"
            ),
            2,
        )
        for name in (
            "CERT_ACTIVATION_INTENT_SIGNING_KEY",
            "CERT_ACTIVATION_RECOVERY_SUPERADMIN_USERNAME",
            "CERT_ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD",
        ):
            self.assertIn(f"${{{name}:-}}", COMPOSE)
            self.assertIn(f': "${{{name}:?', RUNNER)
        self.assertIn('export CERT_ACTIVATION_ENABLED=1', RUNNER)
        self.assertIn('export CERT_ACTIVATION_ENABLED=0', RUNNER)
        self.assertIn(
            '[ "$ACTION" = "activate" ] || [ "$ACTION" = "evidence" ]',
            RUNNER,
        )

    def test_shared_sqlite_roles_start_serially(self):
        redis = '"${COMPOSE[@]}" up -d --wait redis'
        web = '"${COMPOSE[@]}" up -d --wait --no-deps web'
        maintenance = '"${COMPOSE[@]}" up -d --wait --no-deps maintenance'
        self.assertLess(RUNNER.rindex(redis), RUNNER.rindex(web))
        self.assertLess(RUNNER.rindex(web), RUNNER.rindex(maintenance))
        self.assertNotIn('up -d --wait redis maintenance web', RUNNER)


if __name__ == "__main__":
    unittest.main()
