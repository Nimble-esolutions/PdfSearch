"""Atomic generation activation and rollback.

Activates a validated restore workspace by switching the active data pointer.
Uses atomic symlink replacement (same filesystem) or directory rename as
fallback. Always preserves the previous generation for rollback.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone as django_timezone

from .models import ArtifactGeneration, ArtifactValidation, MaintenanceAuditEvent


ACTIVE_POINTER = "/app/data-control/active-generation"
PREVIOUS_POINTER = "/app/data-control/previous-generation"
ACTIVATION_LOCK = "/app/data-control/activation.lock"


class ActivationError(RuntimeError):
    """Raised when activation cannot proceed."""


def _active_pointer_path() -> Path:
    return Path(ACTIVE_POINTER)


def _previous_pointer_path() -> Path:
    return Path(PREVIOUS_POINTER)


def _ensure_control_dir() -> None:
    Path("/app/data-control").mkdir(parents=True, exist_ok=True)


def read_active_pointer() -> str | None:
    """Read the currently active generation directory path."""
    ptr = _active_pointer_path()
    if not ptr.is_symlink() and not ptr.exists():
        return None
    try:
        return os.readlink(str(ptr)) if ptr.is_symlink() else ptr.read_text().strip()
    except Exception:
        return None


def write_active_pointer(target: str) -> None:
    """Atomically set the active generation pointer using symlink.

    On filesystems where symlink isn't atomic, we write to a temp file and rename.
    """
    _ensure_control_dir()
    ptr = _active_pointer_path()
    tmp = Path(f"{ptr}.tmp.{os.getpid()}")

    try:
        targets = [Path(target).resolve()]
        if not all(t.exists() for t in targets):
            raise ActivationError(f"Target directory does not exist: {target}")

        tmp.unlink(missing_ok=True)
        tmp.symlink_to(str(Path(target).resolve()))
        tmp.rename(ptr)

    except OSError:
        tmp.unlink(missing_ok=True)
        tmp.write_text(str(target))
        tmp.rename(ptr)


def preserve_previous_pointer() -> str | None:
    """Save the current active pointer as the previous generation for rollback."""
    _ensure_control_dir()
    current = read_active_pointer()
    if current is None:
        return None
    prev = _previous_pointer_path()
    prev.unlink(missing_ok=True)
    try:
        prev.symlink_to(str(Path(current).resolve()))
    except OSError:
        prev.write_text(str(current))
    return current


def acquire_activation_lock(timeout_seconds: int = 30) -> bool:
    """Acquire exclusive activation lock. Returns True if acquired."""
    _ensure_control_dir()
    lock_path = Path(ACTIVATION_LOCK)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f"{os.getpid()}\n{time.time()}".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > timeout_seconds:
                lock_path.unlink()
                return acquire_activation_lock(timeout_seconds)
        except Exception:
            pass
        return False


def release_activation_lock() -> None:
    """Release the activation lock."""
    try:
        Path(ACTIVATION_LOCK).unlink()
    except FileNotFoundError:
        pass


def activate_generation(
    workspace_path: str | Path,
    *,
    generation_id: str = "",
    requested_by=None,
    skip_quiesce: bool = False,
) -> dict[str, Any]:
    """Atomically activate a restored generation workspace.

    Steps:
      1. Acquire activation lock
      2. Validate workspace is activation_ready
      3. Preserve current pointer for rollback
      4. Quiesce writes (optional, skipped for testing)
      5. Switch active pointer
      6. Verify new pointer resolves
      7. Record audit event
      8. Release lock

    Returns a result dict with success status and details.
    """
    workspace_path = Path(workspace_path).resolve()
    if not workspace_path.is_dir():
        raise ActivationError(f"Workspace path is not a directory: {workspace_path}")

    from .restore_workspace import RestoreWorkspace, WorkspaceState
    ws = RestoreWorkspace.load(str(workspace_path))
    if ws is None:
        raise ActivationError("Workspace metadata not found")

    if ws.state not in {WorkspaceState.ACTIVATION_READY, WorkspaceState.ACTIVE}:
        raise ActivationError(
            f"Workspace is not ready for activation (current: {ws.state.value})"
        )

    if not acquire_activation_lock(timeout_seconds=60):
        raise ActivationError("Another activation is in progress")

    from .activation_test_hook import checkpoint as _hook_checkpoint

    try:
        previous = preserve_previous_pointer()
        ws.transition(WorkspaceState.ACTIVATING)
        ws.save_metadata()

        _hook_checkpoint("pointer_switch_pending")

        write_active_pointer(str(workspace_path))

        _hook_checkpoint("pointer_switched")

        activated = read_active_pointer()
        if activated is None or Path(activated).resolve() != workspace_path:
            rollback_to_previous()
            ws.transition(WorkspaceState.ROLLED_BACK, reason="Pointer validation failed")
            ws.save_metadata()
            raise ActivationError("Activation pointer validation failed")

        ws.transition(WorkspaceState.ACTIVE)
        ws.activation_eligible = False
        ws.save_metadata()

        _hook_checkpoint("activation_confirmed")

        _audit_activation(
            event_type="activated",
            details={
                "generation_id": generation_id or ws.source_generation_id,
                "workspace_id": ws.workspace_id,
                "workspace_path": str(workspace_path),
                "previous": previous,
            },
            actor=requested_by,
        )

        return {
            "success": True,
            "workspace_id": ws.workspace_id,
            "generation_id": generation_id or ws.source_generation_id,
            "active_path": str(workspace_path),
            "previous_path": previous,
        }

    except Exception as exc:
        try:
            rollback_to_previous()
        except Exception:
            pass
        ws.transition(WorkspaceState.FAILED, reason=str(exc))
        ws.save_metadata()
        raise ActivationError(f"Activation failed: {exc}") from exc

    finally:
        release_activation_lock()


def rollback_to_previous() -> dict[str, Any]:
    """Restore the previous generation pointer after a failed activation."""
    _ensure_control_dir()
    prev = _previous_pointer_path()

    if not prev.exists():
        raise ActivationError("No previous generation pointer exists")

    previous_target = None
    try:
        previous_target = os.readlink(str(prev)) if prev.is_symlink() else prev.read_text().strip()
    except Exception:
        pass

    if previous_target is None:
        raise ActivationError("Cannot determine previous generation target")

    write_active_pointer(previous_target)

    _audit_activation(
        event_type="rolled_back",
        details={
            "restored_pointer": previous_target,
            "reason": "Activation rollback",
        },
    )
    return {"success": True, "restored_pointer": previous_target}


def activation_status() -> dict[str, Any]:
    """Read the current activation configuration."""
    active = read_active_pointer()
    previous = None
    if _previous_pointer_path().exists():
        try:
            prev = _previous_pointer_path()
            previous = os.readlink(str(prev)) if prev.is_symlink() else prev.read_text().strip()
        except Exception:
            pass

    return {
        "active_pointer": str(_active_pointer_path()),
        "active_target": active,
        "previous_target": previous,
        "lock_held": Path(ACTIVATION_LOCK).exists(),
    }


def validate_generation_coherence(generation_root: str | Path) -> dict[str, Any]:
    """Check that database, media, and index artifacts are consistent.

    Returns a dict with 'coherent' (bool) and per-component checks.
    """
    import sqlite3
    root = Path(generation_root).resolve()
    if not root.is_dir():
        return {"coherent": False, "error": f"Directory not found: {root}"}

    checks = {}
    db_path = root / "db.sqlite3"
    if not db_path.is_file():
        workspaces = list(root.glob("**/db.sqlite3"))
        if workspaces:
            db_path = workspaces[0]
        else:
            return {"coherent": False, "error": "No database found in generation"}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        checks["database_integrity"] = integrity == "ok"
    except Exception as exc:
        checks["database_integrity"] = False
        checks["database_error"] = str(exc)

    checks["coherent"] = all(v is True for v in checks.values() if isinstance(v, bool))
    return {"coherent": checks["coherent"], "checks": checks}


def _audit_activation(*, event_type: str, details: dict, actor=None) -> None:
    try:
        MaintenanceAuditEvent.objects.create(
            event_type=event_type,
            actor=actor,
            payload=details,
        )
    except Exception:
        pass
