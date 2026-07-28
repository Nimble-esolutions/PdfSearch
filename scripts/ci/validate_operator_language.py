#!/usr/bin/env python3
"""Reject machine evidence rendered outside the approved UI boundary."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "flowdocs" / "core" / "templates"
APP_ROOTS = (ROOT / "flowdocs" / "core", ROOT / "flowdocs" / "vaultops")
APPROVED_COMPONENT = TEMPLATE_ROOT / "components" / "operator_evidence.html"

FORBIDDEN_FIELDS = (
    "reason_code",
    "safe_error_code",
    "error_summary",
    "blocking_reasons",
    "protection_reasons",
)
RAW_STATE = re.compile(
    r"{{\s*[^}]+\.(?:[a-zA-Z0-9]+_)?"
    r"(?:state|status|kind|operation|phase|action|result)(?!_label)\b"
    r"\s*(?:\|[^}]*)?}}"
)
UNDERSCORE_FORMATTING = re.compile(
    r"\.replace\(\s*['\"]_['\"]\s*,\s*['\"]\s+['\"]\s*\)"
)
RAW_ERROR_SUMMARY = re.compile(
    r"(?:{{[^}]*\berror_summary\b[^}]*}}|"
    r"{%\s*include\b[^%]*\btechnical_code\s*=\s*[^%\s]*error_summary\b[^%]*%})"
)


def violations(
    *,
    template_root: Path = TEMPLATE_ROOT,
    app_roots: tuple[Path, ...] = APP_ROOTS,
    approved_component: Path = APPROVED_COMPONENT,
    display_root: Path = ROOT,
) -> list[str]:
    errors: list[str] = []
    for path in template_root.rglob("*.html"):
        if path == approved_component:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RAW_ERROR_SUMMARY.search(line):
                errors.append(
                    f"{path.relative_to(display_root)}:{number}: "
                    "raw error_summary is not approved technical evidence"
                )
                continue
            if any(
                re.search(r"{{[^}]*\b" + field + r"\b[^}]*}}", line)
                for field in FORBIDDEN_FIELDS
            ):
                if '{% include "components/operator_evidence.html"' not in line:
                    errors.append(
                        f"{path.relative_to(display_root)}:{number}: "
                        "machine evidence must use operator_evidence.html"
                    )
            if RAW_STATE.search(line):
                # Stable values are allowed only in hidden form fields or CSS hooks.
                if 'type="hidden"' not in line and 'class="status status--' not in line:
                    errors.append(
                        f"{path.relative_to(display_root)}:{number}: "
                        "raw state-machine value requires an authored *_label"
                    )

    for root in app_roots:
        for path in root.rglob("*.py"):
            if "management/commands" in path.as_posix():
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if UNDERSCORE_FORMATTING.search(line):
                    errors.append(
                        f"{path.relative_to(display_root)}:{number}: "
                        "underscore replacement is not approved user-facing copy"
                    )
    return errors


def main() -> int:
    errors = violations()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Operator-language boundary is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
