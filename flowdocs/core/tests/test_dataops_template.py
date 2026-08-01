from pathlib import Path
import unittest


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"


class DataOperationsTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = (TEMPLATE_ROOT / "dataops" / "workbench.html").read_text(
            encoding="utf-8"
        )
        self.stylesheet = (
            STATIC_ROOT / "main" / "css" / "data-operations.css"
        ).read_text(encoding="utf-8")

    def test_workbench_is_server_rendered_and_has_primary_tasks(self):
        for marker in (
            "Current condition",
            "Refresh data",
            "Back up data",
            "Restore data",
            "Storage and automation",
            "History and technical evidence",
            "csrf_token",
        ):
            self.assertIn(marker, self.template)

        # The replacement control plane must not bring the retired product
        # name back into operator-facing copy.
        self.assertNotIn("Vault", self.template)
        self.assertNotIn("vaultops", self.template)

    def test_workbench_uses_accessible_forms_and_no_script_dependency(self):
        self.assertIn('<form class="dataops-form" method="post"', self.template)
        self.assertIn('aria-live="polite"', self.template)
        self.assertIn('aria-labelledby="dataops-status-heading"', self.template)
        self.assertNotIn("<script", self.template)

    def test_styles_preserve_workbench_tokens_and_mobile_layout(self):
        for token in ("--vault-maroon", "--vault-ochre", "--vault-canvas"):
            self.assertIn(token, self.stylesheet)
        self.assertIn(".dataops-status", self.stylesheet)
        self.assertIn("@media (max-width: 560px)", self.stylesheet)
