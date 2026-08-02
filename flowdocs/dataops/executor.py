"""Quarantined artifact-vault restore executor.

This module downloads and verifies a selected generation into an isolated
workspace. It deliberately never replaces ``DATA_ROOT`` or publishes an
authoritative pointer; activation remains a separate, approval-gated step.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .config import ResolvedProfile
from .recovery import RecoveryInspection, RecoveryInspectionError, inspect_manifest
from .storage import generation_manifest_key, generation_prefix


class RestoreExecutionError(RuntimeError):
    """A restore could not be staged safely."""


def execute_backup(client, profile, *, source_root, release_id, files=None):
    """Publish a local source snapshot through the shared pipeline executor."""
    from .pipeline import publish_local_backup

    return publish_local_backup(
        client,
        profile,
        source_root=source_root,
        release_id=release_id,
        files=files,
    )


def execute_restore(client, profile, *, release_id, destination_root):
    """Stage a verified remote generation through the shared pipeline executor."""
    from .pipeline import stage_remote_generation

    try:
        return stage_remote_generation(
            client,
            profile,
            release_id=release_id,
            destination_root=destination_root,
        )
    except Exception as exc:
        if isinstance(exc, RestoreExecutionError):
            raise
        raise RestoreExecutionError(getattr(exc, "code", "restore_execution_failed")) from exc


def manifest_key(profile: ResolvedProfile, release_id: str) -> str:
    try:
        return generation_manifest_key(profile, release_id, legacy=not bool(profile.namespace))
    except ValueError as exc:
        raise RestoreExecutionError(str(exc)) from exc


def _safe_relative(key: str, profile: ResolvedProfile, release_id: str) -> Path:
    prefix = generation_prefix(profile, release_id, legacy=not bool(profile.namespace))
    relative = key[len(prefix):] if key.startswith(prefix) else posixpath.basename(key)
    normalized = posixpath.normpath(relative.lstrip("/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise RestoreExecutionError("object_key_unsafe")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise RestoreExecutionError("object_key_unsafe")
    return path


def _read_body(client: Any, bucket: str, key: str, *, max_bytes: int) -> bytes:
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read(max_bytes + 1)
    except Exception as exc:  # boto3 errors vary by backend
        raise RestoreExecutionError("object_read_failed") from exc
    if len(body) > max_bytes:
        raise RestoreExecutionError("object_exceeds_limit")
    return body


def stage_restore(
    client: Any,
    profile: ResolvedProfile,
    release_id: str,
    destination: str | os.PathLike[str],
    *,
    max_manifest_bytes: int = 8 * 1024 * 1024,
    max_object_bytes: int = 512 * 1024 * 1024,
    max_total_bytes: int = 2 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    """Download and hash-check one generation into ``destination``.

    The returned workspace is atomically renamed only after every object
    matches the manifest digest. On any failure the partial workspace is
    removed.
    """
    if max_manifest_bytes < 1 or max_object_bytes < 1 or max_total_bytes < 1:
        raise ValueError("restore byte limits must be positive")
    key = manifest_key(profile, release_id)
    raw_manifest = _read_body(client, profile.bucket, key, max_bytes=max_manifest_bytes)
    try:
        payload = json.loads(raw_manifest.decode("utf-8"))
        inspection: RecoveryInspection = inspect_manifest(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, RecoveryInspectionError) as exc:
        raise RestoreExecutionError("manifest_invalid") from exc
    if inspection.release_id != release_id or inspection.dataset_id != profile.dataset_id:
        raise RestoreExecutionError("manifest_identity_mismatch")
    expected_digests = {
        str(item.get("key") or item.get("object_key")): str(item.get("sha256") or "")
        for item in payload.get("files", [])
        if isinstance(item, dict)
    }

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f"restore-{release_id}-", dir=root))
    total = 0
    try:
        for object_key in inspection.keys:
            data = _read_body(client, profile.bucket, object_key, max_bytes=max_object_bytes)
            expected = expected_digests.get(object_key, "")
            if len(expected) != 64 or hashlib.sha256(data).hexdigest() != expected:
                raise RestoreExecutionError("object_digest_mismatch")
            total += len(data)
            if total > max_total_bytes:
                raise RestoreExecutionError("restore_total_exceeds_limit")
            relative = _safe_relative(object_key, profile, release_id)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temporary / "manifest.json").write_bytes(raw_manifest)
        marker = {
            "release_id": inspection.release_id,
            "dataset_id": inspection.dataset_id,
            "source_id": inspection.source_id,
            "manifest_digest": inspection.manifest_digest,
            "format_version": inspection.format_version,
            "objects": inspection.object_count,
            "bytes": total,
            "verified": True,
        }
        (temporary / "restore-receipt.json").write_text(
            json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
        )
        final = root / f"restore-{release_id}"
        if final.exists():
            raise RestoreExecutionError("restore_destination_exists")
        temporary.rename(final)
        return {**marker, "workspace": str(final)}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
