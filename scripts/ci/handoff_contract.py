#!/usr/bin/env python3
"""Validate the living handoff and require it for operational changes."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HANDOFF_PATH = "docs/HANDOFF.md"
REQUIRED_METADATA = (
    "Status: Active, living handoff",
    "Audience:",
    "Owner:",
    "Last verified:",
    "Canonical source: docs/HANDOFF.md",
    "Update trigger:",
)
REQUIRED_HEADINGS = (
    "# Current project handoff",
    "## Read this first",
    "## Non-negotiable boundary",
    "## Current verified state",
    "## Data and recovery lineage",
    "## Runtime and policy posture",
    "## Recently completed work",
    "## Open decisions and next actions",
    "## Known traps that must not recur",
    "## Resume checks",
    "## Handoff update contract",
)
OPERATIONAL_PREFIXES = (
    ".kilo/",
    ".opencode/",
    ".github/workflows/",
    "browser_tests/",
    "docs/dataops/",
    "docs/design/",
    "docs/environments/",
    "docs/incidents/",
    "flowdocs/",
    "integration_tests/",
    "scripts/ci/",
    "scripts/ops/",
    "scripts/runtime/",
    "skills/",
)
OPERATIONAL_FILES = {
    ".env.example",
    ".github/pull_request_template.md",
    ".github/CODEOWNERS",
    "AGENTS.md",
    "DEPLOYMENT.md",
    "DEPLOYMENT_GUIDE.md",
    "Dockerfile",
    "README.md",
    "docker-entrypoint.sh",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "requirements-web.lock",
    "requirements-web.txt",
    "package-lock.json",
    "package.json",
    "start.sh",
    "worker-entrypoint.sh",
}
OPERATIONAL_DOC_PREFIXES = (
    "docs/AGENT_",
    "docs/ARCHITECTURE_",
    "docs/BUILD_",
    "docs/CODEX_",
    "docs/DATA_",
    "docs/DOKPLOY_",
    "docs/EMERGENCY_",
    "docs/ENVIRONMENT_",
    "docs/INDEX_MAINTENANCE_",
    "docs/INTERNAL_VAULT_",
    "docs/LEGACY_",
    "docs/OPERATIONS_",
    "docs/PERSISTENT_",
    "docs/PRODUCTION_",
    "docs/RECOVERY_",
    "docs/RUSTFS_",
    "docs/SECURITY_",
    "docs/STAGING_",
    "docs/VAULT_",
)


def validate_handoff(text: str) -> list[str]:
    failures: list[str] = []
    for field in REQUIRED_METADATA:
        if field not in text:
            failures.append(f"missing metadata field: {field}")
    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            failures.append(f"missing required heading: {heading}")
    verified = re.search(r"(?m)^Last verified: (\d{4}-\d{2}-\d{2})$", text)
    if not verified:
        failures.append("Last verified must use YYYY-MM-DD")
    if "docs/STATUS-2026-08-03.md for current operational state" not in text:
        failures.append("handoff must explicitly supersede the last dated status")
    return failures


def is_operational_path(path: str) -> bool:
    if path == HANDOFF_PATH:
        return False
    if path in OPERATIONAL_FILES:
        return True
    if path.startswith("docker-compose") and path.endswith((".yml", ".yaml")):
        return True
    if path.startswith("requirements") and path.endswith((".txt", ".lock")):
        return True
    return path.startswith(OPERATIONAL_PREFIXES + OPERATIONAL_DOC_PREFIXES)


def requires_handoff(changed_paths: set[str]) -> bool:
    return any(is_operational_path(path) for path in changed_paths)


def changed_paths(base: str, head: str = "HEAD") -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "git diff failed"
        raise RuntimeError(detail)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="Base commit used to enforce handoff updates")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    handoff = ROOT / HANDOFF_PATH
    failures: list[str] = []
    if not handoff.is_file():
        failures.append(f"missing canonical handoff: {HANDOFF_PATH}")
    else:
        failures.extend(validate_handoff(handoff.read_text(encoding="utf-8")))

    if args.base:
        try:
            paths = changed_paths(args.base, args.head)
        except RuntimeError as exc:
            failures.append(f"cannot inspect changed paths: {exc}")
        else:
            if requires_handoff(paths) and HANDOFF_PATH not in paths:
                operational = sorted(path for path in paths if is_operational_path(path))
                failures.append(
                    "operational change requires docs/HANDOFF.md in the same PR: "
                    + ", ".join(operational)
                )

    if failures:
        print("\n".join(f"ERROR: {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("Living handoff contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
