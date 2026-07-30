"""Isolated, fail-closed migration rehearsal for restored SQLite databases."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class RehearsalError(RuntimeError):
    reason_code = "migration_rehearsal_failed"

    def __init__(self, reason_code: str | None = None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _migration_leaf(database_path: Path) -> str:
    connection_value = sqlite3.connect(
        f"file:{database_path}?mode=ro", uri=True
    )
    try:
        rows = connection_value.execute(
            "SELECT app, name FROM django_migrations ORDER BY id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RehearsalError("migration_evidence_missing") from exc
    finally:
        connection_value.close()
    if not rows:
        raise RehearsalError("migration_evidence_missing")
    return f"{rows[-1][0]}.{rows[-1][1]}"


def _validate_database(database_path: Path) -> tuple[bool, bool]:
    connection_value = sqlite3.connect(
        f"file:{database_path}?mode=ro", uri=True
    )
    try:
        integrity = connection_value.execute(
            "PRAGMA integrity_check"
        ).fetchone()
        foreign_keys = connection_value.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RehearsalError("migration_rehearsal_database_invalid") from exc
    finally:
        connection_value.close()
    return bool(integrity and integrity[0] == "ok"), not foreign_keys


def rehearse_migrations(
    source_db_path: str | Path,
    *,
    workspace_path: str | Path = "",
    timeout_seconds: int = 120,
    promote_to: str | Path | None = None,
) -> dict[str, Any]:
    """Migrate a private database copy in another Python process.

    The returned evidence contains only bounded, operator-safe fields. The
    subprocess output is deliberately excluded so provider or database errors
    cannot leak into browser-facing control-plane state.
    """
    source = Path(source_db_path).resolve()
    if not source.is_file() or source.is_symlink():
        raise RehearsalError("migration_rehearsal_source_invalid")
    workspace = (
        Path(workspace_path).resolve()
        if workspace_path
        else Path(tempfile.mkdtemp(prefix="pdfsearch-rehearsal-")).resolve()
    )
    rehearsal_root = workspace / "rehearsal"
    rehearsal_root.mkdir(parents=True, exist_ok=True)
    rehearsal_db = rehearsal_root / "db.sqlite3"
    if rehearsal_db.exists():
        raise RehearsalError("migration_rehearsal_workspace_not_empty")
    shutil.copy2(source, rehearsal_db)
    before = _migration_leaf(rehearsal_db)
    started = time.monotonic()
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    environment = os.environ.copy()
    environment.update(
        {
            "SQLITE_DB_PATH": str(rehearsal_db),
            "ALLOW_INSECURE_DEFAULTS": "1",
            "VAULT_ADMIN_MUTATIONS_ENABLED": "0",
            "VAULT_RESTORE_ENABLED": "0",
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "0",
            "STAGING_INITIAL_ACTIVATION_ENABLED": "0",
        }
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(manage_py),
                "migrate",
                "--noinput",
                "--verbosity",
                "0",
            ],
            cwd=settings.BASE_DIR,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RehearsalError("migration_rehearsal_timeout") from exc
    if result.returncode != 0:
        raise RehearsalError("migration_rehearsal_failed")
    integrity_ok, foreign_keys_ok = _validate_database(rehearsal_db)
    if not integrity_ok:
        raise RehearsalError("migration_rehearsal_integrity_failed")
    if not foreign_keys_ok:
        raise RehearsalError("migration_rehearsal_foreign_keys_failed")
    after = _migration_leaf(rehearsal_db)
    if promote_to is not None:
        destination = Path(promote_to).resolve()
        temporary = destination.with_name(
            f".{destination.name}.rehearsed.partial"
        )
        shutil.copy2(rehearsal_db, temporary)
        os.replace(temporary, destination)
    return {
        "success": True,
        "migration_leaf_before": before,
        "migration_leaf_after": after,
        "integrity_ok": integrity_ok,
        "foreign_keys_ok": foreign_keys_ok,
        "image_rollback_safe": (
            "image_rollback_safe"
            if before == after
            else "image_rollback_requires_data_rollback"
        ),
        "app_release": settings.ENV_IDENTITY.app_release_version,
        "image_digest": settings.ENV_IDENTITY.app_image_digest,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def detect_schema_incompatibility(
    restored_leaf: str, current_leaf: str
) -> dict[str, Any]:
    """Compare a restored migration leaf with migrations known to this image."""
    if not restored_leaf:
        return {
            "compatible": False,
            "direction": "unknown",
            "reason": "migration_evidence_missing",
        }

    executor = MigrationExecutor(connection)
    current_applied = {
        f"{app_label}.{name}"
        for app_label, name in executor.loader.applied_migrations
    }
    if restored_leaf in current_applied:
        return {"compatible": True, "direction": "same_or_earlier"}

    restored_parts = restored_leaf.split(".", 1)
    if len(restored_parts) != 2:
        return {
            "compatible": False,
            "direction": "newer",
            "reason": "migration_leaf_invalid",
        }
    restored_app, restored_name = restored_parts
    try:
        restored_number = int(restored_name.split("_")[0])
    except (ValueError, IndexError):
        return {
            "compatible": False,
            "direction": "newer",
            "reason": "migration_leaf_invalid",
        }

    latest_current = 0
    for migration_name in current_applied:
        if migration_name.startswith(f"{restored_app}."):
            try:
                number = int(
                    migration_name.split(".")[1].split("_")[0]
                )
                latest_current = max(latest_current, number)
            except (ValueError, IndexError):
                continue
    if restored_number > latest_current:
        return {
            "compatible": False,
            "direction": "newer",
            "reason": "restored_schema_newer",
        }
    return {
        "compatible": False,
        "direction": "unknown",
        "reason": "migration_leaf_unknown",
    }
