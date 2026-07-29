from __future__ import annotations

import unittest

from scripts.ci.assert_compose_env_parity import (
    CRITICAL_KEYS,
    find_image_contract_errors,
    find_parity_errors,
)


def _services():
    environment = {key: f"value-for-{key}" for key in CRITICAL_KEYS}
    image = (
        "ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:"
        + ("a" * 64)
    )
    return {
        "web": {"environment": dict(environment), "image": image},
        "maintenance": {"environment": dict(environment), "image": image},
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

    def test_canonical_immutable_image_contract_passes(self):
        self.assertEqual(find_image_contract_errors(_services()), [])

    def test_registry_port_is_not_mistaken_for_an_image_tag(self):
        services = _services()
        reference = (
            "registry.example:5000/org/image@sha256:" + ("b" * 64)
        )
        services["web"]["image"] = reference
        services["maintenance"]["image"] = reference

        self.assertEqual(find_image_contract_errors(services), [])

    def test_missing_image_fails_without_exposing_a_reference(self):
        for service_name in ("web", "maintenance"):
            with self.subTest(service=service_name):
                services = _services()
                del services[service_name]["image"]

                errors = find_image_contract_errors(services)

                self.assertEqual(errors, [f"{service_name}_image_missing"])
                self.assertNotIn("ghcr.io", repr(errors))

    def test_mutable_or_malformed_image_references_fail(self):
        invalid_references = {
            "tag": "ghcr.io/org/image:latest",
            "short_digest": "ghcr.io/org/image@sha256:" + ("a" * 63),
            "nonhex_digest": "ghcr.io/org/image@sha256:" + ("g" * 64),
            "uppercase_digest": "ghcr.io/org/image@sha256:" + ("A" * 64),
            "tag_and_digest": (
                "ghcr.io/org/image:release@sha256:" + ("a" * 64)
            ),
            "digest_only": "sha256:value@sha256:" + ("a" * 64),
            "whitespace": "ghcr.io/org/image @sha256:" + ("a" * 64),
        }
        for case, reference in invalid_references.items():
            with self.subTest(case=case):
                services = _services()
                services["web"]["image"] = reference
                services["maintenance"]["image"] = reference

                self.assertEqual(
                    find_image_contract_errors(services),
                    [
                        "maintenance_image_not_immutable",
                        "web_image_not_immutable",
                    ],
                )

    def test_web_and_maintenance_images_must_match(self):
        services = _services()
        services["maintenance"]["image"] = (
            "ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:"
            + ("b" * 64)
        )

        self.assertEqual(
            find_image_contract_errors(services),
            ["service_images_divergent"],
        )


if __name__ == "__main__":
    unittest.main()
