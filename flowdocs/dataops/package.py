"""Format v2 package contract and deterministic manifest helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


FORMAT_VERSION = 2
EXCLUDED_TOP_LEVEL = frozenset({"credentials", "logs", "temp", "redis", "control_db", "static"})
REQUIRED_FIELDS = frozenset({"format_version", "release_id", "dataset_id", "source", "identity", "counts", "files"})


class PackageContractError(ValueError):
    pass


@dataclass(frozen=True)
class PackageManifest:
    raw: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return manifest_digest(self.raw)


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_manifest(*, release_id: str, dataset_id: str, source: Mapping[str, Any], identity: Mapping[str, Any], counts: Mapping[str, Any], files: Iterable[Mapping[str, Any]], evidence: Mapping[str, Any] | None = None) -> PackageManifest:
    payload = {
        "format_version": FORMAT_VERSION,
        "release_id": release_id,
        "dataset_id": dataset_id,
        "source": dict(source),
        "identity": dict(identity),
        "counts": dict(counts),
        "files": [dict(item) for item in files],
        "evidence": dict(evidence or {}),
    }
    validate_manifest(payload)
    return PackageManifest(payload)


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    missing = REQUIRED_FIELDS - set(manifest)
    if missing:
        raise PackageContractError("missing required fields: " + ", ".join(sorted(missing)))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise PackageContractError(f"expected format_version={FORMAT_VERSION}")
    for name in ("release_id", "dataset_id"):
        if not str(manifest.get(name) or "").strip():
            raise PackageContractError(f"{name} is required")
    for field in ("source", "identity", "counts"):
        if not isinstance(manifest[field], Mapping):
            raise PackageContractError(f"{field} must be an object")
    files = manifest["files"]
    if not isinstance(files, list):
        raise PackageContractError("files must be a list")
    for entry in files:
        if not isinstance(entry, Mapping) or not entry.get("key") or not entry.get("sha256"):
            raise PackageContractError("each file requires key and sha256")
    forbidden = sorted(set(manifest) & EXCLUDED_TOP_LEVEL)
    if forbidden:
        raise PackageContractError("package cannot include: " + ", ".join(forbidden))
