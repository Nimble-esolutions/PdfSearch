#!/usr/bin/env python3
"""Fail CI on broken active operator-document contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from html import unescape
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
MERMAID_CLI = ROOT / "node_modules" / ".bin" / "mmdc"
ACTIVE_RUNBOOKS = {
    DOCS / "EMERGENCY_DATA_RECOVERY.md",
    DOCS / "INDEX_MAINTENANCE_RUNBOOK.md",
}
CURRENT_TRUTH_FILES = {
    ROOT / "README.md",
    ROOT / "DEPLOYMENT.md",
    ROOT / "DEPLOYMENT_GUIDE.md",
    DOCS / "HANDOFF.md",
    DOCS / "ARCHITECTURE_OVERVIEW.md",
    DOCS / "AI_SAHAKAR_ADMIN_USER_GUIDE.md",
    DOCS / "AI_SAHAKAR_DEVELOPER_GUIDE.md",
    DOCS / "ENVIRONMENT_CONFIGURATION_GUIDE.md",
    DOCS / "ENVIRONMENT_CONTRACT.md",
    DOCS / "ENVIRONMENT_REFERENCE.md",
    DOCS / "OPERATIONS_RUNBOOK.md",
    DOCS / "RECOVERY_CERTIFICATION.md",
    DOCS / "dataops" / "ENV_CONTRACT.md",
    DOCS / "dataops" / "V3_ARCHITECTURE.md",
    DOCS / "dataops" / "V3_IMPACT_ANALYSIS.md",
}
OBSOLETE_CURRENT_LANGUAGE = (
    "Vault Operations Workbench",
    "Vault & Recovery",
    "Documents & Indexes",
    "active Vault Operations",
    "final immutable PR 176",
    "atomic symlink switch",
    "/app/data-control/active-generation",
    "/app/data-control/previous-generation",
    "/app/data-control/activation-journals",
)
BOUNDARY_TRUTH_FILES = {
    ROOT / "README.md",
    DOCS / "ARCHITECTURE_OVERVIEW.md",
    DOCS / "dataops" / "V3_ARCHITECTURE.md",
}
STALE_ASSERTIONS = (
    "Plan 003",
    "dashboard/operations/s3",
    "metadata-only Vault",
)


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def markdown_files():
    paths = set(DOCS.rglob("*.md"))
    paths.update(ROOT.glob("*.md"))
    landing_pages = ROOT / "landing-pages"
    if landing_pages.is_dir():
        paths.update(landing_pages.rglob("*.md"))
    yield from sorted(path for path in paths if path.is_file())


def check_current_architecture_language(
    path: Path,
    text: str,
    failures: list[str],
) -> None:
    """Reject product-language drift in designated current truth sources."""
    if path not in CURRENT_TRUTH_FILES:
        return
    folded = text.casefold()
    for stale in OBSOLETE_CURRENT_LANGUAGE:
        if stale.casefold() in folded:
            fail(
                f"{path.relative_to(ROOT)}: obsolete current architecture "
                f"language: {stale}",
                failures,
            )
    if path in BOUNDARY_TRUTH_FILES:
        for required in ("DataOps v3", "internal compatibility"):
            if required.casefold() not in folded:
                fail(
                    f"{path.relative_to(ROOT)}: missing current boundary "
                    f"language: {required}",
                    failures,
                )


def heading_anchors(text: str) -> set[str]:
    anchors = set(
        re.findall(
            r"<(?:a|[A-Za-z][A-Za-z0-9-]*)\b[^>]*\bid=[\"']([^\"']+)[\"']",
            text,
            flags=re.IGNORECASE,
        )
    )
    duplicates: dict[str, int] = {}
    for heading in re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", heading)
        heading = re.sub(r"[`*_~]", "", unescape(heading)).casefold()
        slug = "".join(
            character
            for character in heading
            if (
                character.isalnum()
                or unicodedata.category(character).startswith("M")
                or character in {" ", "-", "_"}
            )
        )
        slug = re.sub(r"\s+", "-", slug.strip())
        if not slug:
            continue
        duplicate = duplicates.get(slug, 0)
        anchors.add(slug if duplicate == 0 else f"{slug}-{duplicate}")
        duplicates[slug] = duplicate + 1
    return anchors


def check_links(path: Path, text: str, failures: list[str]) -> None:
    for raw_target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = raw_target.strip()
        if (
            not target
            or target.startswith(("http://", "https://", "mailto:", "/"))
        ):
            continue
        file_target, separator, fragment = target.partition("#")
        file_target = unquote(file_target)
        fragment = unquote(fragment)
        candidate = (
            (path.parent / file_target).resolve()
            if file_target
            else path.resolve()
        )
        root = ROOT.resolve()
        if root not in candidate.parents and candidate != root:
            fail(f"{path.relative_to(ROOT)}: link escapes repository: {target}", failures)
        elif not candidate.exists():
            fail(f"{path.relative_to(ROOT)}: broken link: {target}", failures)
        elif separator and fragment and candidate.suffix.casefold() == ".md":
            candidate_text = candidate.read_text(encoding="utf-8")
            if fragment not in heading_anchors(candidate_text):
                fail(
                    f"{path.relative_to(ROOT)}: broken anchor: {target}",
                    failures,
                )


def fenced_blocks(text: str, language: str):
    pattern = rf"```{language}\s*\n(.*?)```"
    yield from re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)


def resolve_mermaid_browser() -> tuple[str, str]:
    """Resolve the browser used by Mermaid without forcing a GUI build."""
    configured = os.environ.get("PUPPETEER_EXECUTABLE_PATH", "").strip()
    if configured:
        executable = Path(configured).expanduser()
        if not executable.is_file():
            return "", (
                "PUPPETEER_EXECUTABLE_PATH does not reference a browser file: "
                f"{executable}"
            )
        if (
            sys.platform == "darwin"
            and executable.name == "Google Chrome for Testing"
            and any(part.endswith(".app") for part in executable.parts)
        ):
            return "", (
                "refusing the macOS Google Chrome for Testing app for headless "
                "documentation rendering; use the matching chrome-headless-shell"
            )
        return str(executable), ""

    playwright_executable = subprocess.run(
        [
            "node",
            "-e",
            "process.stdout.write(require('playwright').chromium.executablePath())",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if (
        playwright_executable.returncode == 0
        and Path(playwright_executable.stdout).is_file()
    ):
        executable = Path(playwright_executable.stdout)
        if sys.platform != "darwin":
            return str(executable), ""

        revision_directory = next(
            (
                parent
                for parent in executable.parents
                if re.fullmatch(r"chromium-\d+", parent.name)
            ),
            None,
        )
        if revision_directory is not None:
            revision = revision_directory.name.removeprefix("chromium-")
            shell_directory = (
                revision_directory.parent / f"chromium_headless_shell-{revision}"
            )
            shells = sorted(
                path
                for path in shell_directory.rglob("chrome-headless-shell")
                if path.is_file()
            )
            if shells:
                return str(shells[0]), ""
        return "", (
            "Playwright's matching chrome-headless-shell is unavailable on "
            "macOS; run `npx playwright install chromium` and retry"
        )
    return "", ""


def subprocess_failure_detail(result: subprocess.CompletedProcess) -> str:
    """Keep the actionable start and end of a failed compiler diagnostic."""
    lines = [
        line.strip()
        for line in (result.stderr or result.stdout).splitlines()
        if line.strip()
    ]
    if not lines:
        return "no compiler output"
    selected = lines if len(lines) <= 10 else [*lines[:6], "...", *lines[-4:]]
    return " | ".join(selected)[:1200]


def compile_mermaid(
    *, label: str, source: str, failures: list[str], compiler=MERMAID_CLI
) -> None:
    if not compiler.is_file():
        fail(f"{label}: Mermaid compiler unavailable: {compiler}", failures)
        return
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        source_path = directory / "diagram.mmd"
        output_path = directory / "diagram.svg"
        config_path = directory / "puppeteer.json"
        source_path.write_text(source, encoding="utf-8")
        browser_executable, browser_error = resolve_mermaid_browser()
        if browser_error:
            fail(f"{label}: {browser_error}", failures)
            return
        command = [
            str(compiler),
            "--input",
            str(source_path),
            "--output",
            str(output_path),
            "--quiet",
        ]
        if browser_executable:
            config_path.write_text(
                json.dumps({
                    "executablePath": browser_executable,
                    "args": ["--no-sandbox"],
                }),
                encoding="utf-8",
            )
            command.extend(["--puppeteerConfigFile", str(config_path)])
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail(f"{label}: Mermaid compilation timed out", failures)
            return
        if result.returncode or not output_path.is_file():
            fail(
                f"{label}: Mermaid compilation failed: "
                f"{subprocess_failure_detail(result)}",
                failures,
            )


def check_mermaid(
    path: Path,
    text: str,
    failures: list[str],
    sources: list[tuple[str, str]],
) -> None:
    for index, block in enumerate(fenced_blocks(text, "mermaid"), 1):
        if not re.search(
            r"^\s*(flowchart|graph|sequenceDiagram|stateDiagram|classDiagram|erDiagram)",
            block,
            flags=re.MULTILINE,
        ):
            fail(
                f"{path.relative_to(ROOT)}: Mermaid block {index} has no diagram declaration",
                failures,
            )
            continue
        sources.append(
            (
                f"{path.relative_to(ROOT)}: Mermaid block {index}",
                block,
            )
        )


def compile_mermaid_batch(
    sources: list[tuple[str, str]],
    failures: list[str],
    *,
    compiler=MERMAID_CLI,
    timeout=60,
) -> None:
    if not sources:
        return
    if not compiler.is_file():
        fail(f"Mermaid compiler unavailable: {compiler}", failures)
        return
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        source_path = directory / "diagrams.md"
        output_path = directory / "compiled.md"
        config_path = directory / "puppeteer.json"
        source_path.write_text(
            "\n\n".join(
                (
                    f"## Diagram {index}: {label}\n\n"
                    f"```mermaid\n{source.rstrip()}\n```"
                )
                for index, (label, source) in enumerate(sources, 1)
            )
            + "\n",
            encoding="utf-8",
        )
        browser_executable, browser_error = resolve_mermaid_browser()
        if browser_error:
            fail(f"Mermaid batch compilation failed: {browser_error}", failures)
            return
        command = [
            str(compiler),
            "--input",
            str(source_path),
            "--output",
            str(output_path),
            "--quiet",
        ]
        if browser_executable:
            config_path.write_text(
                json.dumps({
                    "executablePath": browser_executable,
                    "args": ["--no-sandbox"],
                }),
                encoding="utf-8",
            )
            command.extend(["--puppeteerConfigFile", str(config_path)])
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail(
                f"Mermaid batch compilation timed out after {timeout}s",
                failures,
            )
            return
        rendered = list(directory.glob("compiled-*.svg"))
        if (
            result.returncode
            or not output_path.is_file()
            or len(rendered) != len(sources)
        ):
            fail(
                "Mermaid batch compilation failed "
                f"({len(rendered)}/{len(sources)} diagrams rendered): "
                f"{subprocess_failure_detail(result)}",
                failures,
            )


def check_shell(path: Path, text: str, failures: list[str]) -> None:
    blocks = list(fenced_blocks(text, "sh")) + list(fenced_blocks(text, "bash"))
    for index, block in enumerate(blocks, 1):
        sanitized = re.sub(r"<[A-Za-z0-9._/-]+>", "placeholder", block)
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as handle:
            handle.write(sanitized)
            handle.flush()
            result = subprocess.run(
                ["bash", "-n", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode:
            fail(
                f"{path.relative_to(ROOT)}: shell block {index}: "
                f"{result.stderr.strip()}",
                failures,
            )


def compose_environment_names() -> set[str]:
    names = set()
    for path in ROOT.glob("docker-compose*.yml"):
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"^\s{6}([A-Z][A-Z0-9_]+):", text, re.MULTILINE))
    return names


def main() -> int:
    failures = []
    mermaid_sources = []
    compose_names = compose_environment_names()
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        check_links(path, text, failures)
        check_mermaid(path, text, failures, mermaid_sources)
        check_shell(path, text, failures)
        check_current_architecture_language(path, text, failures)
    for path in sorted(DOCS.rglob("*.mmd")):
        source = path.read_text(encoding="utf-8")
        for stale in OBSOLETE_CURRENT_LANGUAGE:
            if stale.casefold() in source.casefold():
                fail(
                    f"{path.relative_to(ROOT)}: obsolete diagram language: "
                    f"{stale}",
                    failures,
                )
        if not re.search(
            r"^\s*(flowchart|graph|sequenceDiagram|stateDiagram|classDiagram|erDiagram)",
            source,
            flags=re.MULTILINE,
        ):
            fail(
                f"{path.relative_to(ROOT)}: no Mermaid diagram declaration",
                failures,
            )
            continue
        mermaid_sources.append(
            (str(path.relative_to(ROOT)), source)
        )
    compile_mermaid_batch(mermaid_sources, failures)

    for path in ACTIVE_RUNBOOKS:
        if not path.is_file():
            fail(f"missing active runbook: {path.relative_to(ROOT)}", failures)
            continue
        text = path.read_text(encoding="utf-8")
        for field in ("Status: Active", "Owner:", "Last reviewed:"):
            if field not in text:
                fail(f"{path.relative_to(ROOT)}: missing {field}", failures)
        for stale in STALE_ASSERTIONS:
            if stale in text:
                fail(f"{path.relative_to(ROOT)}: stale contract: {stale}", failures)
        documented = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", text))
        compose_like = {
            name for name in documented
            if name.endswith(("_ENABLED", "_ROOT", "_PATH", "_DIR"))
        }
        unknown = compose_like - compose_names
        if unknown:
            fail(
                f"{path.relative_to(ROOT)}: unknown Compose environment name(s): "
                f"{', '.join(sorted(unknown))}",
                failures,
            )

    if failures:
        print("\n".join(f"ERROR: {item}" for item in failures), file=sys.stderr)
        return 1
    print("Documentation contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
