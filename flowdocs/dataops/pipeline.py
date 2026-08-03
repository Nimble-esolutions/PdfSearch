"""The single automatic Data Operations execution pipeline.

The pipeline is intentionally usable without Django models.  Storage clients,
lease callbacks, and reindex callbacks are injectable, which makes the source
and destination matrix testable against a small S3-compatible fake while the
maintenance worker can use the same implementation in production.

Every mutating path follows the same evidence order::

    preflight -> lease -> transfer -> manifest verification -> reconciliation
    -> reindex -> health check -> publish receipt

Restore activation is an atomic symlink/pointer swap.  A failed stage leaves
the previous active generation untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .config import (
    ConfigurationIssue,
    ResolvedProfile,
    profile_map,
    resolve_profiles,
    resolve_selectors,
    validate_operation_route,
)
from .package import PackageContractError, build_manifest, manifest_digest, validate_manifest
from .recovery import RecoveryInspectionError, inspect_manifest
from .storage import (
    StorageConfigurationError,
    StorageObservationError,
    generation_manifest_key,
    generation_prefix,
    profile_namespace,
    validate_endpoint,
)


PIPELINE_STAGES = (
    "preflight",
    "lease",
    "transfer",
    "manifest_verification",
    "reconciliation",
    "reindex",
    "health_check",
    "publish_receipt",
)
RETRYABLE_CODES = frozenset(
    {
        "source_read_failed",
        "destination_write_failed",
        "destination_verify_failed",
        "transfer_retryable",
        "lease_busy",
        "reindex_retryable",
        "health_check_retryable",
    }
)

_OPERATION_ALIASES = {"clone/rebind": "clone_rebind"}


class DataOpsPipelineError(RuntimeError):
    """A safe, non-secret pipeline failure."""

    def __init__(self, code: str, *, stage: str = "", retryable: bool | None = None, details: Mapping[str, Any] | None = None):
        self.code = str(code)
        self.stage = stage
        self.retryable = self.code in RETRYABLE_CODES if retryable is None else bool(retryable)
        self.details = dict(details or {})
        super().__init__(self.code)


@dataclass(frozen=True)
class OperationRoute:
    operation: str
    source: ResolvedProfile | None
    destination: ResolvedProfile | None
    source_key: str = ""
    destination_key: str = ""
    local_dataset_id: str = ""

    @property
    def same_bucket(self) -> bool:
        return bool(
            self.source
            and self.destination
            and self.source.endpoint.rstrip("/").lower() == self.destination.endpoint.rstrip("/").lower()
            and self.source.bucket == self.destination.bucket
            and self.source.dataset_id == self.destination.dataset_id
        )

    @property
    def namespace(self) -> str:
        if self.destination:
            return profile_namespace(self.destination, fallback_to_key=bool(self.destination.namespace))
        if self.source:
            return profile_namespace(self.source, fallback_to_key=bool(self.source.namespace))
        return "local"


@dataclass(frozen=True)
class PreflightResult:
    route: OperationRoute
    release_id: str
    issues: tuple[ConfigurationIssue, ...] = ()
    safety_backup_required: bool = False
    capacity: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    def redacted(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "operation": self.route.operation,
            "source_profile": self.route.source.key if self.route.source else "local",
            "destination_profile": self.route.destination.key if self.route.destination else "local",
            "source_bucket": self.route.source.bucket if self.route.source else "local",
            "destination_bucket": self.route.destination.bucket if self.route.destination else "local",
            "source_dataset": self.route.source.dataset_id if self.route.source else self.route.local_dataset_id,
            "destination_dataset": self.route.destination.dataset_id if self.route.destination else self.route.local_dataset_id,
            "namespace": self.route.namespace,
            "release_id": self.release_id,
            "same_bucket": self.route.same_bucket,
            "safety_backup_required": self.safety_backup_required,
            "capacity": dict(self.capacity),
            "issues": [issue.__dict__ for issue in self.issues],
        }


@dataclass
class ReindexBudget:
    per_run: int = 500
    per_day: int = 5000
    used_run: int = 0
    used_day: int = 0

    def take(self, requested: int) -> int:
        amount = max(0, min(int(requested), self.per_run - self.used_run, self.per_day - self.used_day))
        self.used_run += amount
        self.used_day += amount
        return amount


def _safe_release_id(value: str | None = None) -> str:
    release = str(value or "").strip()
    if not release:
        release = "release-" + uuid.uuid4().hex
    if len(release) > 160 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", release) or ".." in release:
        raise DataOpsPipelineError("release_id_invalid", stage="preflight")
    return release


def clone_destination_generation_id(source_generation_id: str, destination_generation_id: str = "") -> str:
    """Return the deterministic destination id used by clone/rebind."""
    supplied = str(destination_generation_id or "").strip()
    if supplied:
        return supplied
    source = str(source_generation_id or "").strip()
    candidate = f"clone-{source}"
    if len(candidate) <= 160:
        return candidate
    return "clone-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def clone_confirmation_phrase(
    source_profile: str,
    source_generation_id: str,
    destination_profile: str,
    destination_generation_id: str,
    clone_reason: str = "stage-rehearsal",
) -> str:
    """Build the exact human confirmation required for a cross-dataset clone."""
    return (
        f"CLONE {str(source_profile).strip().lower()}:{str(source_generation_id).strip()} "
        f"-> {str(destination_profile).strip().lower()}:{str(destination_generation_id).strip()} "
        f"/ {str(clone_reason or 'stage-rehearsal').strip()}"
    )


def resolve_operation_route(
    operation: str,
    profiles: Iterable[ResolvedProfile],
    *,
    selectors: Mapping[str, str] | None = None,
    source_profile: str = "",
    destination_profile: str = "",
    local_dataset_id: str = "",
) -> OperationRoute:
    values = profile_map(profiles)
    selectors = selectors or resolve_selectors()
    operation = operation.strip().lower()
    operation = _OPERATION_ALIASES.get(operation, operation)
    if operation not in {"backup", "restore", "copy", "transfer", "clone_rebind"}:
        raise DataOpsPipelineError("operation_invalid", stage="preflight")
    if operation == "backup":
        destination_key = (destination_profile or selectors.get("backup_destination") or selectors.get("backup") or "").strip().lower()
        source_key = source_profile.strip().lower()
        source = values.get(source_key) if source_key else None
        destination = values.get(destination_key) if destination_key else None
    elif operation == "restore":
        source_key = (source_profile or selectors.get("restore_source") or selectors.get("restore") or "").strip().lower()
        destination_key = (destination_profile or selectors.get("restore_destination") or "").strip().lower()
        source = values.get(source_key) if source_key else None
        destination = values.get(destination_key) if destination_key else None
    else:
        source_key = source_profile.strip().lower()
        destination_key = destination_profile.strip().lower()
        source = values.get(source_key) if source_key else None
        destination = values.get(destination_key) if destination_key else None
    return OperationRoute(operation, source, destination, source_key, destination_key, local_dataset_id)


def _issue(code: str, message: str, field: str = "") -> ConfigurationIssue:
    return ConfigurationIssue(code, message, field)


def preflight_operation(
    operation: str,
    source: ResolvedProfile | None,
    destination: ResolvedProfile | None,
    *,
    release_id: str = "",
    local_dataset_id: str = "",
    active_root: str | os.PathLike[str] | None = None,
    source_client: Any = None,
    destination_client: Any = None,
    require_permissions: bool = False,
    source_generation_id: str = "",
    destination_generation_id: str = "",
    confirmation: str = "",
    clone_reason: str = "stage-rehearsal",
) -> PreflightResult:
    """Return an exact, secret-free operation preview or typed issues."""
    operation = _OPERATION_ALIASES.get(operation.strip().lower(), operation.strip().lower())
    route = OperationRoute(operation, source, destination, source.key if source else "", destination.key if destination else "", local_dataset_id)
    issues = list(validate_operation_route(source, destination, operation=route.operation, local_dataset_id=local_dataset_id))
    if route.operation in {"restore", "copy", "transfer"} and not str(release_id or "").strip():
        issues.append(_issue("release_id_missing", "A release ID is required for this operation", "release_id"))
    if route.operation == "clone_rebind":
        source_generation_id = str(source_generation_id or "").strip()
        destination_generation_id = clone_destination_generation_id(source_generation_id, destination_generation_id)
        if not source_generation_id:
            issues.append(_issue("source_generation_id_missing", "A source generation is required for clone/rebind", "source_generation_id"))
        if source and destination and source.dataset_id == destination.dataset_id:
            issues.append(_issue("clone_requires_distinct_dataset", "Clone/rebind requires distinct source and destination datasets", "dataset_id"))
        expected = clone_confirmation_phrase(
            source.key if source else "",
            source_generation_id,
            destination.key if destination else "",
            destination_generation_id,
            clone_reason,
        )
        if confirmation != expected:
            issues.append(_issue("clone_confirmation_required", "Type the exact clone/rebind confirmation phrase", "confirmation"))
        release_id = release_id or destination_generation_id
    for profile, field in ((source, "source"), (destination, "destination")):
        if profile and profile.enabled and not profile.endpoint:
            issues.append(_issue("endpoint_missing", f"{field.title()} profile {profile.key} has no endpoint", f"{field}_endpoint"))
        if profile and not profile.enabled:
            issues.append(_issue("profile_disabled", f"Profile {profile.key} is disabled", field))
    try:
        if source and source.endpoint:
            validate_endpoint(source.endpoint, allow_http=True)
        if destination and destination.endpoint:
            validate_endpoint(destination.endpoint, allow_http=True)
    except StorageConfigurationError as exc:
        issues.append(_issue("endpoint_invalid", str(exc), "endpoint"))
    if route.same_bucket and source and destination and profile_namespace(source, fallback_to_key=bool(source.namespace)) == profile_namespace(destination, fallback_to_key=bool(destination.namespace)):
        issues.append(_issue("same_bucket_namespace_collision", "Same-bucket source and destination must use distinct namespaces", "namespace"))
    if route.operation in {"restore", "copy", "transfer"} and source and destination and source.dataset_id != destination.dataset_id:
        issues.append(_issue("dataset_mismatch", "Source and destination datasets do not match", "dataset_id"))
    if route.operation == "restore" and source and local_dataset_id and source.dataset_id != local_dataset_id:
        issues.append(_issue("source_dataset_mismatch", "Restore source dataset does not match the active dataset", "source_dataset_id"))
    if route.operation == "backup" and destination and local_dataset_id and destination.dataset_id != local_dataset_id:
        issues.append(_issue("destination_dataset_mismatch", "Backup destination dataset does not match the active dataset", "destination_dataset_id"))
    if require_permissions:
        try:
            from .storage import probe_profile_access

            if source and source_client:
                probe_profile_access(source_client, source, require_write=False)
            if destination and destination_client:
                probe_profile_access(destination_client, destination, require_write=True)
        except StorageObservationError as exc:
            issues.append(_issue(str(exc), "Object-store permissions do not satisfy this operation", "permissions"))
    capacity: dict[str, Any] = {}
    if active_root:
        root = Path(active_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            stat = os.statvfs(root)
            capacity = {"free_bytes": stat.f_bavail * stat.f_frsize, "free_inodes": stat.f_favail}
            # Some container-backed filesystems report f_files=f_favail=0 to
            # mean inode accounting is unavailable, not exhausted.
            inodes_exhausted = stat.f_files > 0 and stat.f_favail <= 0
            if capacity["free_bytes"] <= 0 or inodes_exhausted:
                issues.append(_issue("destination_capacity_insufficient", "Destination has no free space or inodes", "capacity"))
        except OSError:
            issues.append(_issue("destination_capacity_unavailable", "Destination capacity could not be measured", "capacity"))
    active_exists = bool(active_root and (Path(active_root) / "active").exists())
    return PreflightResult(route, _safe_release_id(release_id), tuple(issues), active_exists and route.operation == "restore" and destination is None, capacity)


def _provider_code(exc: Exception) -> str:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    return str(error.get("Code", ""))


def _read_object(client: Any, *, bucket: str, key: str, max_bytes: int = 2 * 1024 * 1024 * 1024) -> bytes:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = body.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise DataOpsPipelineError("object_exceeds_limit", stage="transfer")
            chunks.append(chunk)
        return b"".join(chunks)
    except DataOpsPipelineError:
        raise
    except Exception as exc:
        code = _provider_code(exc)
        raise DataOpsPipelineError("source_permission_denied" if code in {"403", "AccessDenied"} else "source_read_failed", stage="transfer", retryable=code not in {"403", "AccessDenied", "404", "NoSuchKey", "NotFound"}) from exc


def _put_object(client: Any, *, bucket: str, key: str, body: bytes) -> None:
    kwargs = {"Bucket": bucket, "Key": key, "Body": body, "Metadata": {"sha256": hashlib.sha256(body).hexdigest(), "immutable": "true"}}
    try:
        client.put_object(**kwargs, IfNoneMatch="*")
    except TypeError:
        try:
            client.put_object(**kwargs)
        except Exception as exc:
            raise DataOpsPipelineError("destination_write_failed", stage="transfer", retryable=True) from exc
    except Exception as exc:
        code = _provider_code(exc)
        if code in {"PreconditionFailed", "412"}:
            return
        raise DataOpsPipelineError("destination_write_failed", stage="transfer", retryable=code not in {"403", "AccessDenied"}) from exc


def _relative_object_key(key: str, source_prefix: str) -> str:
    value = str(key or "").strip().lstrip("/")
    if value.startswith(source_prefix):
        value = value[len(source_prefix):]
    normalized = posixpath.normpath(value)
    if (
        not value
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or "\\" in value
    ):
        raise DataOpsPipelineError("object_key_unsafe", stage="transfer")
    return normalized


def _load_manifest(client: Any, profile: ResolvedProfile, release_id: str) -> tuple[dict[str, Any], str, bool]:
    keys = [generation_manifest_key(profile, release_id, legacy=not bool(profile.namespace))]
    if profile.namespace:
        keys.append(generation_manifest_key(profile, release_id, legacy=True))
    last: Exception | None = None
    for key in keys:
        try:
            raw = _read_object(client, bucket=profile.bucket, key=key, max_bytes=8 * 1024 * 1024)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest is not an object")
            try:
                validate_manifest(payload)
            except PackageContractError:
                inspect_manifest(payload)
            inspection = inspect_manifest(payload)
            if inspection.release_id != release_id or inspection.dataset_id != profile.dataset_id:
                raise DataOpsPipelineError("manifest_identity_mismatch", stage="manifest_verification", retryable=False)
            return payload, key, key == keys[-1] and len(keys) > 1
        except DataOpsPipelineError as exc:
            if exc.code == "manifest_identity_mismatch":
                raise
            last = exc
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecoveryInspectionError) as exc:
            raise DataOpsPipelineError("manifest_invalid", stage="manifest_verification") from exc
    raise DataOpsPipelineError("source_manifest_missing", stage="manifest_verification", retryable=False) from last


def _manifest_entries(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification")
    normalized: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification")
        key = str(item.get("key") or item.get("object_key") or "").strip()
        digest = str(item.get("sha256") or "").strip().lower()
        if not key or (payload.get("format_version") == 2 and len(digest) != 64):
            raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification")
        normalized.append({**dict(item), "key": key, "sha256": digest, "path": str(item.get("path") or "").strip()})
    return normalized


def transfer_generation(
    source_client: Any,
    source: ResolvedProfile,
    destination_client: Any,
    destination: ResolvedProfile,
    release_id: str,
) -> dict[str, Any]:
    """Copy one verified generation between any two configured profiles."""
    payload, source_manifest_key, legacy_source = _load_manifest(source_client, source, release_id)
    entries = _manifest_entries(payload)
    source_prefix = source_manifest_key.rsplit("manifest.json", 1)[0]
    destination_prefix = generation_prefix(destination, release_id, legacy=not bool(destination.namespace))
    destination_files: list[dict[str, Any]] = []
    total_bytes = 0
    for item in entries:
        source_key = item["key"]
        if "/" not in source_key:
            source_key = source_prefix + source_key
        data = _read_object(source_client, bucket=source.bucket, key=source_key)
        digest = hashlib.sha256(data).hexdigest()
        if item["sha256"] and digest != item["sha256"]:
            raise DataOpsPipelineError("manifest_checksum_mismatch", stage="manifest_verification", retryable=False)
        relative = _relative_object_key(source_key, source_prefix)
        destination_key = destination_prefix + relative
        _put_object(destination_client, bucket=destination.bucket, key=destination_key, body=data)
        try:
            verified = _read_object(destination_client, bucket=destination.bucket, key=destination_key)
        except DataOpsPipelineError as exc:
            raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=True) from exc
        if hashlib.sha256(verified).hexdigest() != digest:
            raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False)
        destination_files.append({**item, "key": destination_key, "bytes": len(data), "sha256": digest})
        total_bytes += len(data)

    source_info = payload.get("source") if isinstance(payload.get("source"), Mapping) else {}
    rebased = {
        "format_version": 2,
        "release_id": release_id,
        "dataset_id": destination.dataset_id,
        "source": {
            **dict(source_info),
            "profile": source.key,
            "source_id": source.source_id,
            "transferred_from": source.bucket,
        },
        "identity": dict(payload.get("identity") or {}),
        "counts": {**dict(payload.get("counts") or {}), "objects": len(destination_files), "bytes": total_bytes},
        "files": destination_files,
        "evidence": {
            "pipeline": "dataops",
            "source_profile": source.key,
            "destination_profile": destination.key,
            "legacy_source": legacy_source,
        },
    }
    validate_manifest(rebased)
    raw = json.dumps(rebased, sort_keys=True, separators=(",", ":")).encode("utf-8")
    destination_manifest_key = destination_prefix + "manifest.json"
    _put_object(destination_client, bucket=destination.bucket, key=destination_manifest_key, body=raw)
    verified_manifest = _read_object(destination_client, bucket=destination.bucket, key=destination_manifest_key)
    if hashlib.sha256(verified_manifest).hexdigest() != hashlib.sha256(raw).hexdigest():
        raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False)
    return {
        "release_id": release_id,
        "manifest": rebased,
        "manifest_digest": manifest_digest(rebased),
        "manifest_key": destination_manifest_key,
        "objects": len(destination_files),
        "bytes": total_bytes,
        "source_profile": source.key,
        "destination_profile": destination.key,
    }


def _iter_local_files(root: Path) -> Iterable[tuple[str, Path]]:
    excluded = {"data-control", "restore-quarantine", "runtime-generations", ".git"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in excluded or part.startswith(".") for part in path.relative_to(root).parts):
            continue
        yield path.relative_to(root).as_posix(), path


def publish_local_backup(
    destination_client: Any,
    destination: ResolvedProfile,
    *,
    source_root: str | os.PathLike[str],
    release_id: str,
    files: Iterable[tuple[str, bytes]] | None = None,
) -> dict[str, Any]:
    """Publish a stable local file set as a verified v2 generation."""
    root = Path(source_root).resolve()
    if files is None:
        source_files = ((relative, path.read_bytes()) for relative, path in _iter_local_files(root))
    else:
        source_files = ((str(relative).strip().lstrip("/"), bytes(data)) for relative, data in files)
    prefix = generation_prefix(destination, release_id, legacy=not bool(destination.namespace))
    manifest_files: list[dict[str, Any]] = []
    total = 0
    for relative, data in source_files:
        if (
            not relative
            or posixpath.normpath(relative) in {"", ".", ".."}
            or posixpath.normpath(relative).startswith("../")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or "\\" in relative
        ):
            raise DataOpsPipelineError("source_path_unsafe", stage="transfer", retryable=False)
        digest = hashlib.sha256(data).hexdigest()
        key = prefix + relative
        _put_object(destination_client, bucket=destination.bucket, key=key, body=data)
        verified = _read_object(destination_client, bucket=destination.bucket, key=key)
        if hashlib.sha256(verified).hexdigest() != digest:
            raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False)
        manifest_files.append({"key": key, "path": relative, "sha256": digest, "bytes": len(data)})
        total += len(data)
    manifest = build_manifest(
        release_id=release_id,
        dataset_id=destination.dataset_id,
        source={"profile": "local", "source_id": destination.source_id},
        identity={"pipeline": "dataops"},
        counts={"objects": len(manifest_files), "bytes": total},
        files=manifest_files,
        evidence={"destination_profile": destination.key},
    ).raw
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_key = prefix + "manifest.json"
    _put_object(destination_client, bucket=destination.bucket, key=manifest_key, body=raw)
    if hashlib.sha256(_read_object(destination_client, bucket=destination.bucket, key=manifest_key)).hexdigest() != hashlib.sha256(raw).hexdigest():
        raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False)
    return {"release_id": release_id, "manifest": manifest, "manifest_digest": manifest_digest(manifest), "manifest_key": manifest_key, "objects": len(manifest_files), "bytes": total, "source_profile": "local", "destination_profile": destination.key}


def stage_remote_generation(
    source_client: Any,
    source: ResolvedProfile,
    *,
    release_id: str,
    destination_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Download and verify a generation into an isolated local workspace."""
    payload, source_manifest_key, legacy_source = _load_manifest(source_client, source, release_id)
    entries = _manifest_entries(payload)
    root = Path(destination_root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = root / f"restore-{release_id}"
    if final.exists():
        receipt_path = final / "restore-receipt.json"
        manifest_path = final / "manifest.json"
        try:
            marker = json.loads(receipt_path.read_text(encoding="utf-8"))
            staged_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_manifest(staged_manifest)
            if (
                final.is_dir()
                and not final.is_symlink()
                and marker.get("verified") is True
                and marker.get("release_id") == release_id
                and marker.get("manifest_digest") == manifest_digest(staged_manifest)
            ):
                return {**marker, "workspace": str(final), "manifest": staged_manifest, "reused": True}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, PackageContractError, AttributeError, TypeError):
            pass
        raise DataOpsPipelineError("restore_destination_exists", stage="transfer", retryable=False)
    temporary = Path(tempfile.mkdtemp(prefix=f".dataops-{release_id}-", dir=root))
    source_prefix = source_manifest_key.rsplit("manifest.json", 1)[0]
    total = 0
    downloaded_files: list[dict[str, Any]] = []
    try:
        for item in entries:
            source_key = item["key"] if "/" in item["key"] else source_prefix + item["key"]
            data = _read_object(source_client, bucket=source.bucket, key=source_key)
            if item["sha256"] and hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise DataOpsPipelineError("manifest_checksum_mismatch", stage="manifest_verification", retryable=False)
            relative = item.get("path") if payload.get("manifest_version") == 1 else ""
            if not relative:
                relative = _relative_object_key(source_key, source_prefix)
            else:
                relative = _relative_object_key(str(relative), "")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(data)
            total += len(data)
            downloaded_files.append({**item, "key": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        if payload.get("format_version") == 2:
            staged_manifest = payload
        else:
            staged_manifest = build_manifest(
                release_id=release_id,
                dataset_id=source.dataset_id,
                source={"profile": source.key, "source_id": source.source_id, "compatibility": "legacy"},
                identity={"pipeline": "dataops"},
                counts={"objects": len(downloaded_files), "bytes": total},
                files=downloaded_files,
                evidence={"legacy_source": legacy_source},
            ).raw
        raw_manifest = json.dumps(staged_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (temporary / "manifest.json").write_bytes(raw_manifest)
        marker = {"release_id": release_id, "source_profile": source.key, "manifest_digest": manifest_digest(staged_manifest), "objects": len(entries), "bytes": total, "legacy_source": legacy_source, "verified": True}
        (temporary / "restore-receipt.json").write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")
        temporary.rename(final)
        return {**marker, "workspace": str(final), "manifest": staged_manifest}
    except DataOpsPipelineError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise DataOpsPipelineError("restore_write_failed", stage="transfer", retryable=True) from exc


def reconcile_staged_generation(workspace: str | os.PathLike[str], manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_root = Path(workspace)
    if not raw_root.is_dir() or raw_root.is_symlink():
        raise DataOpsPipelineError("staged_generation_missing", stage="reconciliation", retryable=False)
    root = raw_root.resolve()
    database = root / "db.sqlite3"
    database_check = "not_present"
    if database.exists():
        try:
            with sqlite3.connect(database) as connection:
                database_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except (OSError, sqlite3.DatabaseError) as exc:
            raise DataOpsPipelineError("database_reconciliation_failed", stage="reconciliation", retryable=False) from exc
        if database_check != "ok":
            raise DataOpsPipelineError("database_reconciliation_failed", stage="reconciliation", retryable=False)
    documents = (
        sum(
            1
            for path in (root / "media").rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        )
        if (root / "media").is_dir()
        else 0
    )
    indexes = len(list((root / "faiss_indexes").glob("*.index"))) if (root / "faiss_indexes").is_dir() else 0
    # FAISS artifacts are folder-scoped, while readiness is document-scoped.
    # Prefer the restored database's searchable-row evidence when the current
    # schema is present; legacy snapshots fall back to the artifact count.
    if database.exists():
        try:
            with sqlite3.connect(database) as connection:
                indexed_row_count = connection.execute(
                    "SELECT COUNT(*) FROM core_pdffile "
                    "WHERE indexed = 1 AND processing_status = 'ready'"
                ).fetchone()[0]
        except sqlite3.DatabaseError:
            indexed_row_count = None
        if indexed_row_count is not None:
            indexes = int(indexed_row_count)
    expected = manifest.get("counts", {}) if isinstance(manifest.get("counts"), Mapping) else {}
    expected_documents = expected.get("documents")
    if isinstance(expected_documents, int) and expected_documents >= 0 and documents != expected_documents:
        raise DataOpsPipelineError("media_reconciliation_failed", stage="reconciliation", retryable=False)
    ratio = 1.0 if documents == 0 else min(1.0, indexes / documents)
    return {"database": database_check, "documents": documents, "indexes": indexes, "indexing_ratio": round(ratio, 4), "expected_documents": expected_documents}


def atomic_activate_generation(workspace: str | os.PathLike[str], runtime_root: str | os.PathLike[str], release_id: str) -> dict[str, str]:
    """Install a verified generation with one atomic active-pointer swap."""
    raw_source = Path(workspace)
    if not raw_source.is_dir() or raw_source.is_symlink():
        raise DataOpsPipelineError("staged_generation_missing", stage="health_check", retryable=False)
    source = raw_source.resolve()
    root = Path(runtime_root).resolve()
    generations = root / "generations"
    generations.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = generations / release_id
    if target.exists():
        raise DataOpsPipelineError("active_generation_exists", stage="health_check", retryable=False)
    temporary = generations / f".{release_id}.{uuid.uuid4().hex}.partial"
    pointer = root / ".active-generation"
    pointer_tmp = root / f".active-generation.{uuid.uuid4().hex}.tmp"
    active_link = root / "active"
    link_tmp = root / f".active.{uuid.uuid4().hex}.tmp"
    previous_pointer = pointer.read_text(encoding="utf-8") if pointer.is_file() else None
    previous_link = os.readlink(active_link) if active_link.is_symlink() else None
    try:
        shutil.copytree(source, temporary, symlinks=False)
        os.replace(temporary, target)
        link_tmp.symlink_to(Path("generations") / release_id)
        os.replace(link_tmp, active_link)
        pointer_tmp.write_text(release_id + "\n", encoding="utf-8")
        os.replace(pointer_tmp, pointer)
    except (OSError, shutil.Error) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        for temporary_path in (pointer_tmp, link_tmp):
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        try:
            if previous_link is not None:
                rollback_link = root / f".active.rollback.{uuid.uuid4().hex}.tmp"
                rollback_link.symlink_to(previous_link)
                os.replace(rollback_link, active_link)
            elif active_link.is_symlink():
                active_link.unlink()
            if previous_pointer is not None:
                pointer.write_text(previous_pointer, encoding="utf-8")
            elif pointer.is_file():
                pointer.unlink()
        except OSError:
            # The original generation is still retained under generations/;
            # surface one typed failure rather than claiming activation.
            pass
        raise DataOpsPipelineError("activation_failed", stage="health_check", retryable=False) from exc
    return {"active_generation": release_id, "active_path": str(target), "pointer": str(pointer)}


def run_bounded_reindex(
    reconciliation: Mapping[str, Any],
    *,
    reindexer: Callable[[int], int] | None = None,
    budget: ReindexBudget | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    budget = budget or ReindexBudget()
    requested = max(0, int(reconciliation.get("documents", 0)) - int(reconciliation.get("indexes", 0)))
    allowed = budget.take(requested)
    attempts = 0
    completed = 0
    if allowed and reindexer:
        while attempts <= max(0, max_retries):
            attempts += 1
            try:
                completed = max(0, int(reindexer(allowed)))
                break
            except Exception as exc:
                if attempts > max_retries:
                    raise DataOpsPipelineError("reindex_retryable", stage="reindex", retryable=True) from exc
    elif allowed:
        completed = allowed
    return {"requested": requested, "allowed": allowed, "completed": completed, "attempts": attempts, "budget_remaining_run": budget.per_run - budget.used_run, "budget_remaining_day": budget.per_day - budget.used_day}


def run_operation_pipeline(
    operation: str,
    profiles: Iterable[ResolvedProfile],
    *,
    source_profile: str = "",
    destination_profile: str = "",
    selectors: Mapping[str, str] | None = None,
    source_client: Any = None,
    destination_client: Any = None,
    release_id: str = "",
    source_root: str | os.PathLike[str] | None = None,
    staging_root: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    local_dataset_id: str = "",
    activate: bool = True,
    safety_backup: Callable[[], Mapping[str, Any]] | None = None,
    reindexer: Callable[[int], int] | None = None,
    health_checker: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    lease: Callable[[], Any] | None = None,
    budget: ReindexBudget | None = None,
    max_retries: int = 3,
    require_permissions: bool = False,
    source_generation_id: str = "",
    destination_generation_id: str = "",
    confirmation: str = "",
    clone_reason: str = "stage-rehearsal",
) -> dict[str, Any]:
    profiles = tuple(profiles)
    budget = budget or ReindexBudget()
    route = resolve_operation_route(operation, profiles, selectors=selectors, source_profile=source_profile, destination_profile=destination_profile, local_dataset_id=local_dataset_id)
    result: dict[str, Any] = {"operation": route.operation, "source_profile": route.source.key if route.source else "local", "destination_profile": route.destination.key if route.destination else "local", "stages": [], "attempts": 0}
    preflight = preflight_operation(
        route.operation,
        route.source,
        route.destination,
        release_id=release_id,
        local_dataset_id=local_dataset_id,
        active_root=runtime_root,
        source_client=source_client,
        destination_client=destination_client,
        require_permissions=require_permissions,
        source_generation_id=source_generation_id,
        destination_generation_id=destination_generation_id,
        confirmation=confirmation,
        clone_reason=clone_reason,
    )
    result["preflight"] = preflight.redacted()
    if not preflight.ok:
        raise DataOpsPipelineError("preflight_failed", stage="preflight", retryable=False, details={"issues": [issue.__dict__ for issue in preflight.issues]})
    result["release_id"] = preflight.release_id
    for attempt in range(1, max(0, max_retries) + 2):
        result["attempts"] = attempt
        try:
            result["stages"].append({"stage": "preflight", "status": "succeeded"})
            if lease:
                try:
                    lease_value = lease()
                except Exception as exc:
                    raise DataOpsPipelineError("lease_busy", stage="lease", retryable=True) from exc
            else:
                lease_value = {"lease_id": uuid.uuid4().hex}
            result["lease"] = {"lease_id": str(lease_value.get("lease_id", "")) if isinstance(lease_value, Mapping) else "acquired"}
            result["stages"].append({"stage": "lease", "status": "succeeded"})
            if route.operation == "clone_rebind":
                if source_client is None or destination_client is None or route.source is None or route.destination is None:
                    raise DataOpsPipelineError("storage_client_missing", stage="transfer", retryable=False)
                from .clone import clone_rebind_generation

                transfer = clone_rebind_generation(
                    source_client,
                    route.source,
                    destination_client,
                    route.destination,
                    source_generation_id,
                    destination_generation_id=preflight.release_id,
                    confirmation=confirmation,
                    clone_reason=clone_reason,
                )
                staged = None
            elif route.operation == "backup":
                if route.source and route.destination:
                    if source_client is None or destination_client is None:
                        raise DataOpsPipelineError("storage_client_missing", stage="transfer", retryable=False)
                    transfer = transfer_generation(source_client, route.source, destination_client, route.destination, preflight.release_id)
                else:
                    if route.destination is None or destination_client is None or source_root is None:
                        raise DataOpsPipelineError("backup_source_or_destination_missing", stage="transfer", retryable=False)
                    transfer = publish_local_backup(destination_client, route.destination, source_root=source_root, release_id=preflight.release_id)
                staged = None
            elif route.operation in {"copy", "transfer"}:
                if source_client is None or destination_client is None or route.source is None or route.destination is None:
                    raise DataOpsPipelineError("storage_client_missing", stage="transfer", retryable=False)
                transfer = transfer_generation(source_client, route.source, destination_client, route.destination, preflight.release_id)
                staged = None
            else:
                if route.source is None or source_client is None:
                    raise DataOpsPipelineError("restore_source_missing", stage="transfer", retryable=False)
                if route.destination is not None:
                    if destination_client is None:
                        raise DataOpsPipelineError("storage_client_missing", stage="transfer", retryable=False)
                    transfer = transfer_generation(source_client, route.source, destination_client, route.destination, preflight.release_id)
                    staged = None
                else:
                    if staging_root is None:
                        raise DataOpsPipelineError("restore_staging_root_missing", stage="transfer", retryable=False)
                    staged = stage_remote_generation(source_client, route.source, release_id=preflight.release_id, destination_root=staging_root)
                    transfer = staged
            result["transfer"] = {key: value for key, value in transfer.items() if key != "manifest"} if isinstance(transfer, Mapping) else transfer
            result["stages"].append({"stage": "transfer", "status": "succeeded"})
            manifest = transfer.get("manifest", {}) if isinstance(transfer, Mapping) else {}
            if route.operation == "clone_rebind":
                from .clone import _validate_custom_manifest

                _validate_custom_manifest(manifest, route.destination.dataset_id, preflight.release_id)
                result["manifest_format_version"] = int(manifest.get("manifest_version", 1))
                result["manifest_digest"] = str(transfer.get("manifest_digest", ""))
            else:
                validate_manifest(manifest)
                result["manifest_digest"] = manifest_digest(manifest)
            result["stages"].append({"stage": "manifest_verification", "status": "succeeded", "manifest_digest": result["manifest_digest"]})
            if route.operation == "clone_rebind":
                reconciliation = {
                    "database": "remote_verified",
                    "documents": int(manifest.get("counts", {}).get("pdfs", 0) or 0),
                    "indexes": int(manifest.get("counts", {}).get("faiss", 0) or 0),
                    "indexing_ratio": 1.0,
                }
            else:
                reconciliation = reconcile_staged_generation(staged["workspace"], manifest) if staged else {"database": "not_staged", "documents": int(manifest.get("counts", {}).get("documents", 0) or 0), "indexes": int(manifest.get("counts", {}).get("indexes", 0) or 0), "indexing_ratio": 1.0}
            result["reconciliation"] = reconciliation
            result["stages"].append({"stage": "reconciliation", "status": "succeeded"})
            if route.operation == "clone_rebind":
                result["reindex"] = {"requested": 0, "allowed": 0, "completed": 0, "attempts": 0, "budget_remaining_run": budget.per_run - budget.used_run, "budget_remaining_day": budget.per_day - budget.used_day}
            else:
                result["reindex"] = run_bounded_reindex(reconciliation, reindexer=reindexer, budget=budget, max_retries=max_retries)
            completed_reindex = int(result["reindex"].get("completed", 0) or 0)
            if completed_reindex and int(reconciliation.get("documents", 0) or 0):
                reconciliation["indexes"] = min(
                    int(reconciliation.get("documents", 0) or 0),
                    int(reconciliation.get("indexes", 0) or 0) + completed_reindex,
                )
                reconciliation["indexing_ratio"] = round(
                    reconciliation["indexes"] / reconciliation["documents"],
                    4,
                )
            result["stages"].append({"stage": "reindex", "status": "succeeded"})
            default_health = {
                "status": "ok" if result["reconciliation"].get("indexing_ratio", 0.0) >= 1.0 else "degraded",
                "indexing_ratio": result["reconciliation"].get("indexing_ratio", 0.0),
            }
            health = dict(health_checker(result) if health_checker else default_health)
            if health.get("status") not in {"ok", "ready", "verified"}:
                raise DataOpsPipelineError("health_check_retryable", stage="health_check", retryable=True)
            result["health"] = health
            result["stages"].append({"stage": "health_check", "status": "succeeded"})
            if route.operation == "restore" and staged and activate:
                if preflight.safety_backup_required:
                    if safety_backup is None:
                        raise DataOpsPipelineError("safety_backup_required", stage="health_check", retryable=False)
                    result["safety_backup"] = dict(safety_backup())
                if runtime_root is None:
                    raise DataOpsPipelineError("runtime_root_missing", stage="health_check", retryable=False)
                result["activation"] = atomic_activate_generation(staged["workspace"], runtime_root, preflight.release_id)
            activation = result.get("activation")
            if not isinstance(activation, Mapping):
                activation = {}
            result["receipt"] = {
                "published_at": datetime.now(timezone.utc).isoformat(),
                "manifest_digest": result["manifest_digest"],
                "active_generation": activation.get("active_generation", ""),
            }
            result["stages"].append({"stage": "publish_receipt", "status": "succeeded"})
            return result
        except DataOpsPipelineError as exc:
            result["stages"].append({"stage": exc.stage or "unknown", "status": "failed", "code": exc.code, "attempt": attempt})
            if not exc.retryable or attempt > max_retries:
                raise DataOpsPipelineError(exc.code, stage=exc.stage, retryable=False, details={**exc.details, "attempts": attempt, "stages": result["stages"]}) from exc
    raise DataOpsPipelineError("pipeline_retry_exhausted", stage="publish_receipt", retryable=False)


def execute_operation_record(
    operation,
    *,
    environ: Mapping[str, str] | None = None,
    lease: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Execute a queued ``DataOperation`` and persist its safe receipt."""
    from django.conf import settings
    from .models import DataOperation
    from .storage import StorageConfigurationError, client_for_profile
    from datetime import timedelta

    profiles = resolve_profiles(environ)
    selectors = resolve_selectors(environ)
    is_clone = operation.kind in {DataOperation.Kind.CLONE_REBIND, "clone_rebind"}
    if is_clone:
        source_key = operation.source_profile_key or ""
        destination_key = operation.destination_profile_key or ""
    else:
        source_key = operation.source_profile_key or (selectors.get("backup_source") if operation.kind == "backup" else selectors.get("restore_source")) or ""
        destination_key = operation.destination_profile_key or (selectors.get("backup_destination") if operation.kind == "backup" else selectors.get("restore_destination")) or ""
    values = profile_map(profiles)
    source = values.get(source_key) if source_key else None
    destination = values.get(destination_key) if destination_key else values.get(selectors.get("backup", "")) if operation.kind == "backup" else None
    if operation.kind == "restore" and not source:
        source = values.get(operation.profile_key or selectors.get("restore", ""))
    if operation.kind == "backup" and not destination:
        destination = values.get(operation.profile_key or selectors.get("backup", ""))
    if operation.kind not in {"backup", "restore", DataOperation.Kind.CLONE_REBIND, "clone_rebind"}:
        raise DataOpsPipelineError("operation_kind_unsupported", stage="preflight", retryable=False)
    credential_environment = dict(os.environ) if environ is None else environ

    clone_options = operation.checkpoint if is_clone and isinstance(operation.checkpoint, Mapping) else {}
    source_generation_id = str(clone_options.get("source_generation_id", "") or "").strip()
    destination_generation_id = str(clone_options.get("destination_generation_id", "") or operation.release_id or "").strip()
    clone_reason = str(clone_options.get("clone_reason", "stage-rehearsal") or "stage-rehearsal").strip()
    clone_confirmation = ""
    if is_clone:
        if not getattr(settings, "DATAOPS_CLONE_REBIND_ENABLED", False):
            raise DataOpsPipelineError("clone_rebind_disabled", stage="preflight", retryable=False)
        destination_generation_id = clone_destination_generation_id(source_generation_id, destination_generation_id)
        expected_confirmation = clone_confirmation_phrase(
            source.key if source else source_key,
            source_generation_id,
            destination.key if destination else destination_key,
            destination_generation_id,
            clone_reason,
        )
        expected_digest = hashlib.sha256(expected_confirmation.encode("utf-8")).hexdigest()
        if clone_options.get("confirmation_sha256") != expected_digest:
            raise DataOpsPipelineError("clone_confirmation_required", stage="preflight", retryable=False)
        clone_confirmation = expected_confirmation

    def make_client(profile):
        if profile is None:
            return None
        try:
            return client_for_profile(profile, credential_environment)
        except StorageConfigurationError as exc:
            reason = str(exc)
            code = "profile_credentials_invalid" if "credential" in reason else "storage_configuration_invalid"
            raise DataOpsPipelineError(code, stage="preflight", retryable=False, details={"reason": reason}) from exc

    source_client = make_client(source)
    destination_client = make_client(destination)

    safety_profile = None
    if operation.kind == DataOperation.Kind.RESTORE:
        safety_key = selectors.get("backup_destination") or selectors.get("backup") or ""
        safety_profile = values.get(safety_key) if safety_key else None
    used_day = 0
    try:
        from django.utils import timezone

        since = timezone.now() - timedelta(days=1)
        for prior in DataOperation.objects.using("control").filter(kind=DataOperation.Kind.RESTORE, state=DataOperation.State.SUCCEEDED, finished_at__gte=since).only("result"):
            if isinstance(prior.result, dict):
                used_day += int((prior.result.get("reindex") or {}).get("completed", 0) or 0)
    except Exception:
        pass
    budget = ReindexBudget(
        per_run=int(getattr(settings, "DATAOPS_AUTO_HEAL_REINDEX_PER_RUN", 500)),
        per_day=int(getattr(settings, "DATAOPS_AUTO_HEAL_REINDEX_PER_DAY", 5000)),
        used_day=used_day,
    )

    runtime_root = Path(getattr(settings, "RUNTIME_GENERATIONS_ROOT", Path(getattr(settings, "DATA_ROOT", "/tmp")) / "runtime-generations"))

    def safety_backup():
        if safety_profile is None or not safety_profile.can_backup:
            raise DataOpsPipelineError("safety_backup_required", stage="health_check", retryable=False)
        active = runtime_root / "active"
        if not active.is_dir():
            raise DataOpsPipelineError("active_generation_missing", stage="health_check", retryable=False)
        safety_release = "safety-" + operation.public_id.hex
        safety_client = make_client(safety_profile)
        return publish_local_backup(
            safety_client,
            safety_profile,
            source_root=active,
            release_id=safety_release,
        )

    def reindex_local(allowed: int) -> int:
        if allowed <= 0:
            return 0
        try:
            from core.maintenance import queue_job, run_job
            from core.models import PDFFile

            pdfs = list(PDFFile.objects.filter(indexed=False).order_by("pk")[:allowed])
            if not pdfs:
                return 0
            job = queue_job(kind="reindex_needed", requested_by=None, pdfs=pdfs, options={"dataops_operation": str(operation.public_id)})
            finished = run_job(job)
            if finished.failed_items:
                raise DataOpsPipelineError("reindex_retryable", stage="reindex", retryable=True)
            return int(finished.completed_items or 0)
        except DataOpsPipelineError:
            raise
        except Exception as exc:
            raise DataOpsPipelineError("reindex_retryable", stage="reindex", retryable=True) from exc

    return run_operation_pipeline(
        operation.kind,
        profiles,
        source_profile=source.key if source else "",
        destination_profile=destination.key if destination else "",
        selectors=selectors,
        source_client=source_client,
        destination_client=destination_client,
        release_id=operation.release_id,
        source_root=getattr(settings, "DATA_ROOT", None),
        staging_root=Path(getattr(settings, "DATAOPS_RESTORE_STAGING_ROOT", Path(getattr(settings, "DATA_ROOT", "/tmp")) / "dataops-restore")),
        runtime_root=runtime_root,
        local_dataset_id=str(getattr(settings, "DATASET_ID", "")),
        activate=bool(getattr(settings, "DATAOPS_RESTORE_AUTO_ACTIVATE_STAGING", False)),
        safety_backup=safety_backup if operation.kind == DataOperation.Kind.RESTORE else None,
        reindexer=reindex_local if getattr(settings, "DATAOPS_AUTO_HEAL_ENABLED", False) else None,
        budget=budget,
        max_retries=int(getattr(settings, "DATAOPS_AUTO_HEAL_MAX_RETRIES", 3)),
        require_permissions=True,
        lease=lease,
        source_generation_id=source_generation_id,
        destination_generation_id=destination_generation_id,
        confirmation=clone_confirmation,
        clone_reason=clone_reason,
    )
