"""Opt-in immutable artifact storage for PDFs, FAISS indexes, and manifests.

This module is deliberately independent from Django's file storage. It only
creates an S3 client when the explicit vault flag is enabled and never falls
back to local storage after an enabled vault operation fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, BinaryIO, Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PDF_KEY_RE = re.compile(r"^pdfs/sha256/([0-9a-f]{64})\.pdf$")
FAISS_KEY_RE = re.compile(r"^faiss/([A-Za-z0-9][A-Za-z0-9._-]*)/(folder_[0-9]+\.index)$")
DATABASE_KEY_RE = re.compile(r"^databases/([A-Za-z0-9][A-Za-z0-9._-]*)\.sqlite3$")
INVENTORY_SCHEMA = "pdfsearch-artifact-inventory/v1"
METADATA_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".npy", ".npz", ".pkl", ".pickle"}
MUTABLE_RELEASE_IDS = {"active", "current", "latest", "dev", "stage", "staging", "prod", "production"}


class ArtifactVaultError(RuntimeError):
    """Base error for all vault failures."""


class ArtifactVaultDisabled(ArtifactVaultError):
    """Raised when a vault operation is attempted while the feature is off."""


class ArtifactVaultConfigurationError(ArtifactVaultError):
    """Raised when enabled vault configuration is incomplete or invalid."""


class ArtifactVaultIntegrityError(ArtifactVaultError):
    """Raised when an artifact does not match its declared checksum."""


@dataclass(frozen=True)
class VaultConfig:
    enabled: bool
    endpoint: str = ""
    bucket: str = ""
    region: str = ""
    access_key: str = ""
    secret_key: str = ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "VaultConfig":
        env = os.environ if environ is None else environ
        return cls(
            enabled=_parse_bool(env.get("ARTIFACT_VAULT_ENABLED", "0")),
            endpoint=env.get("ARTIFACT_VAULT_ENDPOINT", "").strip(),
            bucket=env.get("ARTIFACT_VAULT_BUCKET", "").strip(),
            region=env.get("ARTIFACT_VAULT_REGION", "").strip(),
            access_key=env.get("ARTIFACT_VAULT_ACCESS_KEY", "").strip(),
            secret_key=env.get("ARTIFACT_VAULT_SECRET_KEY", "").strip(),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = [
            name
            for name, value in (
                ("ARTIFACT_VAULT_ENDPOINT", self.endpoint),
                ("ARTIFACT_VAULT_BUCKET", self.bucket),
                ("ARTIFACT_VAULT_REGION", self.region),
                ("ARTIFACT_VAULT_ACCESS_KEY", self.access_key),
                ("ARTIFACT_VAULT_SECRET_KEY", self.secret_key),
            )
            if not value
        ]
        if missing:
            raise ArtifactVaultConfigurationError(
                "Enabled artifact vault is missing: " + ", ".join(missing)
            )


@dataclass(frozen=True)
class ArtifactMetadata:
    key: str
    sha256: str
    size: int
    content_type: str = "application/octet-stream"
    etag: str | None = None


@dataclass(frozen=True)
class VaultHealth:
    """Safe capability state for operator-facing vault status."""

    enabled: bool
    configured: bool
    reachable: bool
    bucket_exists: bool | None
    error_code: str = ""

    @property
    def healthy(self) -> bool:
        return self.enabled and self.configured and self.reachable and self.bucket_exists is True


class ArtifactVault:
    """Small fail-closed API around an S3-compatible object store."""

    def __init__(self, config: VaultConfig | None = None, client: Any | None = None):
        self.config = config or VaultConfig.from_env()
        self.config.validate()
        self._client = client

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def client(self) -> Any:
        self._require_enabled()
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ArtifactVaultConfigurationError(
                    "boto3 is required when the artifact vault is enabled"
                ) from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint,
                region_name=self.config.region,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
            )
        return self._client

    def health_check(self) -> VaultHealth:
        """Probe the configured bucket and return redacted capability state."""
        if not self.config.enabled:
            return VaultHealth(False, False, False, False)
        try:
            self.client.head_bucket(Bucket=self.config.bucket)
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            error = response.get("Error", {}) or {}
            code = str(error.get("Code", ""))
            if code in {"403", "AccessDenied"}:
                return VaultHealth(True, True, True, None, code)
            if code in {"404", "NoSuchBucket", "NotFound"}:
                return VaultHealth(True, True, True, False, code)
            return VaultHealth(True, True, False, None, code or "probe_failed")
        return VaultHealth(True, True, True, True)

    def put(
        self,
        key: str,
        payload: bytes | bytearray | BinaryIO,
        expected_sha256: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> ArtifactMetadata:
        """Put one immutable artifact after validating its content checksum."""
        self._require_enabled()
        self._validate_immutable_key(key)
        data = _read_bytes(payload)
        digest = _sha256(data)
        pdf_match = PDF_KEY_RE.fullmatch(key)
        if pdf_match and pdf_match.group(1) != digest:
            raise ArtifactVaultIntegrityError("PDF object key does not match its content checksum")
        _validate_expected_checksum(expected_sha256, digest)
        response = self._call(
            "put_object",
            Bucket=self.config.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest, "immutable": "true"},
        )
        return ArtifactMetadata(
            key=key,
            sha256=digest,
            size=len(data),
            content_type=content_type,
            etag=response.get("ETag"),
        )

    def put_pdf(
        self,
        payload: bytes | bytearray | BinaryIO,
        expected_sha256: str | None = None,
    ) -> ArtifactMetadata:
        data = _read_bytes(payload)
        digest = _sha256(data)
        _validate_expected_checksum(expected_sha256, digest)
        return self.put(
            self.pdf_object_key(digest),
            data,
            expected_sha256=digest,
            content_type="application/pdf",
        )

    def put_faiss(
        self,
        generation_id: str,
        folder_id: int,
        payload: bytes | bytearray | BinaryIO,
        expected_sha256: str | None = None,
    ) -> ArtifactMetadata:
        return self.put(
            self.faiss_object_key(generation_id, folder_id),
            payload,
            expected_sha256=expected_sha256,
            content_type="application/octet-stream",
        )

    def get(self, key: str, expected_sha256: str | None = None) -> bytes:
        self._validate_immutable_key(key)
        response = self._call("get_object", Bucket=self.config.bucket, Key=key)
        data = _read_bytes(response["Body"])
        stored_sha256 = _metadata_checksum(response.get("Metadata", {}))
        digest = _sha256(data)
        if stored_sha256 != digest:
            raise ArtifactVaultIntegrityError(f"Checksum mismatch for object {key}")
        _validate_expected_checksum(expected_sha256, digest)
        return data

    def head(self, key: str, expected_sha256: str | None = None) -> ArtifactMetadata:
        self._validate_immutable_key(key)
        response = self._call("head_object", Bucket=self.config.bucket, Key=key)
        digest = _metadata_checksum(response.get("Metadata", {}))
        _validate_expected_checksum(expected_sha256, digest)
        return ArtifactMetadata(
            key=key,
            sha256=digest,
            size=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType", "application/octet-stream"),
            etag=response.get("ETag"),
        )

    def put_manifest(
        self,
        manifest: Mapping[str, Any] | bytes | bytearray,
        release_id: str | None = None,
    ) -> ArtifactMetadata:
        """Validate and upload an already-generated inventory manifest."""
        self._require_enabled()
        payload = self.normalize_manifest(manifest, release_id=release_id)
        data, _ = _manifest_bytes(payload)
        release_id = payload["release_id"]
        key = self.manifest_object_key(release_id)
        return self.put(key, data, content_type="application/json")

    def validate_manifest(self, manifest: Mapping[str, Any] | bytes | bytearray) -> dict[str, Any]:
        """Validate a manifest without making a provider call."""
        self._require_enabled()
        return self.normalize_manifest(manifest)

    def normalize_manifest(
        self,
        manifest: Mapping[str, Any] | bytes | bytearray,
        release_id: str | None = None,
    ) -> dict[str, Any]:
        """Normalize legacy or inventory manifests to the vault file contract."""
        self._require_enabled()
        return normalize_manifest(manifest, release_id=release_id)

    def list_manifests(self, prefix: str = "manifests/") -> list[ArtifactMetadata]:
        """List manifest objects and validate their stored checksum metadata."""
        self._require_enabled()
        if not prefix.startswith("manifests/"):
            raise ArtifactVaultConfigurationError("Manifest listings must use the manifests/ prefix")
        results: list[ArtifactMetadata] = []
        continuation_token: str | None = None
        while True:
            params: dict[str, Any] = {"Bucket": self.config.bucket, "Prefix": prefix}
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = self._call("list_objects_v2", **params)
            for item in response.get("Contents", []):
                key = item.get("Key")
                if not isinstance(key, str) or not key.endswith(".json"):
                    continue
                results.append(self.head(key))
            if not response.get("IsTruncated"):
                return results
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                raise ArtifactVaultError("Vault returned a truncated manifest listing without a cursor")

    def get_manifest(self, release_id: str) -> bytes:
        return self.get(self.manifest_object_key(release_id))

    def head_manifest(self, release_id: str) -> ArtifactMetadata:
        return self.head(self.manifest_object_key(release_id))

    @staticmethod
    def pdf_object_key(sha256: str) -> str:
        _validate_sha256(sha256)
        return f"pdfs/sha256/{sha256}.pdf"

    @staticmethod
    def faiss_object_key(generation_id: str, folder_id: int) -> str:
        _validate_release_id(generation_id)
        if not isinstance(folder_id, int) or folder_id < 1:
            raise ArtifactVaultConfigurationError("FAISS folder id must be a positive integer")
        return f"faiss/{generation_id}/folder_{folder_id}.index"

    @staticmethod
    def manifest_object_key(release_id: str) -> str:
        _validate_release_id(release_id)
        return f"manifests/{release_id}.json"

    @staticmethod
    def database_object_key(generation_id: str) -> str:
        _validate_release_id(generation_id)
        return f"databases/{generation_id}.sqlite3"

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise ArtifactVaultDisabled("Artifact vault is disabled")

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = getattr(self.client, method)(**kwargs)
        except ArtifactVaultError:
            raise
        except Exception as exc:
            # Do not expose provider responses, endpoints, or credentials.
            raise ArtifactVaultError(f"Artifact vault {method} failed") from exc
        if not isinstance(response, dict):
            raise ArtifactVaultError(f"Artifact vault {method} returned an invalid response")
        return response

    @staticmethod
    def _validate_immutable_key(key: str) -> None:
        if PDF_KEY_RE.fullmatch(key):
            return
        faiss_match = FAISS_KEY_RE.fullmatch(key)
        if faiss_match:
            _validate_release_id(faiss_match.group(1))
            return
        if DATABASE_KEY_RE.fullmatch(key):
            _validate_release_id(DATABASE_KEY_RE.fullmatch(key).group(1))
            return
        metadata_parts = key.split("/")
        if _is_safe_scoped_key(key, "metadata"):
            _validate_release_id(metadata_parts[1])
            return
        manifest_match = re.fullmatch(r"manifests/([A-Za-z0-9][A-Za-z0-9._-]*)\.json", key)
        if manifest_match:
            _validate_release_id(manifest_match.group(1))
            return
        if _is_safe_scoped_key(key, "control"):
            return
        if _is_safe_scoped_key(key, "generations"):
            return
        if _is_safe_scoped_key(key, "blobs"):
            return
        raise ArtifactVaultConfigurationError("Unsupported immutable artifact key")


def object_key_for_manifest_entry(entry: Mapping[str, Any]) -> str:
    """Resolve a legacy manifest entry to its immutable vault key."""
    object_key = entry.get("object_key")
    if isinstance(object_key, str):
        ArtifactVault._validate_immutable_key(object_key)
        return object_key

    path = entry.get("path")
    digest = entry.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ArtifactVaultIntegrityError("Manifest entry needs path and sha256")
    _validate_sha256(digest)
    parts = _safe_relative_parts(path)
    if path.lower().endswith(".pdf"):
        return ArtifactVault.pdf_object_key(digest)

    if len(parts) >= 3 and parts[-3] in {"faiss", "faiss_indexes"}:
        generation_id = parts[-2]
        filename = parts[-1]
        match = re.fullmatch(r"folder_([0-9]+)\.index", filename)
        if match:
            return ArtifactVault.faiss_object_key(generation_id, int(match.group(1)))
    raise ArtifactVaultIntegrityError(
        "Manifest entry needs an immutable object_key or a generation-bound FAISS path"
    )


def normalize_manifest(
    manifest: Mapping[str, Any] | bytes | bytearray,
    release_id: str | None = None,
) -> dict[str, Any]:
    """Normalize the inventory producer's nested schema without changing its file."""
    _, payload = _manifest_bytes(manifest)
    manifest_release_id = payload.get("release_id")
    if manifest_release_id is not None:
        _validate_release_id(manifest_release_id)
    if release_id is not None:
        _validate_release_id(release_id)
    if manifest_release_id is not None and release_id is not None and manifest_release_id != release_id:
        raise ArtifactVaultIntegrityError("Supplied release id does not match the manifest")
    effective_release_id = manifest_release_id or release_id
    if effective_release_id is None:
        raise ArtifactVaultIntegrityError(
            "Manifest has no release_id; supply an explicit immutable --release-id"
        )

    normalized = deepcopy(payload)
    normalized["release_id"] = effective_release_id
    if _is_inventory_manifest(payload):
        normalized["files"] = _normalize_inventory_files(payload, effective_release_id)
    else:
        normalized["files"] = _normalize_legacy_files(payload, effective_release_id)
    return normalized


