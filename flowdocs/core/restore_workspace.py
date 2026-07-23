"""Restore workspace state machine.

Each restore operation creates a workspace that progresses through
explicit, audited states from download to activation. The workspace
is always outside the active data tree and never partially overwrites
active data.
"""

from __future__ import annotations

import enum
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone as django_timezone

from .models import ArtifactGeneration, ArtifactValidation, MaintenanceJob, MaintenanceAuditEvent


class WorkspaceState(enum.Enum):
    CREATED = "created"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    SOURCE_VALIDATED = "source_validated"
    PREFLIGHT_PASSED = "preflight_passed"
    SANITIZING = "sanitizing"
    SANITIZED = "sanitized"
    MIGRATION_REHEARSAL = "migration_rehearsal"
    MIGRATION_READY = "migration_ready"
    APPLICATION_VALIDATED = "application_validated"
    ACTIVATION_READY = "activation_ready"
    ACTIVATING = "activating"
    ACTIVE = "active"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    EXPIRED = "expired"
    PURGED = "purged"


class RestoreMode(enum.Enum):
    EMPTY = "empty"
    SEED = "seed"
    PINNED = "pinned"
    LATEST_COMPATIBLE = "latest-compatible"
    SANITIZED_PRODUCTION = "sanitized-production"
    EXACT_PRODUCTION = "exact-production"
    DISASTER_RECOVERY = "disaster-recovery"


