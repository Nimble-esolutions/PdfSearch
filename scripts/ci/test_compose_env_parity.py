from __future__ import annotations

import unittest

from scripts.ci.assert_compose_env_parity import (
    CRITICAL_KEYS,
    find_parity_errors,
)


def _services():
    environment = {key: f"value-for-{key}" for key in CRITICAL_KEYS}
    return {
        "web": {"environment": dict(environment)},
        "maintenance": {"environment": dict(environment)},
    }


class ComposeEnvironmentParityTests(unittest.TestCase):
    def test_complete_matching_contract_passes(self):
        self.assertEqual(find_parity_errors(_services()), ([], []))

    def test_every_critical_key_is_required_on_both_services(self):
        for service_name in ("web", "maintenance"):
            for key in CRITICAL_KEYS:
                with self.subTest(service=service_name, key=key):
                    services = _services()
                    del services[service_name]["environment"][key]

                    missing, divergent = find_parity_errors(services)

                    self.assertEqual(missing, [key])
                    self.assertEqual(divergent, [])

    def test_every_critical_key_must_match(self):
        for key in CRITICAL_KEYS:
            with self.subTest(key=key):
                services = _services()
                services["maintenance"]["environment"][key] = "different"

                missing, divergent = find_parity_errors(services)

                self.assertEqual(missing, [])
                self.assertEqual(divergent, [key])

    def test_errors_report_keys_without_values(self):
        services = _services()
        services["web"]["environment"]["VAULT_CREDENTIAL_ALIASES"] = (
            "private-alias-value"
        )

        missing, divergent = find_parity_errors(services)

        self.assertEqual(missing, [])
        self.assertEqual(divergent, ["VAULT_CREDENTIAL_ALIASES"])
        self.assertNotIn("private-alias-value", repr((missing, divergent)))


if __name__ == "__main__":
    unittest.main()
