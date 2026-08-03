"""Bounded, verified SQLite recovery sets for pre-change safety.

Vault generations are the full recovery authority.  These local sets are a
same-volume emergency aid and intentionally contain databases only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.db.migrations.loader import MigrationLoader

from core.database_ownership import database_alias_for_app

from core.recovery_auth import (
    RecoveryAuthenticationError,
    verify_recovery_superadmin_database,
)

MANIFEST = "recovery-set.json"
REASONS = {"pre-migration", "pre-activation", "pre-bulk-maintenance", "manual"}
DEFAULT_KEEP = 3
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_MAX_BYTES = 5 * 1024**3


class RecoverySetError(RuntimeError):
    """A required recovery point could not be safely created or used."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


@dataclass(frozen=True)
class RecoveryPaths:
    root: Path
    application: Path
    control: Path


def configured_paths() -> RecoveryPaths:
    root = Path(
        getattr(settings, "RECOVERY_SET_ROOT", Path(settings.BACKUP_DIR) / "recovery-sets")
    ).resolve()
    return RecoveryPaths(
        root=root,
        application=Path(settings.DATABASES["default"]["NAME"]).resolve(),
        control=Path(settings.DATABASES["control"]["NAME"]).resolve(),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sqlite_checks(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            {
                "table": row[0],
                "rowid": row[1],
                "parent": row[2],
                "foreign_key": row[3],
            }
            for row in connection.execute("PRAGMA foreign_key_check")
        ]
    finally:
        connection.close()
    if integrity != ["ok"]:
        raise RecoverySetError("sqlite_integrity_failed", "; ".join(integrity[:5]))
    if foreign_keys:
        raise RecoverySetError(
            "sqlite_foreign_keys_failed", f"{len(foreign_keys)} violation(s)"
        )
    return {"integrity": "ok", "foreign_key_violations": 0}


def _backup_database(source: Path, destination: Path) -> dict:
    if not source.is_file():
        raise RecoverySetError("database_missing", str(source))
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        target_connection.close()
        source_connection.close()
    checks = _sqlite_checks(destination)
    _fsync_path(destination)
    return {
        "file": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
        "checks": checks,
    }


def migration_leaves() -> dict[str, list[str]]:
    leaves = {}
    for alias in ("default", "control"):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        leaves[alias] = [
            f"{app}.{name}" for app, name in loader.graph.leaf_nodes()
            if database_alias_for_app(app) == alias
        ]
    return leaves


def required_migrations() -> dict[str, list[str]]:
    """Return every migration the current image routes to each SQLite alias."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    required = {}
    for alias in ("default", "control"):
        required[alias] = sorted(
            f"{app}.{name}"
            for app, name in loader.graph.nodes
            if (alias == "control") == (app == "vaultops")
        )
    return required


def _identity() -> dict:
    environment = getattr(settings, "ENV_IDENTITY", None)
    return {
        "deployment_id": getattr(environment, "deployment_id", ""),
        "dataset_id": getattr(environment, "dataset_id", ""),
        "image_digest": getattr(environment, "app_image_digest", ""),
        "release": getattr(environment, "app_release_version", ""),
        "runtime_generation_id": getattr(settings, "RUNTIME_GENERATION_ID", ""),
        "runtime_manifest_digest": getattr(settings, "RUNTIME_MANIFEST_DIGEST", ""),
    }


def _data_identity(paths: RecoveryPaths) -> dict:
    return {
        "application": {
            "path_fingerprint": hashlib.sha256(
                str(paths.application).encode("utf-8")
            ).hexdigest(),
            "bytes": paths.application.stat().st_size if paths.application.exists() else 0,
            "mtime_ns": paths.application.stat().st_mtime_ns if paths.application.exists() else 0,
        },
        "control": {
            "path_fingerprint": hashlib.sha256(
                str(paths.control).encode("utf-8")
            ).hexdigest(),
            "bytes": paths.control.stat().st_size if paths.control.exists() else 0,
            "mtime_ns": paths.control.stat().st_mtime_ns if paths.control.exists() else 0,
        },
    }


@contextmanager
def _exclusive_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".create.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def list_sets() -> list[dict]:
    root = configured_paths().root
    records = []
    if not root.exists():
        return records
    for manifest_path in root.glob(f"*/{MANIFEST}"):
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["_path"] = str(manifest_path.parent)
        records.append(record)
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)


def verify_set(set_id: str) -> dict:
    if not set_id or set_id != Path(set_id).name:
        raise RecoverySetError("invalid_set_id")
    set_root = configured_paths().root / set_id
    manifest_path = set_root / MANIFEST
    if set_root.is_symlink() or not manifest_path.is_file():
        raise RecoverySetError("recovery_set_not_found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest.get("databases", {}).values():
        path = set_root / record["file"]
        if path.is_symlink() or not path.is_file():
            raise RecoverySetError("recovery_set_incomplete", record["file"])
        if path.stat().st_size != record["bytes"] or _sha256(path) != record["sha256"]:
            raise RecoverySetError("recovery_set_hash_mismatch", record["file"])
        _sqlite_checks(path)
    return {**manifest, "verification_state": "verified"}


def _matching_verified_set(identity: dict, data_identity: dict) -> dict | None:
    for record in list_sets():
        if (
            record.get("verification_state") == "verified"
            and record.get("identity") == identity
            and record.get("data_identity") == data_identity
        ):
            try:
                return verify_set(record["set_id"])
            except RecoverySetError:
                continue
    return None


def create_set(reason: str, *, reuse: bool = True) -> dict:
    if reason not in REASONS:
        raise RecoverySetError("invalid_reason", reason)
    paths = configured_paths()
    with _exclusive_lock(paths.root):
        identity = _identity()
        data_identity = _data_identity(paths)
        if reuse:
            existing = _matching_verified_set(identity, data_identity)
            if existing:
                return {**existing, "reused": True}

        prune = plan_prune()
        if prune["blocked"]:
            raise RecoverySetError("recovery_capacity_degraded", prune["reason"])

        set_id = datetime.now(timezone.utc).strftime("rs-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        temporary = Path(tempfile.mkdtemp(prefix=f".{set_id}-", dir=paths.root))
        published = paths.root / set_id
        try:
            databases = {
                "application": _backup_database(
                    paths.application, temporary / "application.sqlite3"
                )
            }
            if paths.control.is_file():
                databases["control"] = _backup_database(
                    paths.control, temporary / "control.sqlite3"
                )
            manifest = {
                "manifest_version": 1,
                "set_id": set_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "verification_state": "verified",
                "databases": databases,
                "migration_leaves": migration_leaves(),
                "identity": identity,
                "data_identity": data_identity,
                "companion_artifacts": {
                    "media": "referenced-not-copied",
                    "faiss": "rebuild-required",
                    "vault_generation": "canonical-full-recovery",
                },
                "retention": {"incident_hold": False, "hold_reference": ""},
            }
            manifest_path = temporary / MANIFEST
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _fsync_path(manifest_path)
            _fsync_path(temporary)
            temporary.rename(published)
            _fsync_path(paths.root)
            verify_set(set_id)
            return {**manifest, "reused": False}
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def plan_prune(
    *, keep: int = DEFAULT_KEEP, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    records = list_sets()
    now = datetime.now(timezone.utc)
    total = sum(
        sum(db.get("bytes", 0) for db in item.get("databases", {}).values())
        for item in records
    )
    protected = []
    candidates = []
    for index, item in enumerate(records):
        held = bool(item.get("retention", {}).get("incident_hold"))
        created = datetime.fromisoformat(item["created_at"])
        size = sum(db.get("bytes", 0) for db in item.get("databases", {}).values())
        expired = created < now - timedelta(days=max_age_days)
        if held or index < keep:
            protected.append({"set_id": item["set_id"], "bytes": size, "held": held})
        elif expired or total > max_bytes:
            candidates.append({"set_id": item["set_id"], "bytes": size})
            total -= size
    protected_bytes = sum(item["bytes"] for item in protected)
    blocked = protected_bytes > max_bytes
    payload = {
        "created_at": now.isoformat(),
        "candidates": candidates,
        "candidate_bytes": sum(item["bytes"] for item in candidates),
        "protected": protected,
        "protected_bytes": protected_bytes,
        "remaining_bytes": total,
        "blocked": blocked,
        "reason": "protected_sets_exceed_capacity" if blocked else "",
    }
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != "created_at"},
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["plan_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return payload


def apply_prune(plan_id: str) -> dict:
    plan = plan_prune()
    if plan_id != plan["plan_id"]:
        raise RecoverySetError("stale_prune_plan")
    removed = []
    for item in plan["candidates"]:
        target = configured_paths().root / item["set_id"]
        if target.is_symlink():
            raise RecoverySetError("unsafe_recovery_set_path", item["set_id"])
        shutil.rmtree(target)
        removed.append(item)
    return {**plan, "removed": removed}


def prepare_set(set_id: str, target_root: Path) -> dict:
    manifest = verify_set(set_id)
    target = Path(target_root)
    if target.exists() or target.is_symlink():
        raise RecoverySetError("target_exists")
    configured = configured_paths()
    resolved_parent = target.parent.resolve()
    live_roots = {
        Path(settings.DATA_ROOT).resolve(),
        configured.root,
        configured.application.parent,
        configured.control.parent,
    }
    if any(resolved_parent == root or root in resolved_parent.parents for root in live_roots):
        raise RecoverySetError("target_is_active_volume")
    required = sum(db["bytes"] for db in manifest["databases"].values()) * 2
    if shutil.disk_usage(resolved_parent).free < required:
        raise RecoverySetError("insufficient_capacity")
    source = configured.root / set_id
    target.mkdir(mode=0o700)
    try:
        for record in manifest["databases"].values():
            shutil.copy2(source / record["file"], target / record["file"])
        (target / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return validate_workspace(target)


def _applied_migrations(path: Path) -> set[str]:
    connection = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro&immutable=1", uri=True
    )
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "django_migrations" not in tables:
            return set()
        return {
            f"{app}.{name}"
            for app, name in connection.execute(
                "SELECT app, name FROM django_migrations"
            )
        }
    finally:
        connection.close()


def _migration_compatibility(
    *,
    alias: str,
    database_path: Path,
    recorded_leaves: object,
    expected_leaves: list[str],
    required_nodes: list[str],
    known_nodes: set[str],
) -> tuple[dict, list[dict]]:
    recorded = (
        sorted(set(recorded_leaves))
        if isinstance(recorded_leaves, list)
        and all(isinstance(item, str) for item in recorded_leaves)
        else []
    )
    expected = sorted(set(expected_leaves))
    applied = _applied_migrations(database_path)
    blockers = []
    if not recorded:
        blockers.append({"code": "migration_evidence_missing", "scope": alias})
    elif recorded != expected:
        blockers.append(
            {"code": "migration_evidence_incompatible", "scope": alias}
        )
    missing = sorted(set(required_nodes) - applied)
    if missing:
        blockers.append(
            {
                "code": "required_migrations_unapplied",
                "scope": alias,
                "count": len(missing),
            }
        )
    unknown = sorted(applied - known_nodes)
    if unknown:
        blockers.append(
            {
                "code": "unknown_applied_migrations",
                "scope": alias,
                "count": len(unknown),
            }
        )
    return (
        {
            "state": "compatible" if not blockers else "blocked",
            "recorded_leaf_count": len(recorded),
            "expected_leaf_count": len(expected),
            "required_migration_count": len(required_nodes),
            "unapplied_migration_count": len(missing),
            "unknown_applied_migration_count": len(unknown),
        },
        blockers,
    )


def _identity_compatibility(recorded: object) -> tuple[dict, list[dict]]:
    current = _identity()
    recorded = recorded if isinstance(recorded, dict) else {}
    blockers = []
    report = {}
    for field in ("deployment_id", "dataset_id", "image_digest"):
        expected = current.get(field) or ""
        actual = recorded.get(field) or ""
        if not expected or not actual:
            state = "missing"
            blockers.append({"code": f"{field}_missing", "scope": "identity"})
        elif actual != expected:
            state = "incompatible"
            blockers.append(
                {"code": f"{field}_incompatible", "scope": "identity"}
            )
        else:
            state = "compatible"
        report[field] = {"state": state}
    return report, blockers


def _workspace_database_path(root: Path, record: object) -> Path:
    if not isinstance(record, dict):
        raise RecoverySetError("workspace_manifest_invalid")
    filename = record.get("file")
    if (
        not isinstance(filename, str)
        or not filename
        or filename != Path(filename).name
    ):
        raise RecoverySetError("workspace_database_path_unsafe")
    path = root / filename
    configured = configured_paths()
    if (
        path.is_symlink()
        or not path.is_file()
        or path.resolve() in {configured.application, configured.control}
    ):
        raise RecoverySetError("workspace_database_path_unsafe")
    return path


def validate_workspace(workspace: Path) -> dict:
    candidate = Path(workspace)
    if candidate.is_symlink() or not candidate.is_dir():
        raise RecoverySetError("workspace_not_found")
    root = candidate.resolve()
    configured = configured_paths()
    live_roots = {
        Path(settings.DATA_ROOT).resolve(),
        configured.root,
        configured.application.parent,
        configured.control.parent,
    }
    if root in live_roots or any(
        live_root in root.parents for live_root in live_roots
    ):
        raise RecoverySetError("workspace_is_active_volume")
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RecoverySetError("workspace_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        database_records = manifest["databases"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RecoverySetError("workspace_manifest_invalid") from exc
    if not isinstance(database_records, dict):
        raise RecoverySetError("workspace_manifest_invalid")
    checks = {}
    database_paths = {}
    for key, record in database_records.items():
        path = _workspace_database_path(root, record)
        checks[key] = _sqlite_checks(path)
        if (
            not isinstance(record.get("sha256"), str)
            or _sha256(path) != record["sha256"]
        ):
            raise RecoverySetError("workspace_hash_mismatch", key)
        database_paths[key] = path
    if "application" not in database_paths:
        raise RecoverySetError("workspace_application_database_missing")

    blockers = []
    expected_migrations = migration_leaves()
    required_migration_nodes = required_migrations()
    known_migration_nodes = {
        node
        for nodes in required_migration_nodes.values()
        for node in nodes
    }
    migration_report = {}
    for alias, database_key in (("default", "application"), ("control", "control")):
        database_path = database_paths.get(database_key)
        if database_path is None:
            migration_report[alias] = {"state": "blocked"}
            blockers.append(
                {"code": "required_database_missing", "scope": alias}
            )
            continue
        report, migration_blockers = _migration_compatibility(
            alias=alias,
            database_path=database_path,
            recorded_leaves=manifest.get("migration_leaves", {}).get(alias),
            expected_leaves=expected_migrations.get(alias, []),
            required_nodes=required_migration_nodes.get(alias, []),
            known_nodes=known_migration_nodes,
        )
        migration_report[alias] = report
        blockers.extend(migration_blockers)

    identity_report, identity_blockers = _identity_compatibility(
        manifest.get("identity")
    )
    blockers.extend(identity_blockers)

    media_root = Path(settings.MEDIA_ROOT)
    media_missing = []
    app_db = database_paths["application"]
    connection = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "core_pdffile" in tables:
            media_missing = [
                value for (value,) in connection.execute(
                    "SELECT file FROM core_pdffile WHERE file IS NOT NULL AND file != ''"
                )
                if not (media_root / value).is_file()
            ]
    finally:
        connection.close()
    if media_missing:
        blockers.append(
            {
                "code": "referenced_media_missing",
                "scope": "media",
                "count": len(media_missing),
            }
        )

    try:
        recovery_authentication = verify_recovery_superadmin_database(app_db)
    except RecoveryAuthenticationError:
        recovery_authentication = {
            "state": "blocked",
            "reason_code": "recovery_superadmin_unproven",
        }
        blockers.append(
            {
                "code": "recovery_superadmin_unproven",
                "scope": "authentication",
            }
        )

    blockers.append(
        {"code": "index_rebuild_required", "scope": "indexes"}
    )
    return {
        "workspace": str(root),
        "verification_state": "verified" if not blockers else "blocked",
        "database_verification_state": "verified",
        "database_verified": True,
        "recovery_ready": not blockers,
        "blockers": blockers,
        "database_checks": checks,
        "migrations": migration_report,
        "identity_compatibility": identity_report,
        "recovery_authentication": recovery_authentication,
        "referenced_media": {
            "missing_count": len(media_missing),
            "state": "complete" if not media_missing else "incomplete",
        },
        "indexes": {
            "state": "rebuild-required",
            "reason_code": "index_rebuild_required",
        },
        "database_only": True,
    }
