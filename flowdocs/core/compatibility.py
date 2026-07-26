"""Compatibility preflight checks for restore operations.

Before activating a restored generation, verify that the generation's
schema, embedding model, index format, and application version are
compatible with the running image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from django.conf import settings


@dataclass
class CompatibilityReport:
    """Result of a compatibility check between a generation and running image."""

    compatible: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "CompatibilityReport") -> "CompatibilityReport":
        return CompatibilityReport(
            compatible=self.compatible and other.compatible,
            checks={**self.checks, **other.checks},
            warnings=self.warnings + other.warnings,
            errors=self.errors + other.errors,
        )


def check_generation_compatibility(
    manifest: Mapping[str, Any],
    *,
    app_release: str = "",
    image_digest: str = "",
) -> CompatibilityReport:
    """Check whether a generation manifest is compatible with the running code."""
    report = CompatibilityReport()

    _check_manifest_version(manifest, report)
    _check_migration_compat(manifest, report)
    _check_embedding_compat(manifest, report)
    _check_faiss_compat(manifest, report)
    _check_sanitization_compat(manifest, report)

    if app_release and manifest.get("app_release"):
        report.checks["app_release"] = manifest.get("app_release") == app_release
        if not report.checks["app_release"]:
            report.errors.append(
                f"Generation was created with app release "
                f"'{manifest.get('app_release')}', running '{app_release}'"
            )
            report.compatible = False
    elif app_release:
        report.checks["app_release"] = False
        report.errors.append("Generation has no application release evidence")
        report.compatible = False

    if image_digest:
        report.checks["image_digest"] = (
            manifest.get("image_digest") == image_digest
        )
        if not report.checks["image_digest"]:
            report.errors.append("Generation image digest is incompatible")
            report.compatible = False

    return report


def _check_manifest_version(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    version = manifest.get("manifest_version")
    if version is None:
        report.checks["manifest_version"] = False
        report.errors.append("Manifest version evidence is missing")
        report.compatible = False
        return
    report.checks["manifest_version"] = version == 1
    if not report.checks["manifest_version"]:
        report.errors.append(
            f"Unsupported manifest version: {version} (supported: 1)"
        )
        report.compatible = False


def _check_migration_compat(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    from django.db.migrations.executor import MigrationExecutor
    from django.db import connection

    manifest_migrations = manifest.get("database", {}).get("migrations", {})
    manifest_leaf = manifest_migrations.get("latest", "")
    raw_applied = manifest_migrations.get("applied", [])
    manifest_applied = {_normalize_migration_name(m) for m in raw_applied}

    if not manifest_leaf and not manifest_applied:
        report.checks["migrations"] = False
        report.errors.append("Database migration evidence is missing")
        report.compatible = False
        return

    try:
        executor = MigrationExecutor(connection)
        current_applied_names = {
            f"{app_label}.{name}"
            for app_label, name in executor.loader.applied_migrations
        }

        report.checks["migration_no_downgrade"] = manifest_applied.issubset(
            current_applied_names
        )
        report.checks["migrations"] = True

        if manifest_applied - current_applied_names:
            missing = manifest_applied - current_applied_names
            report.errors.append(
                f"Generation has {len(missing)} migration(s) not in current app: "
                f"{', '.join(sorted(missing)[:5])}"
            )
            report.compatible = False
        if manifest_leaf:
            normalized_leaf = (
                manifest_leaf
                if "." in manifest_leaf
                else f"core.{manifest_leaf}"
            )
            report.checks["migration_leaf_known"] = (
                normalized_leaf in current_applied_names
            )
            if not report.checks["migration_leaf_known"]:
                report.errors.append(
                    f"Generation migration leaf is unknown: {manifest_leaf}"
                )
                report.compatible = False
    except Exception:
        report.checks["migrations"] = False
        report.errors.append(
            "Migration compatibility check is unavailable"
        )
        report.compatible = False


def _check_embedding_compat(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    manifest_model = manifest.get("embedding_index", {}).get("model", "")
    current_model = getattr(settings, "OPENAI_EMBED_MODEL", "")

    report.checks["embedding_model"] = (
        not manifest_model or not current_model or manifest_model == current_model
    )
    if not report.checks["embedding_model"]:
        report.errors.append(
            f"Embedding model mismatch: generation used '{manifest_model}', "
            f"current is '{current_model}'. FAISS indexes will be incompatible."
        )
        report.compatible = False

    manifest_dim = manifest.get("embedding_index", {}).get(
        "dimensions",
        manifest.get("embedding_index", {}).get("embedding_dimensions"),
    )
    if manifest_dim:
        report.checks["embedding_dimensions"] = True
        report.checks["embedding_dimension_value"] = str(manifest_dim)


def _check_faiss_compat(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    faiss = manifest.get("faiss", {})
    faiss_count = faiss.get("file_count", faiss.get("count"))
    faiss_files = faiss.get("files", [])
    if faiss_count is None or not isinstance(faiss_files, list):
        report.checks["faiss_count"] = False
        report.errors.append("FAISS count evidence is missing")
        report.compatible = False
        return

    report.checks["faiss_present"] = bool(faiss_files)
    report.checks["faiss_count"] = len(faiss_files) == faiss_count
    if not report.checks["faiss_count"]:
        report.errors.append("FAISS file count evidence does not match")
        report.compatible = False


def _check_sanitization_compat(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    sanitized = manifest.get("sanitization", {})
    if sanitized:
        report.checks["is_sanitized"] = True
        report.warnings.append(
            "This generation is sanitized and must not be treated as authoritative production data"
        )
        if sanitized.get("policy_version") != "pdfsearch-sanitize/v1":
            report.warnings.append(
                f"Sanitization policy: {sanitized.get('policy_version')} (current: pdfsearch-sanitize/v1)"
            )


def _normalize_migration_name(m: Any) -> str:
    """Normalize a migration entry to a dotted string like 'core.0001_initial'."""
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        app = m.get("app", "")
        name = m.get("name", "")
        return f"{app}.{name}" if app and name else str(m)
    if isinstance(m, (list, tuple)) and len(m) == 2:
        return f"{m[0]}.{m[1]}"
    return str(m)
