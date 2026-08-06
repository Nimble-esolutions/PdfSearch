import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import docs_contract


class DocumentationContractTests(unittest.TestCase):
    def test_current_architecture_language_rejects_retired_operator_surface(self):
        failures = []

        docs_contract.check_current_architecture_language(
            docs_contract.DOCS / "ARCHITECTURE_OVERVIEW.md",
            "DataOps v3 uses internal compatibility. Open Vault & Recovery.",
            failures,
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("obsolete current architecture language", failures[0])

    def test_current_architecture_language_requires_boundary_truth(self):
        failures = []

        docs_contract.check_current_architecture_language(
            docs_contract.ROOT / "README.md",
            "DataOps v3 is the operator product.",
            failures,
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("internal compatibility", failures[0])

    def test_historical_document_is_not_rewritten_as_current(self):
        failures = []

        docs_contract.check_current_architecture_language(
            docs_contract.DOCS / "STATUS-2026-08-02.md",
            "Vault & Recovery was used in this dated record.",
            failures,
        )

        self.assertEqual(failures, [])

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

    def test_mermaid_compile_honors_explicit_browser_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compiler = root / "mmdc"
            compiler.write_text("", encoding="utf-8")
            browser = root / "chrome-headless-shell"
            browser.write_text("", encoding="utf-8")
            failures = []
            commands = []

            def run_compiler(command, **_kwargs):
                commands.append(command)
                config_path = Path(
                    command[command.index("--puppeteerConfigFile") + 1]
                )
                config = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(config["executablePath"], str(browser))
                output_path = Path(command[command.index("--output") + 1])
                output_path.write_text("<svg></svg>", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch.dict(
                    os.environ,
                    {"PUPPETEER_EXECUTABLE_PATH": str(browser)},
                ),
                patch(
                    "scripts.ci.docs_contract.subprocess.run",
                    side_effect=run_compiler,
                ),
            ):
                docs_contract.compile_mermaid(
                    label="example",
                    source="flowchart LR\nA --> B\n",
                    failures=failures,
                    compiler=compiler,
                )

        self.assertEqual(failures, [])
        self.assertEqual(len(commands), 1)

    def test_mermaid_compile_rejects_invalid_explicit_browser_path(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = Path(directory) / "mmdc"
            compiler.write_text("", encoding="utf-8")
            failures = []

            with (
                patch.dict(
                    os.environ,
                    {"PUPPETEER_EXECUTABLE_PATH": "/definitely/missing/browser"},
                ),
                patch("scripts.ci.docs_contract.subprocess.run") as run,
            ):
                docs_contract.compile_mermaid(
                    label="example",
                    source="flowchart LR\nA --> B\n",
                    failures=failures,
                    compiler=compiler,
                )

        self.assertEqual(len(failures), 1)
        self.assertIn("does not reference a browser file", failures[0])
        run.assert_not_called()

    def test_mermaid_browser_selects_matching_macos_headless_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            browser = (
                cache
                / "chromium-1228"
                / "chrome-mac-arm64"
                / "Google Chrome for Testing.app"
                / "Contents"
                / "MacOS"
                / "Google Chrome for Testing"
            )
            browser.parent.mkdir(parents=True)
            browser.write_text("", encoding="utf-8")
            shell = (
                cache
                / "chromium_headless_shell-1228"
                / "chrome-headless-shell-mac-arm64"
                / "chrome-headless-shell"
            )
            shell.parent.mkdir(parents=True)
            shell.write_text("", encoding="utf-8")
            playwright = subprocess.CompletedProcess([], 0, str(browser), "")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("scripts.ci.docs_contract.sys.platform", "darwin"),
                patch(
                    "scripts.ci.docs_contract.subprocess.run",
                    return_value=playwright,
                ),
            ):
                executable, error = docs_contract.resolve_mermaid_browser()

        self.assertEqual(executable, str(shell))
        self.assertEqual(error, "")

    def test_mermaid_browser_refuses_explicit_macos_gui_app(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = (
                Path(directory)
                / "Google Chrome for Testing.app"
                / "Contents"
                / "MacOS"
                / "Google Chrome for Testing"
            )
            browser.parent.mkdir(parents=True)
            browser.write_text("", encoding="utf-8")

            with (
                patch.dict(
                    os.environ,
                    {"PUPPETEER_EXECUTABLE_PATH": str(browser)},
                ),
                patch("scripts.ci.docs_contract.sys.platform", "darwin"),
            ):
                executable, error = docs_contract.resolve_mermaid_browser()

        self.assertEqual(executable, "")
        self.assertIn("refusing the macOS Google Chrome for Testing app", error)

    def test_mermaid_browser_preserves_playwright_executable_on_linux(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = Path(directory) / "chrome"
            browser.write_text("", encoding="utf-8")
            playwright = subprocess.CompletedProcess([], 0, str(browser), "")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("scripts.ci.docs_contract.sys.platform", "linux"),
                patch(
                    "scripts.ci.docs_contract.subprocess.run",
                    return_value=playwright,
                ),
            ):
                executable, error = docs_contract.resolve_mermaid_browser()

        self.assertEqual(executable, str(browser))
        self.assertEqual(error, "")

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

    def test_mermaid_batch_uses_one_compiler_process_for_all_diagrams(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = Path(directory) / "mmdc"
            compiler.write_text(
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = \"--output\" ]; then\n"
                "    shift\n"
                "    output=\"$1\"\n"
                "  fi\n"
                "  shift\n"
                "done\n"
                "touch \"$output\"\n"
                "touch \"$(dirname \"$output\")/compiled-1.svg\"\n"
                "touch \"$(dirname \"$output\")/compiled-2.svg\"\n",
                encoding="utf-8",
            )
            os.chmod(compiler, 0o700)
            failures = []

            docs_contract.compile_mermaid_batch(
                [
                    ("first", "flowchart LR\nA --> B\n"),
                    ("second", "sequenceDiagram\nA->>B: hello\n"),
                ],
                failures,
                compiler=compiler,
            )

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
