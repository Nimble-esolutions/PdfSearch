"""One conservative planner for local recovery and workspace artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings

MAX_APPLY_BYTES = 20 * 1024**3


class CleanupError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


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


def _runtime_protections() -> set[str]:
    protected = set()
    try:
        from vaultops.models import RuntimePointerObservation

        pointer = RuntimePointerObservation.objects.filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id
        ).order_by("-observed_at").first()
        for key in ("active_generation_id", "previous_generation_id"):
            value = getattr(pointer, key, "") if pointer else ""
            if value:
                protected.add(value)
    except Exception:
        pass
    return protected


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
        ("runtime-manifest.json", "manifest.json"),
    )
    protected_runtime = _runtime_protections()
    for record in records:
        reasons = []
        manifest = record["manifest"]
        if manifest.get("retention", {}).get("incident_hold"):
            reasons.append("incident_hold")
        if record["category"] == "runtime_generation":
            generation = manifest.get("generation_id", record["name"])
            if generation in protected_runtime:
                reasons.append("active_or_previous_runtime")
            if manifest.get("runtime_state") in {"active", "previous"}:
                reasons.append("active_or_previous_runtime")
        if (
            record["category"] == "maintenance_workspace"
            and manifest.get("state") == "activation_ready"
        ):
            reasons.append("maintenance_candidate")
        if manifest.get("activation_reference"):
            reasons.append("activation_reference")
        if manifest.get("resumable_checkpoint"):
            reasons.append("resumable_checkpoint")
        record["protection_reasons"] = reasons
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
        elif category == "runtime_generation":
            retained.append(record)
            continue
        if reason:
            candidates.append({**record, "reason_code": reason})
        else:
            retained.append(record)
    basis = {
        "candidates": [
            (item["category"], item["path"], item["bytes"], item["reason_code"])
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
    target.mkdir(parents=True, exist_ok=True)
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
