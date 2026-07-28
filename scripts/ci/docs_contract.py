#!/usr/bin/env python3
"""Fail CI on broken active operator-document contracts."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
ACTIVE_RUNBOOKS = {
    DOCS / "EMERGENCY_DATA_RECOVERY.md",
    DOCS / "INDEX_MAINTENANCE_RUNBOOK.md",
}
STALE_ASSERTIONS = (
    "Plan 003",
    "dashboard/operations/s3",
    "metadata-only Vault",
)


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def markdown_files():
    yield from sorted(DOCS.rglob("*.md"))
    for path in (ROOT / "README.md",):
        if path.is_file():
            yield path


def check_links(path: Path, text: str, failures: list[str]) -> None:
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.strip().split("#", 1)[0]
        if (
            not target
            or target.startswith(("http://", "https://", "mailto:", "/"))
        ):
            continue
        candidate = (path.parent / target).resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            fail(f"{path.relative_to(ROOT)}: link escapes repository: {target}", failures)
        elif not candidate.exists():
            fail(f"{path.relative_to(ROOT)}: broken link: {target}", failures)


def fenced_blocks(text: str, language: str):
    pattern = rf"```{language}\s*\n(.*?)```"
    yield from re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)


def check_mermaid(path: Path, text: str, failures: list[str]) -> None:
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
    compose_names = compose_environment_names()
    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        check_links(path, text, failures)
        check_mermaid(path, text, failures)
        check_shell(path, text, failures)

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
