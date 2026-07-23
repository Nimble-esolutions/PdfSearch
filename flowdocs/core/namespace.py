"""Dataset-scoped S3 object-key builder.

Centralises all object-key construction so dataset IDs, generation IDs,
and blob digests are combined in one tested component. Validates every
component against unsafe characters, path traversal, and length limits.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FAISS_FILE_RE = re.compile(r"^folder_([0-9]+)\.index$")


class KeyBuilderError(ValueError):
    """Raised when an object-key component is invalid."""


class KeyBuilder:
    """Build dataset-scoped S3 object keys with strict validation."""

    def __init__(self, dataset_id: str):
        _validate_dataset_id(dataset_id)
        self.dataset_id = dataset_id

    def prefix(self) -> str:
        return f"datasets/{self.dataset_id}"

    def control_registration(self) -> str:
        return f"{self.prefix()}/control/registration.json"

    def control_authoritative(self) -> str:
        return f"{self.prefix()}/control/authoritative.json"

    def control_global_writer(self) -> str:
        return f"{self.prefix()}/control/writer.json"

    def control_lease(self) -> str:
        return f"{self.prefix()}/control/writer-lease.json"

    def control_history(self, timestamp: str, event: str) -> str:
        _validate_safe_component(timestamp, "timestamp")
        _validate_safe_component(event, "event")
        return f"{self.prefix()}/control/history/{timestamp}-{event}.json"

    def generation_manifest(self, generation_id: str) -> str:
        _validate_generation_id(generation_id)
        return f"{self.prefix()}/generations/{generation_id}/manifest.json"

    def generation_database(self, generation_id: str) -> str:
        _validate_generation_id(generation_id)
        return f"{self.prefix()}/generations/{generation_id}/database.sqlite3"

    def generation_faiss(self, generation_id: str, folder_id: int) -> str:
        _validate_generation_id(generation_id)
        if not isinstance(folder_id, int) or folder_id < 1:
            raise KeyBuilderError("FAISS folder_id must be a positive integer")
        return f"{self.prefix()}/generations/{generation_id}/faiss/folder_{folder_id}.index"

    def generation_metadata(self, generation_id: str, path: str) -> str:
        _validate_generation_id(generation_id)
        parts = _safe_relative_parts(path)
        return f"{self.prefix()}/generations/{generation_id}/metadata/{'/'.join(parts)}"

    def blob_pdf(self, sha256: str) -> str:
        _validate_sha256(sha256)
        return f"{self.prefix()}/blobs/pdfs/sha256/{sha256}.pdf"

    def blob_db(self, sha256: str) -> str:
        _validate_sha256(sha256)
        return f"{self.prefix()}/blobs/db/{sha256}.sqlite3"

    def blob_faiss(self, sha256: str) -> str:
        _validate_sha256(sha256)
        return f"{self.prefix()}/blobs/faiss/{sha256}.index"

    @staticmethod
    def legacy_pdf_key(sha256: str) -> str:
        _validate_sha256(sha256)
        return f"pdfs/sha256/{sha256}.pdf"

    @staticmethod
    def legacy_manifest_key(release_id: str) -> str:
        _validate_generation_id(release_id)
        return f"manifests/{release_id}.json"

    def is_scoped(self, key: str) -> bool:
        return key.startswith(f"datasets/{self.dataset_id}/")

    def extract_generation_id(self, key: str) -> str | None:
        prefix = f"datasets/{self.dataset_id}/generations/"
        if not key.startswith(prefix):
            return None
        rest = key[len(prefix):]
        parts = rest.split("/", 1)
        return parts[0] if parts else None


def _validate_dataset_id(value: str) -> None:
    if not value:
        raise KeyBuilderError("Dataset ID is required")
    if "/" in value or ".." in value or "\\" in value:
        raise KeyBuilderError(f"Dataset ID contains unsafe characters: {value}")
    if len(value) > 100:
        raise KeyBuilderError(f"Dataset ID exceeds maximum length: {value}")
    for char in value:
        if ord(char) < 32:
            raise KeyBuilderError(f"Dataset ID contains control characters: {value}")


def _validate_generation_id(value: str) -> None:
    if not value:
        raise KeyBuilderError("Generation ID is required")
    if "/" in value or ".." in value or "\\" in value:
        raise KeyBuilderError(f"Generation ID contains unsafe characters: {value}")
    if len(value) > 120:
        raise KeyBuilderError(f"Generation ID exceeds maximum length: {value}")
    for char in value:
        if ord(char) < 32:
            raise KeyBuilderError(f"Generation ID contains control characters: {value}")


def _validate_sha256(value: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise KeyBuilderError("Expected a lowercase 64-character SHA-256 hex digest")


def _validate_safe_component(value: str, label: str) -> None:
    if not value or not SAFE_ID_RE.fullmatch(value):
        raise KeyBuilderError(f"Unsafe {label}: {value}")


def _safe_relative_parts(path: str) -> tuple[str, ...]:
    if not path or path.startswith("/") or "\\" in path:
        raise KeyBuilderError(f"Unsafe path: {path}")
    parts = tuple(path.split("/"))
    for part in parts:
        if not part or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", part):
            raise KeyBuilderError(f"Unsafe path component: {part}")
    return parts
