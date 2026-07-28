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
        self.assertIsNotNone(
            validate_operator_language.RAW_STATE.search(
                "{{ job.options.candidate_state }}"
            )
        )
        self.assertIsNone(
            validate_operator_language.RAW_STATE.search("{{ job.status_label }}")
        )
        self.assertIsNotNone(
            validate_operator_language.RAW_ERROR_SUMMARY.search(
                "{{ job.error_summary }}"
            )
        )

    def test_approved_component_is_the_only_evidence_boundary(self):
        component = validate_operator_language.APPROVED_COMPONENT.read_text(
            encoding="utf-8"
        )
        self.assertIn("Technical details", component)
        self.assertIn('lang="en"', component)
        self.assertIn('dir="ltr"', component)

    def test_fixture_rejects_direct_fields_states_and_error_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / "templates"
            app = root / "app"
            templates.mkdir()
            app.mkdir()
            (templates / "bad.html").write_text(
                "\n".join(
                    (
                        "{{ item.reason_code }}",
                        "{{ job.status }}",
                        '{% include "components/operator_evidence.html" '
                        "with technical_code=job.error_summary %}",
                    )
                ),
                encoding="utf-8",
            )
            (app / "views.py").write_text(
                'message = reason.replace("_", " ")\n', encoding="utf-8"
            )

            errors = validate_operator_language.violations(
                template_root=templates,
                app_roots=(app,),
                approved_component=templates / "components/operator_evidence.html",
                display_root=root,
            )

        self.assertEqual(len(errors), 4)
        self.assertTrue(any("machine evidence" in error for error in errors))
        self.assertTrue(any("state-machine" in error for error in errors))
        self.assertTrue(any("raw error_summary" in error for error in errors))
        self.assertTrue(any("underscore replacement" in error for error in errors))

    def test_fixture_accepts_approved_component_and_authored_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / "templates"
            component = templates / "components" / "operator_evidence.html"
            component.parent.mkdir(parents=True)
            component.write_text(
                '<code lang="en" dir="ltr">{{ technical_code }}</code>\n',
                encoding="utf-8",
            )
            (templates / "good.html").write_text(
                "\n".join(
                    (
                        "{{ job.status_label }}",
                        '{% include "components/operator_evidence.html" '
                        "with technical_code=job.safe_code %}",
                    )
                ),
                encoding="utf-8",
            )

            errors = validate_operator_language.violations(
                template_root=templates,
                app_roots=(),
                approved_component=component,
                display_root=root,
            )

        self.assertEqual(errors, [])
