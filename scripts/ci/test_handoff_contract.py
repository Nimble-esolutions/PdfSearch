import unittest

from scripts.ci import handoff_contract


class HandoffContractTests(unittest.TestCase):
    def test_repository_handoff_has_required_contract(self):
        text = (handoff_contract.ROOT / handoff_contract.HANDOFF_PATH).read_text(
            encoding="utf-8"
        )

        self.assertEqual(handoff_contract.validate_handoff(text), [])

    def test_runtime_and_operational_paths_require_handoff(self):
        for path in (
            "flowdocs/core/views.py",
            "docker-compose.yml",
            "docker-compose.integration.yml",
            ".github/workflows/docker-build.yml",
            "scripts/ops/recover.py",
            "docs/PRODUCTION_OPERATING_RULES.md",
            "docs/design/AI_SAHAKAR_UI_CONTRACT.md",
            "README.md",
            "AGENTS.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(handoff_contract.requires_handoff({path}))

    def test_isolated_non_operational_docs_do_not_require_handoff(self):
        self.assertFalse(
            handoff_contract.requires_handoff(
                {"docs/CLIENT_USER_MANUAL.md", "landing-pages/README.md"}
            )
        )

    def test_handoff_itself_does_not_recursively_require_itself(self):
        self.assertFalse(
            handoff_contract.requires_handoff({handoff_contract.HANDOFF_PATH})
        )

    def test_missing_section_and_invalid_date_fail(self):
        text = "\n".join(handoff_contract.REQUIRED_METADATA[:-1])
        failures = handoff_contract.validate_handoff(text)

        self.assertTrue(any("Update trigger" in failure for failure in failures))
        self.assertTrue(any("YYYY-MM-DD" in failure for failure in failures))
        self.assertTrue(any("Current project handoff" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
