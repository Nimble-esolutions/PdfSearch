#!/usr/bin/env python3
"""Build and verify the release source and DataOps migration contract.

The module intentionally has no Django dependency.  It is used while building
the image, by CI after pulling an immutable candidate, and by both runtime
entrypoints before they are allowed to mutate either persistent volume.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import quote


CONTRACT_VERSION = 1
MIGRATION_PATTERN = re.compile(r"^[0-9]{4}_.+\.py$")
DATAOPS_MIGRATIONS = Path("flowdocs/dataops/migrations")
FIXED_SENTINELS = (
    Path("flowdocs/dataops/models.py"),
    Path("docker-entrypoint.sh"),
    Path("worker-entrypoint.sh"),
    Path("start.sh"),
    Path("scripts/ops/release_integrity.py"),
)


class IntegrityError(RuntimeError):
    """A stable release or startup contract could not be proven."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _migration_refs(node: ast.AST | None) -> set[tuple[str, str]]:
    """Extract literal ``(app, migration)`` references from an AST value."""

    refs: set[tuple[str, str]] = set()
    if node is None:
        return refs
    for child in ast.walk(node):
        if not isinstance(child, (ast.Tuple, ast.List)) or len(child.elts) != 2:
            continue
        app, name = child.elts
        if (
            isinstance(app, ast.Constant)
            and isinstance(app.value, str)
            and isinstance(name, ast.Constant)
            and isinstance(name.value, str)
        ):
            refs.add((app.value, name.value))
    return refs


def _migration_assignments(path: Path) -> dict[str, ast.AST]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise IntegrityError(f"cannot parse DataOps migration {path}: {exc}") from exc

    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        result: dict[str, ast.AST] = {}
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = item.value
        return result
    raise IntegrityError(f"DataOps migration has no Migration class: {path}")


def migration_contract(root: Path) -> dict[str, list[str]]:
    migration_dir = root / DATAOPS_MIGRATIONS
    if not migration_dir.is_dir():
        raise IntegrityError(f"DataOps migrations directory is missing: {migration_dir}")

    paths = sorted(
        path for path in migration_dir.iterdir() if MIGRATION_PATTERN.match(path.name)
    )
    if not paths:
        raise IntegrityError(f"no DataOps migrations found in {migration_dir}")

    names = {path.stem for path in paths}
    dependencies: set[str] = set()
    replaces: set[str] = set()
    for path in paths:
        assignments = _migration_assignments(path)
        dependencies.update(
            name
            for app, name in _migration_refs(assignments.get("dependencies"))
            if app == "dataops" and name in names
        )
        replaces.update(
            name
            for app, name in _migration_refs(assignments.get("replaces"))
            if app == "dataops"
        )

    leaves = sorted(names - dependencies)
    if not leaves:
        raise IntegrityError("DataOps migration graph has no leaf")
    return {
        "known": sorted(names),
        "leaves": leaves,
        "replaces": sorted(replaces),
    }


def build_manifest(root: Path) -> dict[str, object]:
    root = root.resolve()
    contract = migration_contract(root)
    sentinel_paths = set(FIXED_SENTINELS)
    sentinel_paths.update(
        DATAOPS_MIGRATIONS / f"{name}.py" for name in contract["known"]
    )

    sentinels: dict[str, str] = {}
    for relative in sorted(sentinel_paths, key=lambda item: item.as_posix()):
        absolute = root / relative
        if not absolute.is_file():
            raise IntegrityError(f"release sentinel is missing: {relative.as_posix()}")
        sentinels[relative.as_posix()] = _sha256(absolute)

    return {
        "contract_version": CONTRACT_VERSION,
        "dataops_migrations": contract,
        "sentinels": sentinels,
    }


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot read release manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IntegrityError(f"release manifest must be a JSON object: {path}")
    return payload


