"""Read-only recovery-point inspection and v1-to-v2 planning helpers."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Mapping

from .compat.v1 import V1Manifest, iter_object_keys, read_manifest
from .package import build_manifest


class RecoveryInspectionError(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryInspection:
    release_id: str
    dataset_id: str
    source_id: str
    manifest_digest: str
    format_version: int
    object_count: int
    keys: tuple[str, ...]


def inspect_manifest(payload: Mapping[str, Any]) -> RecoveryInspection:
    if payload.get("format_version") == 2:
        files = payload.get("files")
        if not isinstance(files, list):
            raise RecoveryInspectionError("v2 files must be a list")
        release_id = str(payload.get("release_id") or "").strip()
        dataset_id = str(payload.get("dataset_id") or "").strip()
        source_id = str(payload.get("source", {}).get("source_id") or "").strip()
        if not release_id or not dataset_id:
            raise RecoveryInspectionError("release_id and dataset_id are required")
        keys = tuple(_safe_key(str(item.get("key", ""))) for item in files if isinstance(item, Mapping))
        from .package import manifest_digest
        return RecoveryInspection(release_id, dataset_id, source_id, manifest_digest(payload), 2, len(keys), keys)
    try:
        legacy: V1Manifest = read_manifest(payload)
    except ValueError as exc:
        raise RecoveryInspectionError(str(exc)) from exc
    keys = tuple(iter_object_keys(legacy))
    return RecoveryInspection(legacy.release_id, legacy.dataset_id, legacy.source_id, legacy.digest, 1, len(keys), keys)


def plan_v1_repack(payload: Mapping[str, Any]):
    """Return a v2 manifest plan without downloading or writing any object."""
    inspected = inspect_manifest(payload)
    if inspected.format_version != 1:
        raise RecoveryInspectionError("repack plan only accepts v1 manifests")
    files = [{"key": key, "sha256": "0" * 64} for key in inspected.keys]
    # Placeholder hashes are replaced only after a quarantined download verifies
    # each object; the evidence flag prevents this draft being activated.
    return build_manifest(
        release_id=inspected.release_id,
        dataset_id=inspected.dataset_id,
        source={"source_id": inspected.source_id, "compatibility": "v1-read-only"},
        identity={"import": "quarantine-required"},
        counts={"objects": inspected.object_count},
        files=files,
        evidence={"legacy_manifest_digest": inspected.manifest_digest, "hashes_pending": True},
    )


def _safe_key(key: str) -> str:
    normalized = posixpath.normpath(key.lstrip("/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise RecoveryInspectionError(f"unsafe object key: {key!r}")
    return normalized
