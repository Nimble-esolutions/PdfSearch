import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "docker-build.yml"


def job_block(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"Workflow job is missing: {name}")
    return match.group("body")


class ReleaseWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.validate = job_block("validate")
        self.candidate = job_block("candidate")
        self.premerge = job_block("premerge")
        self.release = job_block("release")

    def test_validation_contract_stays_independent(self):
        self.assertIn("name: Validate source and deployment contract", self.validate)
        self.assertNotRegex(self.validate, r"(?m)^    needs:")
        self.assertNotRegex(self.validate, r"(?m)^    if:")

    def test_candidate_remains_protected_dev_only_and_exports_exact_build_identity(self):
        self.assertNotRegex(self.candidate, r"(?m)^    needs:")
        self.assertNotIn("github.event_name == 'merge_group'", self.candidate)
        self.assertIn("github.event_name == 'push'", self.candidate)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.candidate)
        self.assertEqual(self.candidate.count("github.ref == 'refs/heads/dev'"), 2)
        self.assertIn("inputs.publish == true", self.candidate)
        self.assertIn("packages: write", self.candidate)
        self.assertIn("image_digest: ${{ steps.build.outputs.digest }}", self.candidate)
        self.assertIn("image_revision: ${{ github.sha }}", self.candidate)
        self.assertIn("docker/build-push-action@", self.candidate)
        self.assertIn("provenance: mode=max", self.candidate)
        self.assertIn("sbom: true", self.candidate)
        self.assertIn("Smoke-test published image through actual entrypoint", self.candidate)
        self.assertIn("aquasecurity/trivy-action@", self.candidate)
        self.assertIn("Enforce image size budget", self.candidate)
        self.assertNotIn("docker buildx imagetools create", self.candidate)
        self.assertNotRegex(self.candidate, r'--tag "\$\{IMAGE_NAME\}:(?:dev|latest)"')

    def test_release_waits_for_both_proofs_and_promotes_only_handoff_digest(self):
        self.assertIn("needs: [validate, candidate]", self.release)
        self.assertIn("environment: production", self.release)
        self.assertIn("DIGEST: ${{ needs.candidate.outputs.image_digest }}", self.release)
        self.assertIn("REVISION: ${{ needs.candidate.outputs.image_revision }}", self.release)
        self.assertIn("test \"$REVISION\" = \"$GITHUB_SHA\"", self.release)
        self.assertIn("^sha256:[0-9a-f]{64}$", self.release)
        self.assertIn("docker buildx imagetools inspect \"$IMAGE_REF\"", self.release)

        handoff = self.release.index("Verify certified candidate handoff")
        superseded_guard = self.release.index("Guard against a newer dev commit")
        promotion = self.release.index("docker buildx imagetools create")
        convergence = self.release.index("Both compatibility tags resolve")
        self.assertLess(handoff, superseded_guard)
        self.assertLess(superseded_guard, promotion)
        self.assertLess(promotion, convergence)

    def test_release_does_not_repeat_candidate_certification(self):
        for duplicate in (
            "docker/build-push-action@",
            "run_compose_smoke.sh",
            "aquasecurity/trivy-action@",
            "npm ci",
            "playwright install",
            "Enforce image size budget",
        ):
            with self.subTest(duplicate=duplicate):
                self.assertNotIn(duplicate, self.release)

    def test_only_release_can_move_compatibility_aliases(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(workflow.count("docker buildx imagetools create"), 2)
        self.assertEqual(self.release.count("docker buildx imagetools create"), 2)
        self.assertIn('--tag "${IMAGE_NAME}:dev"', self.release)
        self.assertIn('--tag "${IMAGE_NAME}:latest"', self.release)

    def test_release_remains_dev_only(self):
        self.assertNotIn("github.event_name == 'merge_group'", self.release)
        self.assertIn("github.event_name == 'push'", self.release)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.release)
        self.assertEqual(self.release.count("github.ref == 'refs/heads/dev'"), 2)
        self.assertIn("inputs.publish == true", self.release)

    def test_premerge_rollup_fails_closed_by_event(self):
        self.assertIn("name: Pre-merge certification", self.premerge)
        self.assertIn("always()", self.premerge)
        self.assertIn("needs: [validate, candidate]", self.premerge)
        self.assertEqual(
            self.premerge.count('test "$VALIDATE_RESULT" = "success"'), 2
        )
        self.assertEqual(
            self.premerge.count('test "$CANDIDATE_RESULT" = "skipped"'), 2
        )
        self.assertIn("Phase one preserves full pull-request validation", self.premerge)
        self.assertIn("merge-group SHA passed full source and runtime", self.premerge)


if __name__ == "__main__":
    unittest.main()
