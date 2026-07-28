import tempfile
import unittest
from pathlib import Path

from scripts.ci import validate_operator_language


class OperatorLanguageValidatorTests(unittest.TestCase):
    def test_repository_templates_pass(self):
        self.assertEqual(validate_operator_language.violations(), [])

    def test_patterns_reject_machine_formatting(self):
        self.assertIsNotNone(
            validate_operator_language.UNDERSCORE_FORMATTING.search(
                'messages.error(request, reason.replace("_", " "))'
            )
        )
        self.assertIsNotNone(
            validate_operator_language.RAW_STATE.search("{{ job.status }}")
        )

    def test_approved_component_is_the_only_evidence_boundary(self):
        component = validate_operator_language.APPROVED_COMPONENT.read_text(
            encoding="utf-8"
        )
        self.assertIn("Technical details", component)
        self.assertIn('lang="en"', component)
        self.assertIn('dir="ltr"', component)