def _is_inventory_manifest(payload: Mapping[str, Any]) -> bool:
    schema = payload.get("schema")
    if schema == INVENTORY_SCHEMA:
        return True
    return isinstance(schema, Mapping) and schema.get("inventory_schema") == INVENTORY_SCHEMA


def _normalize_inventory_files(payload: Mapping[str, Any], release_id: str) -> list[dict[str, Any]]:
    if payload.get("read_only") is not True:
        raise ArtifactVaultIntegrityError("Inventory manifest must declare read_only=true")
    sections = (
        ("pdf_storage", "files", "pdf"),
        ("faiss", "files", "faiss"),
        ("embedding_index", "metadata_files", "metadata"),
    )
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_key_records: dict[str, tuple[str, str, int]] = {}
    for section_name, field_name, artifact_type in sections:
        section = payload.get(section_name)
        if not isinstance(section, Mapping) or not isinstance(section.get(field_name), list):
            raise ArtifactVaultIntegrityError(
                f"Inventory manifest requires {section_name}.{field_name}"
            )
        for entry in section[field_name]:
            if not isinstance(entry, Mapping):
                raise ArtifactVaultIntegrityError("Inventory file entries must be objects")
            path = entry.get("path")
            size = entry.get("size_bytes")
            digest = entry.get("sha256")
            if not isinstance(path, str) or path in seen_paths:
                raise ArtifactVaultIntegrityError("Inventory contains a missing or duplicate path")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ArtifactVaultIntegrityError(f"Invalid size_bytes for inventory path {path}")
            _validate_sha256(digest)
            key = _inventory_object_key(artifact_type, path, digest, release_id)
            previous = seen_key_records.get(key)
            if previous is not None and previous != (artifact_type, digest, size):
                raise ArtifactVaultIntegrityError("Inventory contains conflicting object keys")
            if previous is not None and artifact_type != "pdf":
                raise ArtifactVaultIntegrityError("Inventory contains ambiguous object keys")
            seen_paths.add(path)
            seen_key_records[key] = (artifact_type, digest, size)
            normalized.append(
                {
                    "path": path,
                    "bytes": size,
                    "sha256": digest,
                    "object_key": key,
                    "artifact_type": artifact_type,
                }
            )
    return normalized


