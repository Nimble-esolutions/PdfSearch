#!/usr/bin/env python3
"""Reject machine evidence rendered outside the approved UI boundary."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "flowdocs" / "core" / "templates"
APP_ROOTS = (ROOT / "flowdocs" / "core", ROOT / "flowdocs" / "vaultops")
APPROVED_COMPONENT = TEMPLATE_ROOT / "components" / "operator_evidence.html"
PRESENTATION_REGISTRY = ROOT / "flowdocs" / "core" / "operator_presentation.py"
MARATHI_CATALOG = ROOT / "flowdocs" / "locale" / "mr" / "LC_MESSAGES" / "django.po"

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
PRESENTATION_FIELDS = {"title", "detail", "consequence", "action_label"}
MALFORMED_MARATHI_TOKENS = (
    "प्रोसंचिका",
    "संचिका्स",
    "रोलबॅक",
    "परिचालकला",
    "परिचालकने",
    "परिचालकची",
    "प्रवेश-प्रमाणचा",
    "रूपरेषाचा",
    "रूपरेषाची",
    "रूपरेषामध्ये",
    "रूपरेषामधील",
    "रूपरेषाने",
    "रूपरेषाला",
    "कार्यरत प्रणालीची तयारी",
    "सक्रिय कार्यरत प्रणाली बदललेला",
    "सध्याचा कार्यरत प्रणाली",
    "हा कार्यरत प्रणाली",
    "कार्यरत प्रणाली सज्जतेतून",
    "अचूक सारांश",
    "ताबा-पुरावा",
    "नियंत्रण फलकवर",
    "रूपरेषा संरचनेचा",
    "रूपरेषा तपासणी",
    "कार्यरत प्रणाली पुरावा",
    "कार्यरत प्रणाली मीडिया",
    "पुनर्स्थापनाचा निर्मिती संच",
)
REQUIRED_MARATHI_TRANSLATIONS = {
    "Review runtime evidence": "कार्यरत प्रणालीच्या पुराव्याचा आढावा घ्या",
    "Candidate runtime verification could not be classified": (
        "उमेदवाराच्या कार्यरत प्रणालीच्या पडताळणीचे वर्गीकरण करता आले नाही"
    ),
    "Runtime search verification cannot complete.": (
        "कार्यरत प्रणालीतील शोध पडताळणी पूर्ण होऊ शकत नाही."
    ),
    "Runtime search verification cannot continue.": (
        "कार्यरत प्रणालीतील शोध पडताळणी पुढे सुरू राहू शकत नाही."
    ),
}


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _definition_strings(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.Tuple, ast.List)):
        return {
            value
            for item in node.elts[:4]
            if (value := _literal(item)) is not None
        }
    if isinstance(node, ast.Call):
        return {
            value
            for item in node.args[:4]
            if (value := _literal(item)) is not None
        }
    if isinstance(node, ast.Dict):
        return {
            value
            for key, item in zip(node.keys, node.values)
            if _literal(key) in PRESENTATION_FIELDS
            and (value := _literal(item)) is not None
        }
    return set()


def registry_messages(path: Path = PRESENTATION_REGISTRY) -> set[str]:
    """Read every authored registry message without importing Django."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    messages: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Dict):
                continue
            if target.id == "LABELS":
                messages.update(
                    value
                    for item in node.value.values
                    if (value := _literal(item)) is not None
                )
            elif target.id == "UNKNOWN_REASON":
                messages.update(_definition_strings(node.value))
            elif target.id == "REASONS":
                for definition in node.value.values:
                    messages.update(_definition_strings(definition))
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "REASONS"
            and node.value.func.attr == "update"
            and node.value.args
            and isinstance(node.value.args[0], ast.Dict)
        ):
            for definition in node.value.args[0].values:
                messages.update(_definition_strings(definition))
    return messages


def catalog_entries(path: Path = MARATHI_CATALOG) -> dict[str, tuple[str, bool]]:
    """Parse the small PO subset needed for non-empty/fuzzy parity checks."""
    entries: dict[str, tuple[str, bool]] = {}
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []
    fuzzy = False
    active: list[str] | None = None

    def finish() -> None:
        nonlocal msgid_parts, msgstr_parts, fuzzy, active
        msgid = "".join(msgid_parts)
        if msgid:
            entries[msgid] = ("".join(msgstr_parts), fuzzy)
        msgid_parts = []
        msgstr_parts = []
        fuzzy = False
        active = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            finish()
        elif line.startswith("#,"):
            fuzzy = fuzzy or "fuzzy" in {
                flag.strip() for flag in line[2:].split(",")
            }
        elif line.startswith("msgid "):
            active = msgid_parts
            active.append(ast.literal_eval(line[6:]))
        elif line.startswith("msgstr "):
            active = msgstr_parts
            active.append(ast.literal_eval(line[7:]))
        elif line.startswith('"') and active is not None:
            active.append(ast.literal_eval(line))
    finish()
    return entries


def catalog_violations(
    *,
    registry_path: Path = PRESENTATION_REGISTRY,
    catalog_path: Path = MARATHI_CATALOG,
    display_root: Path = ROOT,
) -> list[str]:
    entries = catalog_entries(catalog_path)
    errors: list[str] = []
    catalog_text = catalog_path.read_text(encoding="utf-8")
    for token in MALFORMED_MARATHI_TOKENS:
        if token in catalog_text:
            errors.append(
                f"{catalog_path.relative_to(display_root)}: "
                f"malformed or unreviewed Marathi token: {token!r}"
            )
    for message, expected in REQUIRED_MARATHI_TRANSLATIONS.items():
        translated, _fuzzy = entries.get(message, ("", False))
        if translated != expected:
            errors.append(
                f"{catalog_path.relative_to(display_root)}: "
                f"reviewed Marathi translation mismatch: {message!r}"
            )
    for message in sorted(registry_messages(registry_path)):
        translated, fuzzy = entries.get(message, ("", False))
        if not translated:
            errors.append(
                f"{catalog_path.relative_to(display_root)}: "
                f"missing Marathi registry translation: {message!r}"
            )
        elif fuzzy:
            errors.append(
                f"{catalog_path.relative_to(display_root)}: "
                f"fuzzy Marathi registry translation: {message!r}"
            )
        elif translated == message:
            errors.append(
                f"{catalog_path.relative_to(display_root)}: "
                f"English fallback in Marathi registry translation: {message!r}"
            )
    return errors


def violations(
    *,
    template_root: Path = TEMPLATE_ROOT,
    app_roots: tuple[Path, ...] = APP_ROOTS,
    approved_component: Path = APPROVED_COMPONENT,
    display_root: Path = ROOT,
    check_catalog: bool = True,
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
    if check_catalog:
        errors.extend(
            catalog_violations(
                registry_path=PRESENTATION_REGISTRY,
                catalog_path=MARATHI_CATALOG,
                display_root=display_root,
            )
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
