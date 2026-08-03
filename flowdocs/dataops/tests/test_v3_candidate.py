import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from dataops.v3_candidate import V3CandidateError, prepare_recovery_candidate


class CandidatePreparationTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.digest = "a" * 64
        self.restore = {
            "verified": True,
            "manifest_sha256": self.digest,
            "workspace": str(self.workspace),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_requires_verified_restore(self):
        with self.assertRaisesMessage(V3CandidateError, "candidate_restore_unverified"):
            prepare_recovery_candidate({**self.restore, "verified": False})

    def test_returns_exact_successful_receipt(self):
        observed_environment = {}

        def runner(*_args, **kwargs):
            observed_environment.update(kwargs["env"])
            (self.workspace / ".dataops-candidate.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "success": True,
                        "manifest_sha256": self.digest,
                        "indexing_ratio": 1.0,
                    }
                )
            )
            return SimpleNamespace(returncode=0)

        receipt = prepare_recovery_candidate(self.restore, runner=runner)
        self.assertEqual(receipt["manifest_sha256"], self.digest)
        self.assertEqual(receipt["workspace"], str(self.workspace.resolve()))
        self.assertEqual(
            observed_environment["STAGING_RUNTIME_ACTIVATION_ENABLED"],
            "0",
        )
        self.assertEqual(
            observed_environment["STAGING_INITIAL_ACTIVATION_ENABLED"],
            "0",
        )

    def test_subprocess_failure_is_safe_and_retryable(self):
        def runner(*_args, **_kwargs):
            return SimpleNamespace(returncode=1)

        with self.assertRaises(V3CandidateError) as raised:
            prepare_recovery_candidate(self.restore, runner=runner)
        self.assertEqual(raised.exception.code, "candidate_preparation_failed")
        self.assertTrue(raised.exception.retryable)
