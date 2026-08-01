import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FAST_WORKFLOW = ROOT / ".github" / "workflows" / "pr-contract.yml"
FULL_WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"


class FastPullRequestWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fast = FAST_WORKFLOW.read_text(encoding="utf-8")
        self.full = FULL_WORKFLOW.read_text(encoding="utf-8")

    def test_fast_check_is_stable_bounded_and_read_only(self):
        self.assertIn("name: PR contract", self.fast)
        self.assertIn("timeout-minutes: 2", self.fast)
        self.assertIn("permissions:\n  contents: read", self.fast)
        self.assertIn("pull_request:", self.fast)
        self.assertIn("merge_group:", self.fast)
        self.assertNotIn("paths:", self.fast)

    def test_fast_check_does_not_install_or_start_heavy_dependencies(self):
        forbidden = (
            "npm ci",
            "playwright install",
            "pip install",
            "go install",
            "docker build --tag",
            "docker compose up",
            "docker pull",
            "run_compose_smoke.sh",
            "run_maintenance_lifecycle_e2e.sh",
            "run_vault_integration.sh",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.fast)

    def test_fast_check_covers_static_and_compose_contracts(self):
        required = (
            'git diff --check "${base_sha}...HEAD"',
            "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
            "MERGE_GROUP_BASE_SHA: ${{ github.event.merge_group.base_sha }}",
            "fetch-depth: 0",
            "bash -n",
            "python3 -m compileall",
            "test_release_workflow_contract",
            "test_pr_contract_workflow",
            "test_docs_contract",
            "test_operator_language",
            "test_recovery_certification_contract",
            "test_compose_env_parity",
            "validate_operator_language.py",
            "docker build --check",
            "docker-compose.yml config --quiet",
            "docker-compose.ci.yml config --quiet",
            "docker-compose.integration.yml config --quiet",
            "docker-compose.maintenance-e2e.yml config --quiet",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.fast)

    def test_build_trust_boundary_has_code_owners(self):
        codeowners = CODEOWNERS.read_text(encoding="utf-8")
        for protected_path in (
            "/.github/workflows/",
            "/Dockerfile",
            "/docker-entrypoint.sh",
            "/requirements-web.lock",
            "/scripts/ci/",
        ):
            with self.subTest(protected_path=protected_path):
                self.assertRegex(
                    codeowners,
                    rf"(?m)^{re.escape(protected_path)}\s+@yashodhank$",
                )

    def test_full_workflow_materializes_for_every_pr_and_merge_group(self):
        pull_request_block = re.search(
            r"(?ms)^  pull_request:\n(?P<body>.*?)(?=^  [a-z_]+:\n)", self.full
        )
        self.assertIsNotNone(pull_request_block)
        self.assertNotIn("paths:", pull_request_block.group("body"))
        self.assertRegex(
            self.full,
            r"(?ms)^  merge_group:\n    types: \[checks_requested\]\n"
            r"    branches: \[dev\]",
        )


if __name__ == "__main__":
    unittest.main()
