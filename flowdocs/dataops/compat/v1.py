"""Strict, read-only reader for legacy artifact-vault v1 manifests."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class V1ManifestError(ValueError):
    """Manifest is malformed or violates the v1 read-only contract."""


@dataclass(frozen=True)
class V1Manifest:
    raw: Mapping[str, Any]
    release_id: str
    dataset_id: str
    source_id: str
    files: tuple[Mapping[str, Any], ...]

    @property
    def digest(self) -> str:
        import json

        canonical = json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def _safe_key(key: str) -> str:
    normalized = posixpath.normpath(key.lstrip("/"))
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        raise V1ManifestError(f"unsafe object key: {key!r}")
    return normalized


def read_manifest(payload: Mapping[str, Any]) -> V1Manifest:
    if payload.get("manifest_version") != 1:
        raise V1ManifestError("only manifest_version=1 is supported by the compatibility reader")
    if payload.get("read_only") is not True:
        raise V1ManifestError("legacy manifests must be read_only")
    release_id = str(payload.get("release_id") or "").strip()
    dataset_id = str(payload.get("source", {}).get("dataset_id") or "").strip()
    source_id = str(payload.get("source", {}).get("source_id") or "").strip()
    if not release_id or not dataset_id:
        raise V1ManifestError("release_id and source.dataset_id are required")
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise V1ManifestError("files must be a list")
    files: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not entry.get("key"):
            raise V1ManifestError("each file entry requires key")
        _safe_key(str(entry["key"]))
        files.append(dict(entry))
    return V1Manifest(payload, release_id, dataset_id, source_id, tuple(files))


def iter_object_keys(manifest: V1Manifest) -> Iterable[str]:
    """Yield validated keys once, preserving manifest order."""
    seen: set[str] = set()
    for entry in manifest.files:
        key = _safe_key(str(entry["key"]))
        if key not in seen:
            seen.add(key)
            yield key
