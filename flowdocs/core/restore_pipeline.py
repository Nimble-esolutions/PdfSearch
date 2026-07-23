"""Complete restore pipeline: download → validate → sanitize → rehearse → activate.

Wires restore_workspace, sanitize, compatibility, rehearsal, and activate
modules into a single audited flow. Never modifies the source generation
or partially overwrites active data.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from .artifact_vault import ArtifactVault, ArtifactVaultError
from .models import ArtifactGeneration, ArtifactValidation, MaintenanceAuditEvent, MaintenanceJob
from .namespace import KeyBuilder
from .restore_workspace import (
    RestoreWorkspace, WorkspaceState, RestoreMode,
    create_workspace, WorkspaceError,
)


class RestoreError(RuntimeError):
    """Raised when a restore operation cannot proceed."""


def _resolve_generation(
    vault: ArtifactVault,
    source_dataset_id: str,
    *,
    restore_mode: RestoreMode,
    pinned_generation: str = "",
) -> str:
    """Resolve which generation to restore based on mode."""
    if restore_mode in (RestoreMode.PINNED, RestoreMode.EXACT_PRODUCTION):
        if not pinned_generation:
            raise RestoreError(
                f"Pinned generation ID is required for {restore_mode.value} mode"
            )
        return pinned_generation

    if restore_mode == RestoreMode.LATEST_COMPATIBLE:
        from .registration import get_authoritative_pointer
        pointer = get_authoritative_pointer(vault, source_dataset_id)
        if pointer is None:
            raise RestoreError(
                f"No authoritative pointer found for dataset {source_dataset_id}"
            )
        return pointer.get("generation_id", "")

    raise RestoreError(f"Unsupported restore mode: {restore_mode.value}")


def run_restore_pipeline(
    vault: ArtifactVault,
    *,
    job: MaintenanceJob,
    source_dataset_id: str,
    restore_mode: RestoreMode,
    pinned_generation: str = "",
    target_dataset_id: str = "",
    target_environment: str = "",
    sanitize: bool = False,
    sanitization_policy: str = "default-v1",
    run_rehearsal: bool = False,
    activate: bool = False,
) -> dict[str, Any]:
    """Execute the full restore pipeline.

    Returns a result dict with the workspace state on completion.
    """
    from .registration import validate_registration

    generation_id = _resolve_generation(
        vault, source_dataset_id,
        restore_mode=restore_mode,
        pinned_generation=pinned_generation,
    )
    if not generation_id:
        raise RestoreError("Could not resolve a generation to restore")

    validate_registration(vault, source_dataset_id, app_identifier="pdfsearch")

    workspace_id = f"restore-{source_dataset_id}-{generation_id[:16]}-{secrets.token_hex(4)}"
    ws = create_workspace(
        workspace_id=workspace_id,
        restore_job_id=str(job.public_id) if job else "",
        source_dataset_id=source_dataset_id,
        source_generation_id=generation_id,
        target_dataset_id=target_dataset_id,
        target_environment=target_environment,
        restore_mode=restore_mode,
        sanitization_policy=sanitization_policy if sanitize else "",
    )

    try:
        ws.transition(WorkspaceState.DOWNLOADING)
        ws.save_metadata()
        _download_generation(vault, ws, source_dataset_id, generation_id)

        ws.transition(WorkspaceState.DOWNLOADED)
        ws.save_metadata()

        _validate_source(ws)
        ws.transition(WorkspaceState.SOURCE_VALIDATED)
        ws.save_metadata()

        _compatibility_preflight(ws)
        ws.transition(WorkspaceState.PREFLIGHT_PASSED)
        ws.save_metadata()

        if sanitize:
            _run_sanitization(ws, sanitization_policy)
            ws.transition(WorkspaceState.SANITIZED)
            ws.save_metadata()

        if run_rehearsal:
            working_db = ws.dirs()["working"] / "db.sqlite3"
            if not working_db.is_file():
                working_path = ws.dirs()["source"] / "db.sqlite3"
                import shutil
                shutil.copy2(working_path, working_db)
            from .rehearsal import rehearse_migrations
            ws.transition(WorkspaceState.MIGRATION_REHEARSAL)
            ws.save_metadata()
            result = rehearse_migrations(
                working_db,
                workspace_path=ws.local_path,
            )
            ws.migration_rehearsal_result = result
            ws.rollback_image_safe = result.get("image_rollback_safe", "unknown")
            if result["success"]:
                ws.transition(WorkspaceState.MIGRATION_READY)
            else:
                ws.transition(WorkspaceState.FAILED, reason=f"Migration rehearsal failed: {result.get('error', 'unknown')}")
                ws.save_metadata()
                return _restore_result(ws)
            ws.save_metadata()

        ws.activation_eligible = True
        ws.transition(WorkspaceState.APPLICATION_VALIDATED)
        ws.save_metadata()

        ws.transition(WorkspaceState.ACTIVATION_READY)
        ws.save_metadata()

        if activate:
            ws.transition(WorkspaceState.ACTIVATING)
            ws.save_metadata()
            from .activate import activate_generation
            activation = activate_generation(
                ws.local_path,
                generation_id=generation_id,
            )
            ws.transition(WorkspaceState.ACTIVE)
            ws.save_metadata()

        return _restore_result(ws)

    except Exception as exc:
        if ws.state != WorkspaceState.FAILED:
            ws.transition(WorkspaceState.FAILED, reason=str(exc))
            ws.save_metadata()
        raise RestoreError(str(exc)) from exc


def _download_generation(
    vault: ArtifactVault,
    ws: RestoreWorkspace,
    dataset_id: str,
    generation_id: str,
) -> None:
    keys = KeyBuilder(dataset_id)
    manifest_key = keys.generation_manifest(generation_id)

    try:
        raw_manifest = vault.get(manifest_key)
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, bytes) else raw_manifest
    except ArtifactVaultError:
        from .artifact_vault import ArtifactVault
        legacy_key = ArtifactVault.manifest_object_key(generation_id)
        raw_manifest = vault.get(legacy_key)
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, bytes) else raw_manifest

    if not isinstance(manifest, dict):
        raise RestoreError("Retrieved manifest is not a JSON object")

    source_dir = ws.dirs()["source"]
    (source_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ws.source_manifest_sha256 = _sha256(source_dir / "manifest.json")

    for entry in manifest.get("files", []):
        object_key = entry.get("object_key", "")
        path = entry.get("path", "")
        if not object_key or not path:
            continue

        data = vault.get(object_key, expected_sha256=entry.get("sha256"))
        target = source_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _validate_source(ws: RestoreWorkspace) -> None:
    errors = []
    source = ws.dirs()["source"]
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        errors.append("manifest.json missing from source directory")

    db_path = source / "db.sqlite3"
    if db_path.is_file():
        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            if integrity != "ok":
                errors.append(f"SQLite integrity check failed: {integrity}")
        except Exception as exc:
            errors.append(f"Cannot open source database: {exc}")

    if errors:
        raise RestoreError(" | ".join(errors))


def _compatibility_preflight(ws: RestoreWorkspace) -> None:
    from .compatibility import check_generation_compatibility
    manifest_path = ws.dirs()["source"] / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        report = check_generation_compatibility(manifest)
        if not report.compatible:
            raise RestoreError(f"Compatibility check failed: {'; '.join(report.errors)}")


def _run_sanitization(ws: RestoreWorkspace, policy: str) -> None:
    import shutil
    source_db = ws.dirs()["source"] / "db.sqlite3"
    working_db = ws.dirs()["working"] / "db.sqlite3"
    if not source_db.is_file():
        raise RestoreError("No database to sanitize in source directory")

    shutil.copy2(source_db, working_db)
    from .sanitize import sanitize_database, validate_sanitization
    result = sanitize_database(
        source_db, working_db,
        dataset_id=ws.target_dataset_id or ws.source_dataset_id,
    )
    issues = validate_sanitization(working_db)
    if issues:
        raise RestoreError(f"Sanitization validation failed: {'; '.join(issues)}")
    ws.derived_manifest = {
        "source_dataset_id": ws.source_dataset_id,
        "source_generation_id": ws.source_generation_id,
        "target_dataset_id": ws.target_dataset_id,
        "sanitization_policy": policy,
        "not_authoritative_production_data": True,
        "sanitization_stats": result.get("stats", {}),
    }


def _restore_result(ws: RestoreWorkspace) -> dict[str, Any]:
    return {
        "workspace_id": ws.workspace_id,
        "state": ws.state.value,
        "source_dataset_id": ws.source_dataset_id,
        "source_generation_id": ws.source_generation_id,
        "target_dataset_id": ws.target_dataset_id,
        "activation_eligible": ws.activation_eligible,
        "failure_reason": ws.failure_reason,
        "local_path": ws.local_path,
    }


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
