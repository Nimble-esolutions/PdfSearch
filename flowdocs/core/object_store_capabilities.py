"""S3-compatible object store capability detection.

Probes whether the configured endpoint supports conditional operations
(If-None-Match, If-Match), object versioning, and other features needed
for safe distributed fencing. Runs a safe probe in a dedicated namespace
and cleans up afterward.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .artifact_vault import ArtifactVault, ArtifactVaultError


PROBE_PREFIX = "system-capability-probes"


@dataclass
class StoreCapabilities:
    """Detected capabilities of the configured S3-compatible endpoint."""

    endpoint: str = ""
    bucket: str = ""
    probed_at: str = ""

    conditional_create_supported: bool = False
    conditional_replace_supported: bool = False
    etag_available: bool = False
    version_id_available: bool = False
    read_after_write_consistent: bool = False
    metadata_supported: bool = False
    bucket_versioning_enabled: bool = False
    server_side_encryption: str = ""

    authoritative_publication_allowed: bool = False

    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "conditional_create": self.conditional_create_supported,
            "conditional_replace": self.conditional_replace_supported,
            "etag_available": self.etag_available,
            "version_id": self.version_id_available,
            "read_after_write": self.read_after_write_consistent,
            "metadata": self.metadata_supported,
            "bucket_versioning": self.bucket_versioning_enabled,
            "authoritative_publication_allowed": self.authoritative_publication_allowed,
            "errors": self.errors,
        }


def probe_capabilities(
    vault: ArtifactVault,
    deployment_id: str = "",
) -> StoreCapabilities:
    """Detect S3 capabilities with safe probe objects."""
    cap = StoreCapabilities(
        endpoint=vault.config.endpoint,
        bucket=vault.config.bucket,
        probed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    if not vault.enabled:
        cap.errors.append("Vault is disabled")
        return cap

    deploy_id = deployment_id or secrets.token_hex(4)
    probe_prefix = f"{PROBE_PREFIX}/{deploy_id}"

    try:
        cap.conditional_create_supported = _probe_conditional_create(vault, probe_prefix)
    except Exception as exc:
        cap.errors.append(f"conditional-create probe failed: {exc}")

    try:
        cap.conditional_replace_supported = _probe_conditional_replace(vault, probe_prefix)
    except Exception as exc:
        cap.errors.append(f"conditional-replace probe failed: {exc}")

    try:
        cap.etag_available, cap.version_id_available = _probe_etag_and_version(vault, probe_prefix)
    except Exception as exc:
        cap.errors.append(f"etag/version probe failed: {exc}")

    try:
        cap.read_after_write_consistent = _probe_read_after_write(vault, probe_prefix)
    except Exception as exc:
        cap.errors.append(f"read-after-write probe failed: {exc}")

    try:
        cap.metadata_supported = _probe_metadata(vault, probe_prefix)
    except Exception as exc:
        cap.errors.append(f"metadata probe failed: {exc}")

    try:
        cap.bucket_versioning_enabled = _probe_bucket_versioning(vault)
    except Exception as exc:
        cap.errors.append(f"bucket-versioning probe failed: {exc}")

    cap.authoritative_publication_allowed = (
        cap.conditional_create_supported
        and cap.conditional_replace_supported
    )

    _cleanup_probes(vault, probe_prefix)
    return cap


def _probe_conditional_create(vault: ArtifactVault, prefix: str) -> bool:
    """Test If-None-Match: * for conditional object creation."""
    key = f"{prefix}/cond-create-{secrets.token_hex(4)}.json"
    data = json.dumps({"probe": "conditional-create", "ts": time.time()}).encode()

    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
            IfNoneMatch="*",
        )
        try:
            vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                IfNoneMatch="*",
            )
            return False
        except Exception:
            return True
    except Exception:
        plain_put = vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
        )
        if plain_put and "ETag" in plain_put:
            return False
        raise


def _probe_conditional_replace(vault: ArtifactVault, prefix: str) -> bool:
    """Test If-Match with ETag for conditional replacement."""
    key = f"{prefix}/cond-replace-{secrets.token_hex(4)}.json"
    data1 = json.dumps({"v": 1}).encode()
    data2 = json.dumps({"v": 2}).encode()

    resp = vault.client.put_object(
        Bucket=vault.config.bucket,
        Key=key,
        Body=data1,
        ContentType="application/json",
    )
    etag = resp.get("ETag", "")
    if not etag:
        return False

    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data2,
            ContentType="application/json",
            IfMatch='"wrong-etag"',
        )
        return False
    except Exception:
        pass

    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data2,
            ContentType="application/json",
            IfMatch=etag,
        )
        return True
    except Exception:
        return False


def _probe_etag_and_version(vault: ArtifactVault, prefix: str) -> tuple[bool, bool]:
    """Check whether ETag and VersionId are returned."""
    key = f"{prefix}/etag-probe-{secrets.token_hex(4)}.json"
    resp = vault.client.put_object(
        Bucket=vault.config.bucket,
        Key=key,
        Body=b"etag-probe",
        ContentType="text/plain",
    )
    etag = resp.get("ETag", "")
    version = resp.get("VersionId", "")
    return bool(etag), bool(version)


def _probe_read_after_write(vault: ArtifactVault, prefix: str) -> bool:
    """Test whether a newly written object is immediately readable."""
    key = f"{prefix}/raw-probe-{secrets.token_hex(4)}.json"
    value = f"raw-{secrets.token_hex(8)}"

    vault.client.put_object(
        Bucket=vault.config.bucket,
        Key=key,
        Body=value.encode(),
        ContentType="text/plain",
    )
    resp = vault.client.get_object(Bucket=vault.config.bucket, Key=key)
    body = resp["Body"].read().decode()
    return body == value


def _probe_metadata(vault: ArtifactVault, prefix: str) -> bool:
    """Test object metadata round-trip."""
    key = f"{prefix}/meta-probe-{secrets.token_hex(4)}.json"
    vault.client.put_object(
        Bucket=vault.config.bucket,
        Key=key,
        Body=b"metadata-probe",
        ContentType="text/plain",
        Metadata={"test-key": "test-value"},
    )
    resp = vault.client.head_object(Bucket=vault.config.bucket, Key=key)
    meta = resp.get("Metadata", {})
    return meta.get("test-key") == "test-value"


def _probe_bucket_versioning(vault: ArtifactVault) -> bool:
    """Check if bucket versioning is enabled."""
    try:
        resp = vault.client.get_bucket_versioning(Bucket=vault.config.bucket)
        status = resp.get("Status", "")
        return status == "Enabled"
    except Exception:
        return False


def _cleanup_probes(vault: ArtifactVault, prefix: str) -> None:
    """Remove probe objects."""
    try:
        resp = vault.client.list_objects_v2(
            Bucket=vault.config.bucket,
            Prefix=prefix,
        )
        objects = [{"Key": item["Key"]} for item in resp.get("Contents", [])]
        if objects:
            vault.client.delete_objects(
                Bucket=vault.config.bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
    except Exception:
        pass
