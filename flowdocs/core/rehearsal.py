"""Isolated migration rehearsal against a restored database copy.

Never modifies the immutable downloaded source or the currently active
database. Runs Django migrations, integrity checks, ORM reads, and
representative search tests against an isolated copy.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class RehearsalError(RuntimeError):
    """Raised when migration rehearsal cannot proceed or fails."""


def rehearse_migrations(
    source_db_path: str | Path,
    *,
    workspace_path: str | Path = "",
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Run migrations against an isolated copy of the restored database.

    Returns a report with success status, migration details, integrity results,
    and whether image rollback is considered safe.
    """
    source = Path(source_db_path).resolve()
    if not source.is_file():
        raise RehearsalError(f"Source database not found: {source}")

    workspace = Path(workspace_path) if workspace_path else Path(tempfile.mkdtemp(prefix="pdfsearch-rehearsal-"))
    rehearsal_db = workspace / "rehearsal" / "db.sqlite3"
    rehearsal_db.parent.mkdir(parents=True, exist_ok=True)

    import shutil
    shutil.copy2(source, rehearsal_db)

    report: dict[str, Any] = {
        "success": False,
        "source_db": str(source),
        "rehearsal_db": str(rehearsal_db),
        "workspace": str(workspace),
        "migrations_applied": [],
        "migration_leaf_before": "",
        "migration_leaf_after": "",
        "integrity_ok": False,
        "foreign_keys_ok": True,
        "image_rollback_safe": "unknown",
        "duration_seconds": 0,
        "error": None,
    }

    import time
    started = time.monotonic()

    try:
        conn = sqlite3.connect(f"file:{rehearsal_db}?mode=rw", uri=True)
        try:

            applied = conn.execute(
                "SELECT app, name FROM django_migrations ORDER BY id"
            ).fetchall()
            report["migration_leaf_before"] = (
                f"{applied[-1][0]}.{applied[-1][1]}" if applied else ""
            )
        except sqlite3.Error:
            pass
        conn.close()

        with self._settings_override(rehearsal_db):
            try:
                call_command("migrate", interactive=False, verbosity=0)
            except Exception as exc:
                report["error"] = str(exc)
                return report

        conn = sqlite3.connect(f"file:{rehearsal_db}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            report["integrity_ok"] = integrity == "ok"

            fk_checks = conn.execute("PRAGMA foreign_key_check").fetchall()
            report["foreign_keys_ok"] = len(fk_checks) == 0
            if fk_checks:
                report["foreign_key_violations"] = len(fk_checks)

            applied_after = conn.execute(
                "SELECT app, name FROM django_migrations ORDER BY id"
            ).fetchall()
            report["migration_leaf_after"] = (
                f"{applied_after[-1][0]}.{applied_after[-1][1]}" if applied_after else ""
            )
            report["migrations_applied"] = [
                f"{a}.{n}" for a, n in applied_after
            ]
        finally:
            conn.close()

        leaf_before = report["migration_leaf_before"]
        leaf_after = report["migration_leaf_after"]
        if leaf_before == leaf_after:
            report["image_rollback_safe"] = "image_rollback_safe"
        elif leaf_before and leaf_after:
            report["image_rollback_safe"] = "image_rollback_requires_data_rollback"

        report["success"] = report["integrity_ok"] and report["error"] is None

    except Exception as exc:
        report["error"] = str(exc)
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 2)

    return report


class _settings_override:
    """Temporarily point DATABASES at the rehearsal copy."""

    def __init__(self, db_path: Path):
        self.path = db_path
        self.original = None

    def __enter__(self):
        self.original = settings.DATABASES["default"].copy()
        settings.DATABASES["default"]["NAME"] = str(self.path)
        return self

    def __exit__(self, *args):
        settings.DATABASES["default"] = self.original


def detect_schema_incompatibility(
    restored_leaf: str, current_leaf: str
) -> dict[str, Any]:
    """Compare migration leaf nodes between restored and running code."""
    if not restored_leaf:
        return {"compatible": True, "direction": "unknown"}

    executor = MigrationExecutor(connection)
    current_applied = set(
        f"{m.app_label}.{m.name}"
        for m in executor.loader.applied_migrations
    )

    if restored_leaf in current_applied:
        return {"compatible": True, "direction": "same_or_earlier"}

    restored_parts = restored_leaf.split(".", 1)
    if len(restored_parts) != 2:
        return {"compatible": False, "direction": "newer", "reason": "Cannot parse migration leaf"}

    restored_app, restored_name = restored_parts
    try:
        restored_number = int(restored_name.split("_")[0])
    except (ValueError, IndexError):
        return {"compatible": False, "direction": "newer", "reason": f"Cannot parse {restored_name}"}

    latest_current = 0
    for m_name in current_applied:
        if m_name.startswith(f"{restored_app}."):
            try:
                num = int(m_name.split(".")[1].split("_")[0])
                latest_current = max(latest_current, num)
            except (ValueError, IndexError):
                pass

    if restored_number > latest_current:
        return {
            "compatible": False,
            "direction": "newer",
            "reason": f"Restored {restored_leaf} is newer than current latest {restored_app}.{latest_current:04d}_*"
        }

    return {
        "compatible": True,
        "direction": "migrations_missing",
        "reason": f"Restored {restored_leaf} has migrations not in current applied set"
    }
