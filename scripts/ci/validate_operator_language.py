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
    r"{{\s*[^}]+\.(?:state|status|kind|operation|phase|action|result)\s*(?:\|[^}]*)?}}"
)
UNDERSCORE_FORMATTING = re.compile(
    r"\.replace\(\s*['\"]_['\"]\s*,\s*['\"]\s+['\"]\s*\)"
)


def violations() -> list[str]:
    errors: list[str] = []
    for path in TEMPLATE_ROOT.rglob("*.html"):
        if path == APPROVED_COMPONENT:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(
                re.search(r"{{[^}]*\b" + field + r"\b[^}]*}}", line)
                for field in FORBIDDEN_FIELDS
            ):
                if '{% include "components/operator_evidence.html"' not in line:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{number}: "
                        "machine evidence must use operator_evidence.html"
                    )
            if RAW_STATE.search(line):
                # Stable values are allowed only in hidden form fields or CSS hooks.
                if 'type="hidden"' not in line and 'class="status status--' not in line:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{number}: "
                        "raw state-machine value requires an authored *_label"
                    )

    for root in APP_ROOTS:
        for path in root.rglob("*.py"):
            if "management/commands" in path.as_posix():
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if UNDERSCORE_FORMATTING.search(line):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{number}: "
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