VALID_TRANSITIONS: dict[WorkspaceState, set[WorkspaceState]] = {
    WorkspaceState.CREATED: {WorkspaceState.DOWNLOADING, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.DOWNLOADING: {WorkspaceState.DOWNLOADED, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.DOWNLOADED: {WorkspaceState.SOURCE_VALIDATED, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.SOURCE_VALIDATED: {WorkspaceState.PREFLIGHT_PASSED, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.PREFLIGHT_PASSED: {WorkspaceState.SANITIZING, WorkspaceState.MIGRATION_REHEARSAL, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.SANITIZING: {WorkspaceState.SANITIZED, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.SANITIZED: {WorkspaceState.MIGRATION_REHEARSAL, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.MIGRATION_REHEARSAL: {WorkspaceState.MIGRATION_READY, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.MIGRATION_READY: {WorkspaceState.APPLICATION_VALIDATED, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.APPLICATION_VALIDATED: {WorkspaceState.ACTIVATION_READY, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.ACTIVATION_READY: {WorkspaceState.ACTIVATING, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.ACTIVATING: {WorkspaceState.ACTIVE, WorkspaceState.ROLLED_BACK, WorkspaceState.FAILED, WorkspaceState.PURGED},
    WorkspaceState.ACTIVE: {WorkspaceState.ROLLED_BACK, WorkspaceState.EXPIRED, WorkspaceState.PURGED},
    WorkspaceState.ROLLED_BACK: {WorkspaceState.FAILED, WorkspaceState.EXPIRED, WorkspaceState.PURGED},
    WorkspaceState.FAILED: {WorkspaceState.EXPIRED, WorkspaceState.PURGED},
    WorkspaceState.EXPIRED: {WorkspaceState.PURGED},
    WorkspaceState.PURGED: set(),
}


@dataclass
class RestoreWorkspace:
    """A restoration workspace with audited lifecycle."""

    workspace_id: str
    restore_job_id: str
    source_dataset_id: str
    source_generation_id: str
    source_manifest_sha256: str = ""
    target_dataset_id: str = ""
    target_environment: str = ""
    restore_mode: RestoreMode = RestoreMode.PINNED
    sanitization_policy: str = ""
    state: WorkspaceState = WorkspaceState.CREATED
    created_at: str = ""
    updated_at: str = ""
    local_path: str = ""
    validation_results: list[dict] = field(default_factory=list)
    migration_rehearsal_result: dict | None = None
    activation_eligible: bool = False
    failure_reason: str = ""
    cleanup_eligible: bool = False
    derived_manifest: dict | None = None
    rollback_image_safe: str = "unknown"
    job: Any = None

    def transition(self, new_state: WorkspaceState, *, reason: str = "") -> None:
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise WorkspaceError(
                f"Cannot transition from {self.state.value} to {new_state.value}"
            )
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc).isoformat()
        if new_state == WorkspaceState.FAILED and reason:
            self.failure_reason = reason

    def is_terminal(self) -> bool:
        return self.state in {
            WorkspaceState.ACTIVE, WorkspaceState.ROLLED_BACK,
            WorkspaceState.FAILED, WorkspaceState.EXPIRED, WorkspaceState.PURGED,
        }

    def dirs(self) -> dict[str, Path]:
        root = Path(self.local_path)
        return {
            "root": root,
            "source": root / "source",
            "working": root / "working",
            "rehearsal": root / "rehearsal",
            "reports": root / "reports",
        }

    def save_metadata(self) -> None:
        meta = {
            "workspace_id": self.workspace_id,
            "restore_job_id": self.restore_job_id,
            "source_dataset_id": self.source_dataset_id,
            "source_generation_id": self.source_generation_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "target_dataset_id": self.target_dataset_id,
            "target_environment": self.target_environment,
            "restore_mode": self.restore_mode.value,
            "sanitization_policy": self.sanitization_policy,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "failure_reason": self.failure_reason,
            "activation_eligible": self.activation_eligible,
        }
        reports = self.dirs()["reports"]
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "workspace.json").write_text(json.dumps(meta, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> "RestoreWorkspace | None":
        meta_path = Path(path) / "reports" / "workspace.json"
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text())
            mode = RestoreMode(data.get("restore_mode", "pinned"))
            state = WorkspaceState(data.get("state", "created"))
            return cls(
                workspace_id=data.get("workspace_id", ""),
                restore_job_id=data.get("restore_job_id", ""),
                source_dataset_id=data.get("source_dataset_id", ""),
                source_generation_id=data.get("source_generation_id", ""),
                source_manifest_sha256=data.get("source_manifest_sha256", ""),
                target_dataset_id=data.get("target_dataset_id", ""),
                target_environment=data.get("target_environment", ""),
                restore_mode=mode,
                sanitization_policy=data.get("sanitization_policy", ""),
                state=state,
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                local_path=str(Path(path)),
                failure_reason=data.get("failure_reason", ""),
                activation_eligible=data.get("activation_eligible", False),
            )
        except Exception:
            return None


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation is invalid or unsafe."""


def create_workspace(
    workspace_id: str,
    restore_job_id: str,
    source_dataset_id: str,
    source_generation_id: str,
    *,
    target_dataset_id: str = "",
    target_environment: str = "",
    restore_mode: RestoreMode = RestoreMode.PINNED,
    sanitization_policy: str = "",
    source_manifest_sha256: str = "",
) -> RestoreWorkspace:
    base = Path(settings.BACKUP_DIR) / "restore-workspaces"
    base.mkdir(parents=True, exist_ok=True)
    local = base / workspace_id
    if local.exists():
        existing = RestoreWorkspace.load(str(local))
        if existing and existing.is_terminal():
            return existing
        shutil.rmtree(str(local))

    dirs_to_create = ["source", "working", "rehearsal", "reports"]
    for d in dirs_to_create:
        (local / d).mkdir(parents=True)
    os.chmod(local, 0o750)

    now = datetime.now(timezone.utc).isoformat()
    ws = RestoreWorkspace(
        workspace_id=workspace_id,
        restore_job_id=restore_job_id,
        source_dataset_id=source_dataset_id,
        source_generation_id=source_generation_id,
        source_manifest_sha256=source_manifest_sha256,
        target_dataset_id=target_dataset_id,
        target_environment=target_environment,
        restore_mode=restore_mode,
        sanitization_policy=sanitization_policy,
        state=WorkspaceState.CREATED,
        created_at=now,
        updated_at=now,
        local_path=str(local),
    )
    ws.save_metadata()
    return ws


def validate_workspace_paths(ws: RestoreWorkspace) -> list[str]:
    errors: list[str] = []
    root = Path(ws.local_path).resolve()
    for name in ("source", "working", "rehearsal", "reports"):
        p = root / name
        if not p.exists():
            errors.append(f"Missing directory: {name}")
        resolved = p.resolve()
        if not str(resolved).startswith(str(root)):
            errors.append(f"Path traversal detected: {name}")
    active_root = Path(getattr(settings, "DATA_ROOT", "/app/data")).resolve()
    if str(root).startswith(str(active_root)):
        errors.append("Workspace must be outside the active data tree")
    return errors
