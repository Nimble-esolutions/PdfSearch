"""Read-only validation of an inventory manifest against a data root."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .management.commands.inventory_artifacts import (
    INVENTORY_SCHEMA,
    _sqlite_uri,
    build_manifest,
    inspect_faiss_file,
)


REQUIRED_COUNTS = (
    "pdf_rows",
    "pdf_storage_files",
    "faiss_files",
    "preserved_target_only_rows",
)


class DataReleaseValidationError(ValueError):
    """Raised when a manifest or expected-count policy is unsafe to use."""


def _issue(kind: str, **details: Any) -> dict[str, Any]:
    return {"kind": kind, **details}


def _record_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("path")): record for record in records}


def _record_differences(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    differences = []
    expected_map = _record_map(expected)
    actual_map = _record_map(actual)
    for path in sorted(set(expected_map) | set(actual_map)):
        expected_record = expected_map.get(path)
        actual_record = actual_map.get(path)
        if expected_record is None or actual_record is None:
            differences.append(
                _issue(
                    f"{kind}-path-mismatch",
                    path=path,
                    expected=expected_record is not None,
                    actual=actual_record is not None,
                )
            )
            continue
        for field in ("size_bytes", "sha256"):
            if expected_record.get(field) != actual_record.get(field):
                differences.append(
                    _issue(
                        f"{kind}-{field}-mismatch",
                        path=path,
                        expected=expected_record.get(field),
                        actual=actual_record.get(field),
                    )
                )
    return differences


def _pdf_differences(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    differences = []
    expected_map = {str(item.get("db_id")): item for item in expected}
    actual_map = {str(item.get("db_id")): item for item in actual}
    for db_id in sorted(set(expected_map) | set(actual_map)):
        expected_item = expected_map.get(db_id)
        actual_item = actual_map.get(db_id)
        if expected_item is None or actual_item is None:
            differences.append(
                _issue(
                    "pdf-row-mismatch",
                    db_id=db_id,
                    expected=expected_item is not None,
                    actual=actual_item is not None,
                )
            )
            continue
        for field in ("path", "file_status", "size_bytes", "sha256"):
            if expected_item.get(field) != actual_item.get(field):
                differences.append(
                    _issue(
                        f"pdf-{field}-mismatch",
                        db_id=db_id,
                        expected=expected_item.get(field),
                        actual=actual_item.get(field),
                    )
                )
    return differences


def _sqlite_checks(database_path: Path) -> dict[str, Any]:
    checks = {"integrity": None, "foreign_keys": [], "error": None}
    if not database_path.is_file():
        checks["error"] = "database-missing"
        return checks
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(_sqlite_uri(database_path), uri=True)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        checks["integrity"] = integrity
        checks["foreign_keys"] = [list(row) for row in foreign_keys]
    except sqlite3.Error as exc:
        checks["error"] = f"sqlite-check-error: {exc.__class__.__name__}"
    finally:
        if connection is not None:
            connection.close()
    return checks


def _expected_counts(
    manifest: Mapping[str, Any], overrides: Mapping[str, int] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = dict(manifest.get("expected_counts") or {})
    values.update(overrides or {})
    issues = []
    for name in REQUIRED_COUNTS:
        if name not in values:
            issues.append(_issue("expected-count-missing", name=name))
            continue
        if not isinstance(values[name], int) or isinstance(values[name], bool) or values[name] < 0:
            issues.append(_issue("expected-count-invalid", name=name, value=values[name]))
    if values.get("faiss_files", 0) > 0 and "faiss_vectors" not in values:
        issues.append(_issue("expected-count-missing", name="faiss_vectors"))
    if "faiss_vectors" in values and (
        not isinstance(values["faiss_vectors"], int)
        or isinstance(values["faiss_vectors"], bool)
        or values["faiss_vectors"] < 0
    ):
        issues.append(_issue("expected-count-invalid", name="faiss_vectors", value=values["faiss_vectors"]))
    return values, issues


def validate_release(
    manifest: Mapping[str, Any] | bytes | bytearray,
    data_root: str | Path,
    expected_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Validate a release without writing to the manifest or data root."""
    if isinstance(manifest, (bytes, bytearray)):
        try:
            manifest = json.loads(manifest)
        except json.JSONDecodeError as exc:
            raise DataReleaseValidationError("Manifest is not valid JSON") from exc
    if not isinstance(manifest, Mapping):
        raise DataReleaseValidationError("Manifest must contain a JSON object")

    schema = manifest.get("schema")
    if not isinstance(schema, Mapping):
        schema = {}
    database = manifest.get("database")
    if not isinstance(database, Mapping):
        database = {}
    expected_pdfs = manifest.get("pdfs", [])
    if not isinstance(expected_pdfs, list):
        raise DataReleaseValidationError("Manifest pdfs must be a list")
    expected_pdf_storage = manifest.get("pdf_storage")
    if not isinstance(expected_pdf_storage, Mapping):
        expected_pdf_storage = {}
    expected_faiss = manifest.get("faiss")
    if not isinstance(expected_faiss, Mapping):
        expected_faiss = {}
    expected_metadata = manifest.get("embedding_index")
    if not isinstance(expected_metadata, Mapping):
        expected_metadata = {}
    expected_faiss_files = expected_faiss.get("files", [])
    if not isinstance(expected_faiss_files, list):
        raise DataReleaseValidationError("Manifest faiss.files must be a list")

    issues: list[dict[str, Any]] = []
    if manifest.get("manifest_version") != 1:
        issues.append(_issue("manifest-version-mismatch", expected=1, actual=manifest.get("manifest_version")))
    if manifest.get("read_only") is not True:
        issues.append(_issue("manifest-not-read-only"))
    if schema.get("inventory_schema") != INVENTORY_SCHEMA:
        issues.append(
            _issue(
                "inventory-schema-mismatch",
                expected=INVENTORY_SCHEMA,
                actual=schema.get("inventory_schema"),
            )
        )

    policy, policy_issues = _expected_counts(manifest, expected_counts)
    issues.extend(policy_issues)

    root = Path(data_root).expanduser().resolve()
    actual = build_manifest(root)
    sqlite_checks = _sqlite_checks(root / "db.sqlite3")
    if sqlite_checks["error"] or sqlite_checks["integrity"] != "ok" or sqlite_checks["foreign_keys"]:
        issues.append(_issue("sqlite-integrity-failed", checks=sqlite_checks))

    expected_migrations = database.get("migrations", {})
    if not isinstance(expected_migrations, Mapping):
        expected_migrations = {}
    actual_migrations = actual.get("database", {}).get("migrations", {})
    expected_leaf = policy.get("migration_leaf", expected_migrations.get("latest"))
    if expected_leaf != actual_migrations.get("latest"):
        issues.append(
            _issue(
                "migration-leaf-mismatch",
                expected=expected_leaf,
                actual=actual_migrations.get("latest"),
            )
        )
    if expected_migrations.get("applied") != actual_migrations.get("applied"):
        issues.append(_issue("migration-applied-set-mismatch"))

    issues.extend(_pdf_differences(expected_pdfs, actual.get("pdfs", [])))
    issues.extend(
        _record_differences(
            expected_pdf_storage.get("files", []),
            actual.get("pdf_storage", {}).get("files", []),
            "pdf-storage",
        )
    )
    issues.extend(
        _record_differences(
            expected_faiss_files,
            actual.get("faiss", {}).get("files", []),
            "faiss",
        )
    )
    issues.extend(
        _record_differences(
            expected_metadata.get("metadata_files", []),
            actual.get("embedding_index", {}).get("metadata_files", []),
            "metadata",
        )
    )

    actual_counts = actual.get("counts", {})
    expected_count_fields = {
        "pdf_rows": actual_counts.get("pdf_rows"),
        "pdf_storage_files": actual_counts.get("pdf_storage_files"),
        "faiss_files": actual_counts.get("faiss_files"),
        "preserved_target_only_rows": actual_counts.get("pdf_rows_missing_files"),
    }
    for name, actual_value in expected_count_fields.items():
        if name in policy and policy[name] != actual_value:
            issues.append(_issue("expected-count-mismatch", name=name, expected=policy[name], actual=actual_value))

    faiss_total_vectors = 0
    faiss_dimensions: set[int] = set()
    for expected_file in expected_faiss_files:
        path_value = expected_file.get("path")
        if not path_value:
            issues.append(_issue("faiss-path-missing"))
            continue
        path = (root / path_value).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            issues.append(_issue("faiss-unsafe-path", path=path_value))
            continue
        if not path.is_file():
            continue
        metadata = inspect_faiss_file(path)
        expected_faiss = expected_file.get("faiss") or {}
        if not metadata.get("loadable"):
            if expected_faiss.get("loadable") or "faiss_vectors" in policy:
                issues.append(_issue("faiss-not-loadable", path=path_value, reason=metadata.get("load_error")))
            continue
        dimensions = metadata["dimensions"]
        vector_count = metadata["vector_count"]
        faiss_total_vectors += vector_count
        faiss_dimensions.add(dimensions)
        for field in ("dimensions", "vector_count"):
            if expected_faiss.get(field) is not None and expected_faiss[field] != metadata[field]:
                issues.append(
                    _issue(
                        f"faiss-{field}-mismatch",
                        path=path_value,
                        expected=expected_faiss[field],
                        actual=metadata[field],
                    )
                )

    if "faiss_vectors" in policy and policy["faiss_vectors"] != faiss_total_vectors:
        issues.append(
            _issue(
                "expected-count-mismatch",
                name="faiss_vectors",
                expected=policy["faiss_vectors"],
                actual=faiss_total_vectors,
            )
        )
    if policy.get("faiss_dimension") is not None and faiss_dimensions != {policy["faiss_dimension"]}:
        issues.append(
            _issue(
                "faiss-dimension-mismatch",
                expected=policy["faiss_dimension"],
                actual=sorted(faiss_dimensions),
            )
        )

    return {
        "ok": not issues,
        "data_root": str(root),
        "checks": {
            "sqlite": sqlite_checks,
            "migration_leaf": actual_migrations.get("latest"),
            "faiss_dimensions": sorted(faiss_dimensions),
            "faiss_vectors": faiss_total_vectors,
            "counts": actual_counts,
        },
        "expected_counts": policy,
        "issues": issues,
    }
