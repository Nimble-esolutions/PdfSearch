from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import unittest

from scripts.ci.assert_compose_env_parity import (
    CRITICAL_KEYS,
    find_image_contract_errors,
    find_parity_errors,
    find_contract_errors,
    find_local_build_errors,
    find_release_image_errors,
    find_rustfs_errors,
    find_topology_errors,
)


def _services():
    environment = {key: f"value-for-{key}" for key in CRITICAL_KEYS}
    image = (
        "ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:"
        + ("a" * 64)
    )
    base = {
        "image": image,
        "pull_policy": "always",
        "healthcheck": {"test": ["CMD", "true"]},
        "networks": {"internal": None},
        "volumes": ["data:/app/data", "control:/app/data-control"],
    }
    return {
        "web": {
            **base,
            "environment": dict(environment),
            "depends_on": {"redis": {"condition": "service_healthy"}},
        },
        "maintenance": {
            **base,
            "environment": dict(environment),
            "depends_on": {"redis": {"condition": "service_healthy"}},
        },
        "redis": {"healthcheck": {"test": ["CMD", "true"]}},
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

    def test_release_policy_rejects_build_pull_and_digest_drift(self):
        services = _services()
        services["web"]["build"] = {"context": "."}
        services["maintenance"]["pull_policy"] = "missing"
        services["web"]["environment"]["APP_IMAGE_DIGEST"] = "sentinel-secret-image"
        services["maintenance"]["environment"]["APP_IMAGE_DIGEST"] = services["maintenance"]["image"]
        errors = find_release_image_errors(services)
        self.assertIn("web_build_forbidden", errors)
        self.assertIn("maintenance_pull_policy_not_always", errors)
        self.assertIn("web_image_digest_mismatch", errors)
        self.assertNotIn("sentinel-secret-image", repr(errors))

    def test_development_requires_equal_local_image_and_build(self):
        services = _services()
        for name in ("web", "maintenance"):
            services[name]["image"] = "pdfsearch-dev:local"
            services[name]["build"] = {"context": ".", "dockerfile": "Dockerfile"}
            services[name]["environment"]["APP_IMAGE_DIGEST"] = ""
        self.assertEqual(find_local_build_errors(services), [])
        services["maintenance"]["build"] = {"context": "different"}
        self.assertEqual(find_local_build_errors(services), ["service_builds_divergent"])

    def test_topology_requirements_are_reason_codes_only(self):
        services = _services()
        del services["redis"]
        services["web"]["volumes"] = ["secret-source:/app/data"]
        services["maintenance"]["networks"] = {}
        errors = find_topology_errors(services)
        self.assertIn("redis_service_missing", errors)
        self.assertIn("web_data-control_mount_missing", errors)
        self.assertIn("maintenance_internal_network_missing", errors)
        self.assertNotIn("secret-source", repr(errors))

    def test_development_requires_rustfs_initializer_barrier(self):
        services = _services()
        self.assertEqual(
            find_rustfs_errors(services),
            [
                "maintenance_rustfs_init_dependency_missing",
                "rustfs_init_service_missing",
                "rustfs_service_missing",
                "web_rustfs_init_dependency_missing",
            ],
        )
        services["rustfs"] = {}
        services["rustfs-init"] = {
            "restart": "no",
            "depends_on": {"rustfs": {"condition": "service_started"}},
        }
        for name in ("web", "maintenance"):
            services[name]["depends_on"]["rustfs-init"] = {
                "condition": "service_completed_successfully"
            }
        self.assertEqual(find_rustfs_errors(services), [])

    def test_staging_and_production_share_release_policy(self):
        services = _services()
        for name in ("web", "maintenance"):
            services[name]["environment"]["APP_IMAGE_DIGEST"] = services[name]["image"]
        self.assertEqual(find_contract_errors(services, "staging"), [])
        self.assertEqual(find_contract_errors(services, "production"), [])


class DevelopmentCredentialWiringTests(unittest.TestCase):
    compose_file = Path(__file__).resolve().parents[2] / "docker-compose.dev.yml"

    def _render(self, extra_environment):
        environment = os.environ.copy()
        environment.update(extra_environment)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode:
            self.fail("development_compose_render_failed")
        return json.loads(result.stdout)["services"]

    def test_custom_rustfs_credentials_flow_to_every_local_consumer(self):
        access = "sentinel-custom-access"
        secret = "sentinel-custom-secret"
        services = self._render(
            {
                "DEV_RUSTFS_ACCESS_KEY": access,
                "DEV_RUSTFS_SECRET_KEY": secret,
                "ARTIFACT_VAULT_ACCESS_KEY": "",
                "ARTIFACT_VAULT_SECRET_KEY": "",
            }
        )
        for service_name in ("web", "maintenance"):
            environment = services[service_name]["environment"]
            self.assertTrue(environment["ARTIFACT_VAULT_ACCESS_KEY"] == access)
            self.assertTrue(environment["ARTIFACT_VAULT_SECRET_KEY"] == secret)
            self.assertTrue(environment["DATAOPS_LOCAL_RUSTFS_ACCESS_KEY"] == access)
            self.assertTrue(environment["DATAOPS_LOCAL_RUSTFS_SECRET_KEY"] == secret)
        self.assertTrue(services["rustfs"]["environment"]["RUSTFS_ACCESS_KEY"] == access)
        self.assertTrue(services["rustfs"]["environment"]["RUSTFS_SECRET_KEY"] == secret)

    def test_artifact_vault_compatibility_override_remains_explicit(self):
        services = self._render(
            {
                "DEV_RUSTFS_ACCESS_KEY": "sentinel-rustfs-access",
                "DEV_RUSTFS_SECRET_KEY": "sentinel-rustfs-secret",
                "ARTIFACT_VAULT_ACCESS_KEY": "sentinel-compat-access",
                "ARTIFACT_VAULT_SECRET_KEY": "sentinel-compat-secret",
            }
        )
        for service_name in ("web", "maintenance"):
            environment = services[service_name]["environment"]
            self.assertTrue(environment["ARTIFACT_VAULT_ACCESS_KEY"] == "sentinel-compat-access")
            self.assertTrue(environment["ARTIFACT_VAULT_SECRET_KEY"] == "sentinel-compat-secret")
            self.assertTrue(environment["DATAOPS_LOCAL_RUSTFS_ACCESS_KEY"] == "sentinel-rustfs-access")
            self.assertTrue(environment["DATAOPS_LOCAL_RUSTFS_SECRET_KEY"] == "sentinel-rustfs-secret")

if __name__ == "__main__":
    unittest.main()
