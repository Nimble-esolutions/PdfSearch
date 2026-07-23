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
            report.warnings.append(
                f"Generation was created with app release "
                f"'{manifest.get('app_release')}', running '{app_release}'"
            )

    return report


def _check_manifest_version(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    version = manifest.get("manifest_version")
    if version is None:
        report.checks["manifest_version"] = True
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
    manifest_applied = set(manifest_migrations.get("applied", []))

    if not manifest_leaf and not manifest_applied:
        report.checks["migrations"] = True
        return

    try:
        executor = MigrationExecutor(connection)
        current_applied_names = set(
            f"{m.app_label}.{m.name}"
            for m in executor.loader.applied_migrations
        )

        report.checks["migration_no_downgrade"] = manifest_applied.issubset(current_applied_names)

        if manifest_applied - current_applied_names:
            missing = manifest_applied - current_applied_names
            report.warnings.append(
                f"Generation has {len(missing)} migration(s) not in current app: "
                f"{', '.join(sorted(missing)[:5])}"
            )
    except Exception as exc:
        report.warnings.append(f"Migration compatibility check unavailable: {exc}")


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

    manifest_dim = manifest.get("embedding_index", {}).get("dimensions")
    if manifest_dim:
        report.checks["embedding_dimensions"] = True
        report.checks["embedding_dimension_value"] = str(manifest_dim)


def _check_faiss_compat(manifest: Mapping[str, Any], report: CompatibilityReport) -> None:
    faiss_count = manifest.get("faiss", {}).get("count", 0)
    faiss_files = manifest.get("faiss", {}).get("files", [])
    if not faiss_files:
        report.checks["faiss_present"] = False
        return

    report.checks["faiss_present"] = True
    report.checks["faiss_count"] = len(faiss_files) == faiss_count


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
