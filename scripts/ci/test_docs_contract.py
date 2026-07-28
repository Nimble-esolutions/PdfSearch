import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import docs_contract


class DocumentationContractTests(unittest.TestCase):
    def test_heading_anchors_match_duplicates_unicode_and_explicit_ids(self):
        anchors = docs_contract.heading_anchors(
            "# Restore & Activation\n"
            "# Restore & Activation\n"
            "## मराठी मार्गदर्शक\n"
            '<a id="operator-note"></a>\n'
        )

        self.assertIn("restore-activation", anchors)
        self.assertIn("restore-activation-1", anchors)
        self.assertIn("मराठी-मार्गदर्शक", anchors)
        self.assertIn("operator-note", anchors)

    def test_links_validate_same_file_cross_file_and_encoded_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            target = root / "target.md"
            source.write_text(
                "# Source\n"
                "[same](#source)\n"
                "[cross](target.md#target-heading)\n"
                "[encoded](target.md#मराठी-%E0%A4%AE%E0%A4%BE%E0%A4%B0%E0%A5%8D%E0%A4%97)\n",
                encoding="utf-8",
            )
            target.write_text(
                "# Target heading\n## मराठी मार्ग\n",
                encoding="utf-8",
            )
            failures = []

            with patch.object(docs_contract, "ROOT", root):
                docs_contract.check_links(
                    source,
                    source.read_text(encoding="utf-8"),
                    failures,
                )

        self.assertEqual(failures, [])

    def test_links_reject_missing_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            target = root / "target.md"
            source.write_text("[missing](target.md#absent)\n", encoding="utf-8")
            target.write_text("# Present\n", encoding="utf-8")
            failures = []

            with patch.object(docs_contract, "ROOT", root):
                docs_contract.check_links(
                    source,
                    source.read_text(encoding="utf-8"),
                    failures,
                )

        self.assertEqual(
            failures,
            ["source.md: broken anchor: target.md#absent"],
        )

    def test_mermaid_compile_fails_closed_when_compiler_is_missing(self):
        failures = []

        docs_contract.compile_mermaid(
            label="example",
            source="flowchart LR\nA --> B\n",
            failures=failures,
            compiler=Path("/definitely/missing/mmdc"),
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("compiler unavailable", failures[0])

    def test_mermaid_compile_timeout_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = Path(directory) / "mmdc"
            compiler.write_text("", encoding="utf-8")
            failures = []
            no_browser = subprocess.CompletedProcess([], 1, "", "")

            with patch(
                "scripts.ci.docs_contract.subprocess.run",
                side_effect=[
                    no_browser,
                    subprocess.TimeoutExpired(["mmdc"], 30),
                ],
            ):
                docs_contract.compile_mermaid(
                    label="example",
                    source="flowchart LR\nA --> B\n",
                    failures=failures,
                    compiler=compiler,
                )

        self.assertEqual(failures, ["example: Mermaid compilation timed out"])


if __name__ == "__main__":
    unittest.main()
