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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PDF_KEY_RE = re.compile(r"^pdfs/sha256/([0-9a-f]{64})\.pdf$")
FAISS_KEY_RE = re.compile(r"^faiss/([A-Za-z0-9][A-Za-z0-9._-]*)/(folder_[0-9]+\.index)$")


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

    def put_manifest(self, manifest: Mapping[str, Any] | bytes | bytearray) -> ArtifactMetadata:
        """Validate and upload an already-generated inventory manifest."""
        self._require_enabled()
        data, payload = _manifest_bytes(manifest)
        self.validate_manifest(payload)
        release_id = payload["release_id"]
        key = self.manifest_object_key(release_id)
        return self.put(key, data, content_type="application/json")

    def validate_manifest(self, manifest: Mapping[str, Any] | bytes | bytearray) -> dict[str, Any]:
        """Validate a manifest without making a provider call."""
        self._require_enabled()
        _, payload = _manifest_bytes(manifest)
        release_id = payload.get("release_id")
        if not isinstance(release_id, str) or not SAFE_COMPONENT_RE.fullmatch(release_id):
            raise ArtifactVaultIntegrityError("Manifest release_id is missing or invalid")
        _validate_manifest(payload)
        return payload

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
        if not SAFE_COMPONENT_RE.fullmatch(generation_id):
            raise ArtifactVaultConfigurationError("FAISS generation id contains invalid characters")
        if not isinstance(folder_id, int) or folder_id < 1:
            raise ArtifactVaultConfigurationError("FAISS folder id must be a positive integer")
        return f"faiss/{generation_id}/folder_{folder_id}.index"

    @staticmethod
    def manifest_object_key(release_id: str) -> str:
        if not SAFE_COMPONENT_RE.fullmatch(release_id):
            raise ArtifactVaultConfigurationError("Manifest release id contains invalid characters")
        return f"manifests/{release_id}.json"

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
        if FAISS_KEY_RE.fullmatch(key):
            return
        if re.fullmatch(r"manifests/[A-Za-z0-9][A-Za-z0-9._-]*\.json", key):
            return
        raise ArtifactVaultConfigurationError("Unsupported immutable artifact key")


def object_key_for_manifest_entry(entry: Mapping[str, Any]) -> str:
    """Resolve a manifest entry to its immutable vault key without guessing."""
    object_key = entry.get("object_key")
    if isinstance(object_key, str):
        ArtifactVault._validate_immutable_key(object_key)
        return object_key

    path = entry.get("path")
    digest = entry.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ArtifactVaultIntegrityError("Manifest entry needs path, sha256, and bytes")
    _validate_sha256(digest)
    if path.lower().endswith(".pdf"):
        return ArtifactVault.pdf_object_key(digest)

    path_parts = Path(path).parts
    if len(path_parts) >= 3 and path_parts[-3] in {"faiss", "faiss_indexes"}:
        generation_id = path_parts[-2]
        filename = path_parts[-1]
        match = re.fullmatch(r"folder_([0-9]+)\.index", filename)
        if match:
            return ArtifactVault.faiss_object_key(generation_id, int(match.group(1)))
    raise ArtifactVaultIntegrityError(
        "FAISS manifest entries need object_key or a generation-bound path"
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


def _validate_manifest(payload: Mapping[str, Any]) -> None:
    files = payload.get("files")
    if not isinstance(files, list):
        raise ArtifactVaultIntegrityError("Manifest files must be a list")
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ArtifactVaultIntegrityError("Manifest file entries must be objects")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if not isinstance(entry.get("path"), str):
            raise ArtifactVaultIntegrityError("Manifest file entry path is required")
        _validate_sha256(digest)
        if not isinstance(size, int) or size < 0:
            raise ArtifactVaultIntegrityError("Manifest file entry bytes must be non-negative")
        if "object_key" in entry:
            resolved_key = object_key_for_manifest_entry(entry)
            pdf_match = PDF_KEY_RE.fullmatch(resolved_key)
            if pdf_match and pdf_match.group(1) != digest:
                raise ArtifactVaultIntegrityError(
                    "Manifest PDF object key does not match its checksum"
                )
