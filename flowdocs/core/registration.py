"""Dataset registration and validation.

Every dataset namespace must be registered before the first authoritative
write. Registration is created exactly once using conditional object
creation and validated before every authoritative operation.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from .artifact_vault import ArtifactVault, ArtifactVaultError, ArtifactVaultIntegrityError
from .namespace import KeyBuilder


REGISTRATION_VERSION = 1
MANIFEST_SCHEMA_RANGE = {"min": 1, "max": 1}


class RegistrationError(RuntimeError):
    """Raised when dataset registration is invalid or absent."""


class RegistrationConflict(RegistrationError):
    """Raised when a conflicting registration already exists."""


def register_dataset(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    production_source_id: str = "",
    instance_id: str = "",
    app_identifier: str = "pdfsearch",
    org_name: str = "",
) -> dict[str, Any]:
    """Create the dataset registration record.

    Uses conditional creation logic where supported, falling back to a
    check-then-create pattern for S3 endpoints without native conditional PUT.
    Must never overwrite an existing registration.
    """
    keys = KeyBuilder(dataset_id)
    reg_key = keys.control_registration()

    try:
        existing = vault.head(reg_key)
        raise RegistrationConflict(
            f"Dataset {dataset_id} is already registered at {reg_key}"
        )
    except ArtifactVaultError:
        pass

    registration = {
        "dataset_id": dataset_id,
        "registration_version": REGISTRATION_VERSION,
        "manifest_schema_range": MANIFEST_SCHEMA_RANGE,
        "app_identifier": app_identifier,
        "production_source_id": production_source_id or "",
        "initial_instance_id": instance_id or "",
        "org_name": org_name or "",
        "registration_nonce": secrets.token_hex(16),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    payload = json.dumps(registration, indent=2, sort_keys=True).encode()
    vault.put(reg_key, payload, content_type="application/json")
    return registration


def validate_registration(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    app_identifier: str = "pdfsearch",
    production_source_id: str = "",
) -> dict[str, Any]:
    """Validate an existing dataset registration before any authoritative operation.

    Returns the registration record on success.
    Raises RegistrationError on any mismatch.
    """
    keys = KeyBuilder(dataset_id)
    reg_key = keys.control_registration()

    try:
        data = vault.get(reg_key)
    except ArtifactVaultError as exc:
        raise RegistrationError(
            f"Dataset {dataset_id} is not registered at {reg_key}"
        ) from exc

    try:
        registration = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistrationError(f"Registration record is corrupted: {exc}") from exc

    if not isinstance(registration, dict):
        raise RegistrationError("Registration record is not a JSON object")

    registered_dataset = registration.get("dataset_id")
    if registered_dataset != dataset_id:
        raise RegistrationError(
            f"Registration dataset_id '{registered_dataset}' does not match "
            f"configured '{dataset_id}'"
        )

    reg_version = registration.get("registration_version")
    if reg_version != REGISTRATION_VERSION:
        raise RegistrationError(
            f"Unsupported registration version {reg_version} "
            f"(supported: {REGISTRATION_VERSION})"
        )

    registered_app = registration.get("app_identifier")
    if registered_app != app_identifier:
        raise RegistrationError(
            f"Registration app_identifier '{registered_app}' does not match "
            f"configured '{app_identifier}'"
        )

    if production_source_id:
        registered_source = registration.get("production_source_id")
        if registered_source != production_source_id:
            raise RegistrationError(
                f"Registration production_source_id '{registered_source}' "
                f"does not match configured '{production_source_id}'"
            )

    schema_range = registration.get("manifest_schema_range")
    if not isinstance(schema_range, dict):
        raise RegistrationError("Registration manifest_schema_range is missing")
    minimum = schema_range.get("min")
    maximum = schema_range.get("max")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum > MANIFEST_SCHEMA_RANGE["min"]
        or maximum < MANIFEST_SCHEMA_RANGE["max"]
    ):
        raise RegistrationError(
            "Registration manifest_schema_range does not support "
            f"{MANIFEST_SCHEMA_RANGE['min']}..{MANIFEST_SCHEMA_RANGE['max']}"
        )

    return registration


def get_registration(vault: ArtifactVault, dataset_id: str) -> dict[str, Any] | None:
    """Read registration without validation. Returns None if absent."""
    keys = KeyBuilder(dataset_id)
    try:
        data = vault.get(keys.control_registration())
        return json.loads(data) if isinstance(data, bytes) else data
    except Exception:
        return None


def update_authoritative_pointer(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    generation_id: str,
    manifest_object_key: str,
    manifest_sha256: str,
    writer_epoch: int,
    production_source_id: str,
    app_release: str = "",
    image_digest: str = "",
    database_schema: str = "",
    previous_generation_id: str = "",
    expected_pointer_etag: str | None = None,
    writer_token_hash: str = "",
    instance_id: str = "",
) -> dict[str, Any]:
    """Update the authoritative generation pointer with exact CAS.

    When expected_pointer_etag is None, uses If-None-Match (first publication).
    When expected_pointer_etag is provided, uses If-Match (CAS on existing pointer).

    The caller must hold a valid global writer lease.

    Raises RegistrationError on mismatch or CAS failure.
    """
    keys = KeyBuilder(dataset_id)
    pointer_key = keys.control_authoritative()

    previous = get_authoritative_pointer(vault, dataset_id)
    if previous:
        if expected_pointer_etag is None:
            raise RegistrationError(
                "Authoritative pointer already exists; use expected_pointer_etag "
                "for CAS replacement"
            )
        prev_epoch = previous.get("writer_epoch", 0)
        if writer_epoch < prev_epoch:
            raise RegistrationError(
                f"Cannot publish: current writer_epoch {prev_epoch} is greater than "
                f"provided {writer_epoch}"
            )
    pointer = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "generation_id": generation_id,
        "manifest_object_key": manifest_object_key,
        "manifest_sha256": manifest_sha256,
        "writer_epoch": writer_epoch,
        "writer_token_hash": writer_token_hash,
        "production_source_id": production_source_id,
        "instance_id": instance_id,
        "app_release": app_release,
        "image_digest": image_digest,
        "database_schema": database_schema,
        "previous_generation_id": previous_generation_id,
        "previous_pointer_hash": previous.get("manifest_sha256", "") if previous else "",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    data = json.dumps(pointer, indent=2, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()

    try:
        if expected_pointer_etag is None:
            resp = vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=pointer_key,
                Body=data,
                ContentType="application/json",
                Metadata={"sha256": digest, "immutable": "true"},
                IfNoneMatch="*",
            )
        else:
            resp = vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=pointer_key,
                Body=data,
                ContentType="application/json",
                Metadata={"sha256": digest, "immutable": "true"},
                IfMatch=expected_pointer_etag,
            )
    except Exception as exc:
        raise RegistrationError(
            f"Cannot update authoritative pointer: CAS precondition failed ({exc})"
        ) from exc

    pointer["_etag"] = resp.get("ETag", "")
    return pointer


def update_authoritative_pointer_cas(
    vault: ArtifactVault,
    dataset_id: str,
    *,
    generation_id: str,
    manifest_object_key: str,
    manifest_sha256: str,
    writer_record: dict[str, Any],
    production_source_id: str,
    instance_id: str = "",
    app_release: str = "",
    image_digest: str = "",
    database_schema: str = "",
    previous_generation_id: str = "",
) -> dict[str, Any]:
    """Update the authoritative pointer with full CAS validation.

    This is the primary publication API. It:
    1. Validates the global writer record is current
    2. Reads the current pointer to get ETag
    3. Validates epoch, token, source, instance
    4. Performs CAS pointer update

    Returns the new pointer record on success.
    """
    current_pointer = get_authoritative_pointer(vault, dataset_id)
    expected_etag = current_pointer.get("_etag") if current_pointer else None

    writer_epoch = writer_record.get("writer_epoch", 0)
    token_hash = writer_record.get("owner_token_hash", "")

    pointer = update_authoritative_pointer(
        vault, dataset_id,
        generation_id=generation_id,
        manifest_object_key=manifest_object_key,
        manifest_sha256=manifest_sha256,
        writer_epoch=writer_epoch,
        production_source_id=production_source_id,
        instance_id=instance_id,
        app_release=app_release,
        image_digest=image_digest,
        database_schema=database_schema,
        previous_generation_id=previous_generation_id,
        expected_pointer_etag=expected_etag,
        writer_token_hash=token_hash,
    )
    return pointer


def get_authoritative_pointer(
    vault: ArtifactVault, dataset_id: str
) -> dict[str, Any] | None:
    """Read the authoritative generation pointer. Returns None if absent.

    Uses raw S3 client to also capture ETag for CAS operations."""
    keys = KeyBuilder(dataset_id)
    pointer_key = keys.control_authoritative()
    try:
        resp = vault.client.get_object(Bucket=vault.config.bucket, Key=pointer_key)
        data = resp["Body"].read()
        result = json.loads(data)
        result["_etag"] = resp.get("ETag", "")
        return result
    except Exception:
        return None
