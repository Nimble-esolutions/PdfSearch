"""Global writer control record using S3 conditional operations.

Provides the cross-deployment fencing layer. Uses If-None-Match for first
acquisition and If-Match with ETag for renewal, handover, and takeover.
This prevents two production deployments with separate Redis instances
from both publishing as the authoritative writer.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from .artifact_vault import ArtifactVault, ArtifactVaultError
from .namespace import KeyBuilder

WRITER_CONTROL_SCHEMA = 1


class GlobalWriterError(RuntimeError):
    """Raised when global writer acquisition or mutation fails."""


class GlobalWriterHeld(GlobalWriterError):
    """Another writer holds the global authority."""


class GlobalWriterConflict(GlobalWriterError):
    """CAS precondition failed during writer control mutation."""


def _writer_key(dataset_id: str) -> str:
    return KeyBuilder(dataset_id).control_global_writer()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def acquire_global_writer(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    production_source_id: str,
    instance_id: str,
    deployment_id: str = "",
    replica_id: str = "",
    app_release: str = "",
    image_digest: str = "",
    ttl_seconds: int = 300,
    force_takeover: bool = False,
    takeover_reason: str = "",
) -> dict[str, Any]:
    """Acquire global writer authority for a dataset.

    First acquisition uses If-None-Match: * (conditional create).
    Takeover uses If-Match with current ETag (conditional replace).

    Returns the writer control record on success.
    Raises GlobalWriterHeld if another writer is active.
    Raises GlobalWriterConflict on CAS failure.
    """
    from .object_store_capabilities import probe_capabilities
    caps = probe_capabilities(vault)
    if not caps.authoritative_publication_allowed:
        raise GlobalWriterError(
            "S3 endpoint does not support conditional operations required "
            "for global writer fencing"
        )

    key = _writer_key(dataset_id)
    now = time.time()
    token = secrets.token_hex(32)
    epoch = 1

    existing = _read_writer_record(vault, key)
    if existing is not None:
        existing_expiry = existing.get("expires_at", 0)
        existing_epoch = existing.get("writer_epoch", 0)

        if existing_expiry > now and not force_takeover:
            raise GlobalWriterHeld(
                f"Writer held by {existing.get('instance_id', 'unknown')} "
                f"(epoch {existing_epoch}, expires in {max(0, existing_expiry - now):.0f}s)"
            )

        expected_etag = existing.get("_etag", "")
        epoch = existing_epoch + 1
        if not expected_etag and not existing_expiry:
            epoch = existing_epoch

    record = {
        "schema_version": WRITER_CONTROL_SCHEMA,
        "dataset_id": dataset_id,
        "production_source_id": production_source_id,
        "instance_id": instance_id,
        "deployment_id": deployment_id,
        "replica_id": replica_id,
        "owner_token_hash": _token_hash(token),
        "writer_epoch": epoch,
        "acquired_at": now,
        "heartbeat_at": now,
        "expires_at": now + ttl_seconds,
        "previous_epoch": existing.get("writer_epoch") if existing else 0,
        "app_release": app_release,
        "image_digest": image_digest,
        "force_takeover": force_takeover,
        "takeover_reason": takeover_reason if force_takeover else "",
    }

    data = json.dumps(record, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()

    try:
        if existing is None:
            resp = vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                Metadata={"sha256": digest, "immutable": "true"},
                IfNoneMatch="*",
            )
        else:
            resp = vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                Metadata={"sha256": digest, "immutable": "true"},
                IfMatch=expected_etag,
            )
    except Exception as exc:
        raise GlobalWriterConflict(
            f"Cannot acquire global writer for {dataset_id}: {exc}"
        ) from exc

    record["_etag"] = resp.get("ETag", "")
    record["_token"] = token
    return record


def renew_global_writer(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    writer_record: dict[str, Any],
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Renew the global writer lease using conditional replacement."""
    key = _writer_key(dataset_id)
    now = time.time()
    expected_etag = writer_record.get("_etag", "")

    if not expected_etag:
        raise GlobalWriterError("Writer record has no ETag for conditional renewal")

    record = {**writer_record}
    record["heartbeat_at"] = now
    record["expires_at"] = now + ttl_seconds
    record.pop("_etag", None)
    record.pop("_token", None)

    data = json.dumps(record, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()

    try:
        resp = vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
            Metadata={"sha256": digest, "immutable": "true"},
            IfMatch=expected_etag,
        )
    except Exception as exc:
        raise GlobalWriterConflict(
            f"Cannot renew global writer for {dataset_id}: {exc}"
        ) from exc

    record["_etag"] = resp.get("ETag", "")
    record["_token"] = writer_record.get("_token", "")
    return record