def _normalize_legacy_files(payload: Mapping[str, Any], release_id: str) -> list[dict[str, Any]]:
    files = payload.get("files")
    if not isinstance(files, list):
        raise ArtifactVaultIntegrityError("Manifest files must be a list")
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_keys: set[str] = set()
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ArtifactVaultIntegrityError("Manifest file entries must be objects")
        path = entry.get("path")
        size = entry.get("bytes", entry.get("size_bytes"))
        digest = entry.get("sha256")
        if not isinstance(path, str) or path in seen_paths:
            raise ArtifactVaultIntegrityError("Manifest contains a missing or duplicate path")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ArtifactVaultIntegrityError(f"Invalid bytes for manifest path {path}")
        _validate_sha256(digest)
        key = entry.get("object_key")
        if not isinstance(key, str):
            key = _legacy_object_key(path, digest, release_id)
        ArtifactVault._validate_immutable_key(key)
        if key in seen_keys:
            raise ArtifactVaultIntegrityError("Manifest contains ambiguous object keys")
        seen_paths.add(path)
        seen_keys.add(key)
        normalized.append(
            {
                "path": path,
                "bytes": size,
                "sha256": digest,
                "object_key": key,
                "artifact_type": _artifact_type_for_key(key),
            }
        )
    return normalized


