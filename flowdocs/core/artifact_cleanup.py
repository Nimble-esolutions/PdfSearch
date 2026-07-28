"""One conservative planner for local recovery and workspace artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone as django_timezone

MAX_APPLY_BYTES = 20 * 1024**3


class CleanupError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise CleanupError("unsafe_cleanup_inventory_path", str(item))
        if item.is_file():
            total += item.stat().st_size
    return total


def _manifest(path: Path, names: tuple[str, ...]) -> dict:
    for name in names:
        candidate = path / name
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
    return {}


def _records(root: Path, category: str, manifests=()) -> list[dict]:
    if not root.is_dir():
        return []
    records = []
    for path in root.iterdir():
        if path.name.startswith(".") or path.is_symlink():
            continue
        manifest = _manifest(path, tuple(manifests))
        raw_created = manifest.get("created_at")
        try:
            created = datetime.fromisoformat(raw_created) if raw_created else None
        except ValueError:
            created = None
        created = created or datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        )
        records.append(
            {
                "category": category,
                "path": str(path.resolve()),
                "name": path.name,
                "bytes": _tree_bytes(path),
                "created_at": created.isoformat(),
                "state": manifest.get("state", "unknown"),
                "manifest": manifest,
            }
        )
    return records


def _runtime_protections() -> dict:
    generation_reasons = {}
    runtime_path_reasons = {}
    job_public_ids = set()

    def protect_generation(generation_id, reason):
        if generation_id:
            generation_reasons.setdefault(generation_id, set()).add(reason)

    def protect_runtime_path(runtime_path, reason):
        if runtime_path:
            resolved = str(Path(runtime_path).resolve())
            runtime_path_reasons.setdefault(resolved, set()).add(reason)

    try:
        from core.models import MaintenanceJob
        from vaultops.models import (
            ActivationIntent,
            ArtifactGeneration,
            RestoreWorkspace,
            RetentionHold,
            RuntimePointerObservation,
        )

        pointer = RuntimePointerObservation.objects.filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id
        ).order_by("-observed_at").first()
        for key in ("active_generation_id", "previous_generation_id"):
            value = getattr(pointer, key, "") if pointer else ""
            protect_generation(value, "active_or_previous_runtime")

        protected_states = {
            ArtifactGeneration.RuntimeState.PENDING,
            ArtifactGeneration.RuntimeState.APPLYING,
            ArtifactGeneration.RuntimeState.ACTIVE,
            ArtifactGeneration.RuntimeState.PREVIOUS,
            ArtifactGeneration.RuntimeState.ROLLBACK_PENDING,
        }
        for generation_id, runtime_state in ArtifactGeneration.objects.filter(
            runtime_state__in=protected_states
        ).values_list("generation_id", "runtime_state"):
            reason = (
                "active_or_previous_runtime"
                if runtime_state
                in {
                    ArtifactGeneration.RuntimeState.ACTIVE,
                    ArtifactGeneration.RuntimeState.PREVIOUS,
                }
                else "activation_reference"
            )
            protect_generation(generation_id, reason)
        for generation_id in RetentionHold.objects.filter(
            released_at__isnull=True
        ).values_list("generation__generation_id", flat=True):
            protect_generation(generation_id, "incident_hold")
        live_workspace_states = {
            RestoreWorkspace.State.PLANNED,
            RestoreWorkspace.State.DOWNLOADING,
            RestoreWorkspace.State.DOWNLOAD_PAUSED,
            RestoreWorkspace.State.DOWNLOADED,
            RestoreWorkspace.State.VALIDATING,
            RestoreWorkspace.State.SANITIZING,
            RestoreWorkspace.State.MIGRATION_REHEARSAL,
        }
        current_time = django_timezone.now()
        live_workspaces = RestoreWorkspace.objects.filter(
            Q(state__in=live_workspace_states)
            | Q(
                state=RestoreWorkspace.State.ACTIVATION_READY,
                expires_at__gt=current_time,
            )
            | Q(
                state=RestoreWorkspace.State.ACTIVATION_READY,
                expires_at__isnull=True,
            )
        )
        for generation_id, runtime_path in live_workspaces.values_list(
            "generation__generation_id", "runtime_path"
        ):
            protect_generation(generation_id, "workspace_reference")
            protect_runtime_path(runtime_path, "workspace_reference")

        live_intent_states = {
            ActivationIntent.State.PENDING,
            ActivationIntent.State.APPLYING,
        }
        for target, previous in ActivationIntent.objects.filter(
            state__in=live_intent_states,
            expires_at__gt=current_time,
        ).values_list("target_generation_id", "previous_generation_id"):
            for value in (target, previous):
                protect_generation(value, "activation_reference")

        live_jobs = MaintenanceJob.objects.filter(
            status__in={"queued", "running", "paused", "cancel_requested"}
        )
        checkpointed_jobs = MaintenanceJob.objects.filter(
            status="failed", completed_items__gt=0
        )
        job_ids = set(
            live_jobs.values_list("public_id", flat=True)
        ) | set(checkpointed_jobs.values_list("public_id", flat=True))
        job_public_ids.update(str(job_id) for job_id in job_ids)
        for generation_id in ArtifactGeneration.objects.filter(
            lineage_job_public_id__in=job_ids
        ).values_list("generation_id", flat=True):
            protect_generation(generation_id, "current_or_checkpointed_job")
    except Exception as exc:
        raise CleanupError("cleanup_protection_state_unavailable") from exc
    return {
        "generation_reasons": generation_reasons,
        "runtime_path_reasons": runtime_path_reasons,
        "job_public_ids": job_public_ids,
    }


def inventory_local_artifacts() -> list[dict]:
    records = []
    records += _records(
        Path(settings.RECOVERY_SET_ROOT), "recovery_set", ("recovery-set.json",)
    )
    records += _records(
        Path(settings.VAULT_SNAPSHOT_ROOT),
        "source_snapshot",
        ("snapshot.json", "manifest.json"),
    )
    records += _records(
        Path(settings.VAULT_RESTORE_ROOT),
        "quarantine",
        ("workspace.json", "manifest.json"),
    )
    records += _records(
        Path(settings.MAINTENANCE_WORKSPACE_ROOT),
        "maintenance_workspace",
        ("maintenance-candidate.json",),
    )
    records += _records(
        Path(settings.RUNTIME_GENERATIONS_ROOT),
        "runtime_generation",
        (
            "local-generation-manifest.json",
            "runtime-manifest.json",
            "manifest.json",
        ),
    )
    protected_runtime = _runtime_protections()
    for record in records:
        reasons = []
        manifest = record["manifest"]
        if manifest.get("retention", {}).get("incident_hold"):
            reasons.append("incident_hold")
        if record["category"] == "runtime_generation":
            generation = manifest.get("generation_id", record["name"])
            reasons.extend(
                sorted(
                    protected_runtime["generation_reasons"].get(
                        generation, set()
                    )
                    | protected_runtime["runtime_path_reasons"].get(
                        record["path"], set()
                    )
                )
            )
            maintenance_job = manifest.get("maintenance", {}).get(
                "job_public_id", ""
            )
            if maintenance_job in protected_runtime["job_public_ids"]:
                reasons.append("current_or_checkpointed_job")
            if manifest.get("runtime_state") in {"active", "previous"}:
                reasons.append("active_or_previous_runtime")
        if record["category"] == "maintenance_workspace":
            maintenance_job = manifest.get("source", {}).get("job_id", "")
            if maintenance_job in protected_runtime["job_public_ids"]:
                reasons.append("current_or_checkpointed_job")
        if manifest.get("activation_reference"):
            reasons.append("activation_reference")
        if manifest.get("resumable_checkpoint"):
            reasons.append("resumable_checkpoint")
        record["protection_reasons"] = list(dict.fromkeys(reasons))
    return records


def cleanup_plan(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    candidates, protected, retained = [], [], []
    for record in inventory_local_artifacts():
        created = datetime.fromisoformat(record["created_at"])
        age = now - created
        category, state = record["category"], record["state"]
        reason = ""
        if record["protection_reasons"]:
            protected.append(record)
            continue
        if category == "recovery_set":
            retained.append(record)
            continue
        if category == "maintenance_workspace" and age > timedelta(days=7):
            reason = "unactivated_workspace_expired"
        elif category == "quarantine" and age > timedelta(hours=24):
            reason = "verified_quarantine_grace_elapsed"
        elif (
            category == "source_snapshot"
            and state in {"failed", "incomplete"}
            and age > timedelta(hours=24)
        ):
            reason = "failed_diagnostic_payload_expired"
        elif (
            category == "source_snapshot"
            and state in {"verified", "complete"}
            and age > timedelta(hours=24)
        ):
            reason = "verified_source_snapshot_grace_elapsed"
        elif (
            category == "runtime_generation"
            and record["manifest"].get("origin") == "local_maintenance"
            and record["manifest"].get("vault_authority", {}).get("state")
            == "unpublished"
            and age > timedelta(days=7)
        ):
            reason = "unactivated_local_runtime_expired"
        elif category == "runtime_generation":
            retained.append(record)
            continue
        if reason:
            relationship_basis = hashlib.sha256(
                json.dumps(
                    {
                        "category": record["category"],
                        "manifest": record["manifest"],
                        "name": record["name"],
                        "protection_reasons": record["protection_reasons"],
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            candidates.append(
                {
                    **record,
                    "reason_code": reason,
                    "relationship_basis": relationship_basis,
                }
            )
        else:
            retained.append(record)
    basis = {
        "candidates": [
            (
                item["category"],
                item["path"],
                item["bytes"],
                item["reason_code"],
                item["relationship_basis"],
            )
            for item in candidates
        ],
        "protected": [
            (
                item["category"],
                item["path"],
                item["bytes"],
                item["protection_reasons"],
            )
            for item in protected
        ],
    }
    plan_id = hashlib.sha256(
        json.dumps(basis, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    candidate_bytes = sum(item["bytes"] for item in candidates)
    return {
        "plan_id": plan_id,
        "created_at": now.isoformat(),
        "candidates": candidates,
        "candidate_bytes": candidate_bytes,
        "protected": protected,
        "protected_bytes": sum(item["bytes"] for item in protected),
        "retained": retained,
        "retained_bytes": sum(item["bytes"] for item in retained),
        "apply_limit_bytes": MAX_APPLY_BYTES,
        "apply_allowed": candidate_bytes <= MAX_APPLY_BYTES,
    }


def apply_cleanup(plan_id: str) -> dict:
    plan = cleanup_plan()
    if plan["plan_id"] != plan_id:
        raise CleanupError("stale_cleanup_plan")
    if not plan["apply_allowed"]:
        raise CleanupError("cleanup_exceeds_20_gib_approval_boundary")
    allowed_roots = {
        Path(settings.RECOVERY_SET_ROOT).resolve(),
        Path(settings.VAULT_SNAPSHOT_ROOT).resolve(),
        Path(settings.VAULT_RESTORE_ROOT).resolve(),
        Path(settings.MAINTENANCE_WORKSPACE_ROOT).resolve(),
        Path(settings.RUNTIME_GENERATIONS_ROOT).resolve(),
    }
    removed = []
    for item in plan["candidates"]:
        fresh_plan = cleanup_plan()
        if not fresh_plan["apply_allowed"]:
            raise CleanupError("cleanup_exceeds_20_gib_approval_boundary")
        fresh_item = next(
            (
                candidate
                for candidate in fresh_plan["candidates"]
                if candidate["path"] == item["path"]
            ),
            None,
        )
        comparison_fields = (
            "category",
            "path",
            "bytes",
            "reason_code",
            "relationship_basis",
        )
        if fresh_item is None or any(
            fresh_item[field] != item[field] for field in comparison_fields
        ):
            raise CleanupError("stale_cleanup_plan")
        path = Path(item["path"])
        if path.is_symlink() or path.parent.resolve() not in allowed_roots:
            raise CleanupError("unsafe_cleanup_path", str(path))
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
        removed.append(
            {
                "category": item["category"],
                "path": item["path"],
                "bytes": item["bytes"],
            }
        )
    return {**plan, "removed": removed}


def capacity_report(
    *,
    source_bytes: int,
    operation: str,
    target_root: Path | str | None = None,
    minimum_free_bytes: int = 0,
    minimum_free_inodes: int = 0,
) -> dict:
    factors = {
        "source_copy": source_bytes,
        "quarantine": source_bytes if operation == "restore" else 0,
        "runtime_copy": source_bytes if operation in {"restore", "activation"} else 0,
        "maintenance_copy": source_bytes if operation == "maintenance" else 0,
        "sanitized_or_rehearsal_database": int(source_bytes * 0.25),
        "index_build_temporaries": int(source_bytes * 0.5),
        "filesystem_overhead": int(source_bytes * 0.1),
        "operational_reserve": 256 * 1024**2,
    }
    required = max(sum(factors.values()), int(minimum_free_bytes))
    target = Path(target_root or settings.DATA_CONTROL_ROOT)
    usage = shutil.disk_usage(target)
    try:
        filesystem = os.statvfs(target)
        free_inodes = filesystem.f_favail
        inode_reporting_available = int(filesystem.f_files) > 0
        free_inodes = (
            int(filesystem.f_favail)
            if inode_reporting_available
            else None
        )
        inode_reserve = max(
            int(minimum_free_inodes),
            1024 if inode_reporting_available else 0,
            int(filesystem.f_files * 0.01),
        )
    except (AttributeError, OSError):
        free_inodes = None
        inode_reporting_available = False
        inode_reserve = int(minimum_free_inodes)
    return {
        "operation": operation,
        "phases": factors,
        "required_bytes": required,
        "free_bytes": usage.free,
        "byte_capacity_ok": usage.free >= required,
        "free_inodes": free_inodes,
        "inode_reserve": inode_reserve,
        "inode_check": (
            "reported" if inode_reporting_available else "not_reported"
        ),
        "inode_capacity_ok": (
            (
                free_inodes is not None
                and free_inodes >= inode_reserve
            )
            or (
                free_inodes is None
                and int(minimum_free_inodes) == 0
            )
        ),
    }