def _differences(expected: dict[str, object], actual: dict[str, object]) -> list[str]:
    if expected == actual:
        return []
    differences: list[str] = []
    if expected.get("contract_version") != actual.get("contract_version"):
        differences.append("contract_version")
    if expected.get("dataops_migrations") != actual.get("dataops_migrations"):
        differences.append("dataops_migrations")

    expected_hashes = expected.get("sentinels", {})
    actual_hashes = actual.get("sentinels", {})
    if isinstance(expected_hashes, dict) and isinstance(actual_hashes, dict):
        for key in sorted(set(expected_hashes) | set(actual_hashes)):
            if expected_hashes.get(key) != actual_hashes.get(key):
                differences.append(f"sentinel:{key}")
    else:
        differences.append("sentinels")
    return differences or ["manifest"]


def verify_manifest(root: Path, manifest_path: Path) -> None:
    expected = _load_manifest(manifest_path)
    actual = build_manifest(root)
    differences = _differences(expected, actual)
    if differences:
        raise IntegrityError("release_integrity_mismatch fields=" + ",".join(differences))


def compare_manifests(expected_path: Path, actual_path: Path) -> None:
    expected = _load_manifest(expected_path)
    actual = _load_manifest(actual_path)
    differences = _differences(expected, actual)
    if differences:
        raise IntegrityError("release_integrity_mismatch fields=" + ",".join(differences))


def _readonly_sqlite_uri(path: Path) -> str:
    # URI mode=ro guarantees that this compatibility gate cannot create or
    # journal the database it is inspecting.
    absolute = str(path.resolve())
    return f"file:{quote(absolute, safe='/')}?mode=ro"


def check_startup_compatibility(control_db: Path, migrations_root: Path) -> str:
    if not control_db.exists():
        return "control_database_absent"
    if not control_db.is_file():
        raise IntegrityError(f"control database is not a regular file: {control_db}")

    contract = migration_contract(migrations_root)
    represented = set(contract["known"]) | set(contract["replaces"])
    before = control_db.stat()
    try:
        connection = sqlite3.connect(_readonly_sqlite_uri(control_db), uri=True)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'"
            ).fetchone()
            if table is None:
                return "migration_history_absent"
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM django_migrations WHERE app = ?", ("dataops",)
                )
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise IntegrityError(
            f"cannot read DataOps control migration history from {control_db}: {exc}"
        ) from exc

    after = control_db.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise IntegrityError("startup compatibility check observed control database mutation")

    unknown = sorted(applied - represented)
    if unknown:
        raise IntegrityError(
            "startup_schema_contract_older_than_control_database "
            f"unknown_applied_dataops_migrations={','.join(unknown)} "
            f"packaged_leaves={','.join(contract['leaves'])}"
        )
    return "compatible"


def _write_json(payload: dict[str, object], output: Path | None) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(encoded)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="emit canonical sentinel hashes")
    manifest.add_argument("--root", type=Path, required=True)
    manifest.add_argument("--output", type=Path)

    verify = subparsers.add_parser("verify", help="verify files against a manifest")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)

    compare = subparsers.add_parser("compare", help="compare two canonical manifests")
    compare.add_argument("--expected", type=Path, required=True)
    compare.add_argument("--actual", type=Path, required=True)

    startup = subparsers.add_parser(
        "startup-compatibility",
        help="fail if the control DB has newer DataOps migrations than this source",
    )
    startup.add_argument("--control-db", type=Path, required=True)
    startup.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "manifest":
            _write_json(build_manifest(args.root), args.output)
            return 0
        if args.command == "verify":
            verify_manifest(args.root, args.manifest)
            print("release_integrity_ok")
            return 0
        if args.command == "compare":
            compare_manifests(args.expected, args.actual)
            print("release_integrity_match")
            return 0
        if args.command == "startup-compatibility":
            status = check_startup_compatibility(args.control_db, args.root)
            print(f"startup_schema_compatibility_ok status={status}")
            return 0
    except IntegrityError as exc:
        print(f"release_integrity_error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