def _inventory_object_key(artifact_type: str, path: str, digest: str, release_id: str) -> str:
    parts = _safe_relative_parts(path)
    if artifact_type == "pdf":
        if len(parts) < 3 or parts[:2] != ("media", "pdfs") or not path.lower().endswith(".pdf"):
            raise ArtifactVaultIntegrityError(f"Unsupported PDF inventory path: {path}")
        return ArtifactVault.pdf_object_key(digest)
    if artifact_type == "faiss":
        if len(parts) != 2 or parts[0] != "faiss_indexes":
            raise ArtifactVaultIntegrityError(f"Unsupported FAISS inventory path: {path}")
        match = re.fullmatch(r"folder_([0-9]+)\.index", parts[1])
        if not match:
            raise ArtifactVaultIntegrityError(f"Ambiguous FAISS inventory path: {path}")
        return ArtifactVault.faiss_object_key(release_id, int(match.group(1)))
    if artifact_type == "metadata":
        if path.lower().endswith(".pdf") or path.lower().endswith(".index"):
            raise ArtifactVaultIntegrityError(f"Unsupported metadata inventory path: {path}")
        if not any(path.lower().endswith(suffix) for suffix in METADATA_SUFFIXES):
            raise ArtifactVaultIntegrityError(f"Unsupported metadata inventory path: {path}")
        return "metadata/" + release_id + "/" + "/".join(parts)
    raise ArtifactVaultIntegrityError("Unsupported inventory artifact type")