def release_global_writer(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    writer_record: dict[str, Any],
) -> None:
    """Expire only the caller's current writer authority using exact CAS."""
    key = _writer_key(dataset_id)
    current = _read_writer_record(vault, key)
    if current is None:
        raise GlobalWriterConflict("Cannot verify global writer before release")
    caller_token_hash = _token_hash(writer_record.get("_token", ""))
    if (
        not caller_token_hash
        or current.get("owner_token_hash") != caller_token_hash
        or current.get("writer_epoch") != writer_record.get("writer_epoch")
    ):
        raise GlobalWriterConflict("Global writer ownership changed before release")
    expected_etag = current.get("_etag", "")
    if not expected_etag:
        raise GlobalWriterConflict("Global writer has no ETag for conditional release")
    released = {
        key: value
        for key, value in current.items()
        if not key.startswith("_")
    }
    released["heartbeat_at"] = time.time()
    released["expires_at"] = 0
    released["released_at"] = datetime.now(timezone.utc).isoformat()
    data = json.dumps(released, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()
    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
            Metadata={"sha256": digest},
            IfMatch=expected_etag,
        )
    except Exception as exc:
        raise GlobalWriterConflict(
            "Global writer ownership changed during release"
        ) from exc


def get_global_writer(
    vault: ArtifactVault, dataset_id: str
) -> dict[str, Any] | None:
    """Read the current global writer record."""
    return _read_writer_record(vault, _writer_key(dataset_id))


def validate_writer_for_publication(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    writer_record: dict[str, Any],
    production_source_id: str,
    instance_id: str = "",
    expected_epoch: int | None = None,
) -> dict[str, Any]:
    """Validate that the caller holds valid global writer authority for publication.

    Returns the validated (re-read) record.
    Raises GlobalWriterError if any condition fails.
    """
    current = get_global_writer(vault, dataset_id)
    if current is None:
        raise GlobalWriterError(f"No active global writer for {dataset_id}")

    now = time.time()
    if current.get("expires_at", 0) < now:
        raise GlobalWriterError("Global writer lease has expired")

    caller_token_hash = _token_hash(writer_record.get("_token", ""))
    current_token_hash = current.get("owner_token_hash", "")
    if caller_token_hash != current_token_hash:
        raise GlobalWriterError("Ownership token does not match global writer")

    if current.get("production_source_id") != production_source_id:
        raise GlobalWriterError("Production source ID mismatch")

    if instance_id and current.get("instance_id") != instance_id:
        raise GlobalWriterError("Instance ID mismatch")

    if expected_epoch is not None and current.get("writer_epoch") != expected_epoch:
        raise GlobalWriterError(
            f"Writer epoch mismatch: expected {expected_epoch}, "
            f"got {current.get('writer_epoch')}"
        )

    current["_token"] = writer_record.get("_token", "")
    return current


def _read_writer_record(
    vault: ArtifactVault, key: str
) -> dict[str, Any] | None:
    try:
        resp = vault.client.get_object(Bucket=vault.config.bucket, Key=key)
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        error = response.get("Error", {}) or {}
        if str(error.get("Code", "")) in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return None
        raise GlobalWriterError("global_writer_read_failed") from exc
    try:
        data = resp["Body"].read()
        record = json.loads(data)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise GlobalWriterError("global_writer_record_malformed") from exc
    if not isinstance(record, dict):
        raise GlobalWriterError("global_writer_record_malformed")
    digest = hashlib.sha256(data).hexdigest()
    stored_digest = (resp.get("Metadata") or {}).get("sha256", "")
    if stored_digest and stored_digest != digest:
        raise GlobalWriterError("global_writer_digest_mismatch")
    record["_etag"] = resp.get("ETag", "")
    return record