def _legacy_object_key(path: str, digest: str, release_id: str) -> str:
    if path.lower().endswith(".pdf"):
        return ArtifactVault.pdf_object_key(digest)
    parts = _safe_relative_parts(path)
    if len(parts) >= 3 and parts[-3] in {"faiss", "faiss_indexes"}:
        match = re.fullmatch(r"folder_([0-9]+)\.index", parts[-1])
        if match:
            return ArtifactVault.faiss_object_key(parts[-2], int(match.group(1)))
    if any(path.lower().endswith(suffix) for suffix in METADATA_SUFFIXES):
        return "metadata/" + release_id + "/" + "/".join(parts)
    raise ArtifactVaultIntegrityError(f"Unsupported manifest path: {path}")


def _artifact_type_for_key(key: str) -> str:
    if PDF_KEY_RE.fullmatch(key):
        return "pdf"
    if FAISS_KEY_RE.fullmatch(key):
        return "faiss"
    if _is_safe_scoped_key(key, "metadata"):
        return "metadata"
    return "manifest"


def _safe_relative_parts(path: str) -> tuple[str, ...]:
    if not path or path.startswith("/") or "\\" in path:
        raise ArtifactVaultIntegrityError(f"Unsupported unsafe relative path: {path}")
    parts = tuple(path.split("/"))
    if not parts or any(not SAFE_COMPONENT_RE.fullmatch(part) for part in parts):
        raise ArtifactVaultIntegrityError(f"Unsupported unsafe relative path: {path}")
    return parts


def _is_safe_scoped_key(key: str, prefix: str) -> bool:
    parts = key.split("/")
    if len(parts) < 3:
        return False
    if parts[0] == "datasets" and len(parts) >= 4:
        return parts[2] == prefix and all(
            SAFE_COMPONENT_RE.fullmatch(part) for part in parts[1:]
        )
    return parts[0] == prefix and all(
        SAFE_COMPONENT_RE.fullmatch(part) for part in parts[1:]
    )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_bytes(payload: bytes | bytearray | BinaryIO) -> bytes:
    if hasattr(payload, "read"):
        data = payload.read()
    else:
        data = bytes(payload)
    if not isinstance(data, bytes):
        raise ArtifactVaultIntegrityError("Artifact payload must be bytes")
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_sha256(value: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ArtifactVaultIntegrityError("Expected a lowercase SHA-256 checksum")


def _validate_release_id(value: str) -> None:
    if not isinstance(value, str) or not SAFE_COMPONENT_RE.fullmatch(value):
        raise ArtifactVaultIntegrityError("Release id must be a safe immutable name")
    if value.lower() in MUTABLE_RELEASE_IDS:
        raise ArtifactVaultIntegrityError("Mutable release aliases are not allowed")


def _validate_expected_checksum(expected: str | None, actual: str) -> None:
    if expected is None:
        return
    _validate_sha256(expected)
    if expected != actual:
        raise ArtifactVaultIntegrityError("Artifact checksum does not match the manifest")


def _metadata_checksum(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("sha256")
    if not isinstance(value, str):
        raise ArtifactVaultIntegrityError("Vault object is missing its SHA-256 metadata")
    _validate_sha256(value)
    return value


def _manifest_bytes(manifest: Mapping[str, Any] | bytes | bytearray) -> tuple[bytes, dict[str, Any]]:
    if isinstance(manifest, Mapping):
        payload = dict(manifest)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return data, payload
    data = bytes(manifest)
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactVaultIntegrityError("Manifest must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactVaultIntegrityError("Manifest root must be an object")
    return data, payload
