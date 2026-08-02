"""Explicit cross-dataset clone/rebind for immutable legacy generations.

Normal Data Operations transfers intentionally reject dataset mismatches.  This
module is the only path that may cross that boundary, and it keeps the source
read-only while creating a new destination registration and compare-and-swap
authoritative pointer.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .config import ResolvedProfile
from .pipeline import (
    DataOpsPipelineError,
    _provider_code,
    _safe_release_id,
    clone_confirmation_phrase,
    clone_destination_generation_id,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_APP_IDENTIFIER = "pdfsearch"
_MANIFEST_VERSION = 1
_REGISTRATION_VERSION = 1
_MAX_CONTROL_OBJECT_BYTES = 8 * 1024 * 1024


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    # Match the operator migration utility, including its trailing newline.
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest_key(dataset_id: str, generation_id: str) -> str:
    return f"datasets/{dataset_id}/generations/{generation_id}/manifest.json"


def _registration_key(dataset_id: str) -> str:
    return f"datasets/{dataset_id}/control/registration.json"


def _pointer_key(dataset_id: str) -> str:
    return f"datasets/{dataset_id}/control/authoritative.json"


def _not_found(exc: Exception) -> bool:
    return isinstance(exc, (KeyError, FileNotFoundError)) or _provider_code(exc) in {
        "404",
        "NoSuchKey",
        "NotFound",
        "NoSuchBucket",
    }


def _optional_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> tuple[bytes | None, str]:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _not_found(exc):
            return None, ""
        code = _provider_code(exc)
        raise DataOpsPipelineError(
            "source_permission_denied" if code in {"403", "AccessDenied"} else "source_read_failed",
            stage="transfer",
            retryable=code not in {"403", "AccessDenied"},
        ) from exc
    try:
        body = response["Body"]
        data = body.read(max_bytes + 1)
    except Exception as exc:
        raise DataOpsPipelineError("source_read_failed", stage="transfer", retryable=True) from exc
    if len(data) > max_bytes:
        raise DataOpsPipelineError("object_exceeds_limit", stage="transfer", retryable=False)
    return bytes(data), str(response.get("ETag", ""))


def _required_object(client: Any, *, bucket: str, key: str, code: str) -> bytes:
    data, _etag = _optional_object(client, bucket=bucket, key=key)
    if data is None:
        raise DataOpsPipelineError(code, stage="manifest_verification", retryable=False, details={"key": key})
    return data


def _immutable_put(client: Any, *, bucket: str, key: str, body: bytes) -> str:
    """Create an object once, allowing only an exact-content retry."""
    expected = hashlib.sha256(body).hexdigest()
    existing, _etag = _optional_object(client, bucket=bucket, key=key)
    if existing is not None:
        if existing != body:
            raise DataOpsPipelineError("destination_immutable_conflict", stage="transfer", retryable=False, details={"key": key})
        return "already-present"

    kwargs = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": "application/json" if key.endswith(".json") else "application/octet-stream",
        "Metadata": {"sha256": expected, "immutable": "true"},
    }
    try:
        client.put_object(**kwargs, IfNoneMatch="*")
    except TypeError as exc:
        # A client that cannot express conditional creation is not trusted for
        # an immutable lineage.  Do not silently fall back to overwrite.
        raise DataOpsPipelineError("conditional_write_unsupported", stage="transfer", retryable=False) from exc
    except Exception as exc:
        if _provider_code(exc) not in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
            code = _provider_code(exc)
            raise DataOpsPipelineError(
                "destination_permission_denied" if code in {"403", "AccessDenied"} else "destination_write_failed",
                stage="transfer",
                retryable=code not in {"403", "AccessDenied"},
            ) from exc

    verified, _etag = _optional_object(client, bucket=bucket, key=key)
    if verified is None:
        raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=True, details={"key": key})
    if verified != body or hashlib.sha256(verified).hexdigest() != expected:
        raise DataOpsPipelineError("destination_immutable_conflict", stage="manifest_verification", retryable=False, details={"key": key})
    return "uploaded"


def _safe_path(value: object) -> str:
    path = str(value or "").strip().lstrip("/")
    normalized = posixpath.normpath(path)
    if (
        not path
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized != path
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise DataOpsPipelineError("manifest_path_invalid", stage="manifest_verification", retryable=False)
    return path


def _content_addressed_key(dataset_id: str, key: object, digest: str) -> str:
    value = _safe_path(key)
    prefix = f"datasets/{dataset_id}/blobs/"
    if not value.startswith(prefix):
        raise DataOpsPipelineError("manifest_object_key_outside_dataset", stage="manifest_verification", retryable=False, details={"key": value})
    leaf = posixpath.basename(value)
    if leaf.removesuffix(".pdf") != digest:
        raise DataOpsPipelineError("manifest_object_key_not_content_addressed", stage="manifest_verification", retryable=False, details={"key": value})
    return value


def _validate_custom_manifest(payload: Mapping[str, Any], dataset_id: str, generation_id: str) -> list[dict[str, Any]]:
    if payload.get("manifest_version") != _MANIFEST_VERSION or payload.get("read_only") is not True:
        raise DataOpsPipelineError("clone_manifest_schema_invalid", stage="manifest_verification", retryable=False)
    if payload.get("dataset_id") != dataset_id or payload.get("release_id") != generation_id:
        raise DataOpsPipelineError("manifest_identity_mismatch", stage="manifest_verification", retryable=False)
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification", retryable=False)
    entries: list[dict[str, Any]] = []
    paths: set[str] = set()
    objects: dict[str, tuple[str, int]] = {}
    for raw in files:
        if not isinstance(raw, Mapping):
            raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification", retryable=False)
        path = _safe_path(raw.get("path"))
        digest = str(raw.get("sha256") or "").strip().lower()
        size = raw.get("bytes")
        if path in paths or not _SHA256_RE.fullmatch(digest) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise DataOpsPipelineError("manifest_files_invalid", stage="manifest_verification", retryable=False)
        key = _content_addressed_key(dataset_id, raw.get("object_key") or raw.get("key"), digest)
        signature = (digest, size)
        if key in objects and objects[key] != signature:
            raise DataOpsPipelineError("manifest_object_collision", stage="manifest_verification", retryable=False, details={"key": key})
        paths.add(path)
        objects[key] = signature
        entries.append({**dict(raw), "path": path, "object_key": key, "sha256": digest, "bytes": size})
    counts = payload.get("counts")
    if isinstance(counts, Mapping) and counts.get("files") is not None and counts.get("files") != len(files):
        raise DataOpsPipelineError("manifest_count_mismatch", stage="manifest_verification", retryable=False)
    database_entries = [entry for entry in entries if entry.get("artifact_type") == "database"]
    if len(database_entries) != 1:
        raise DataOpsPipelineError("manifest_database_binding_invalid", stage="manifest_verification", retryable=False)
    database = payload.get("database")
    if not isinstance(database, Mapping) or database.get("entry") != database_entries[0]:
        raise DataOpsPipelineError("manifest_database_binding_invalid", stage="manifest_verification", retryable=False)
    for field, artifact_type, count_field in (
        ("pdf_storage", "media", "file_count"),
        ("faiss", "faiss_indexes", "count"),
        ("chroma", "chroma_db", "count"),
    ):
        section = payload.get(field)
        if not isinstance(section, Mapping):
            raise DataOpsPipelineError("manifest_artifact_evidence_invalid", stage="manifest_verification", retryable=False)
        selected = [entry for entry in entries if entry.get("artifact_type") == artifact_type and (artifact_type != "media" or entry["path"].lower().endswith(".pdf"))]
        if section.get(count_field) != len(selected) or section.get("files") != selected:
            raise DataOpsPipelineError("manifest_artifact_evidence_invalid", stage="manifest_verification", retryable=False)
    return entries


def _rewrite_dataset_bindings(value: Any, source_prefix: str, destination_prefix: str) -> Any:
    if isinstance(value, str) and value.startswith(source_prefix):
        return destination_prefix + value[len(source_prefix):]
    if isinstance(value, list):
        return [_rewrite_dataset_bindings(item, source_prefix, destination_prefix) for item in value]
    if isinstance(value, Mapping):
        return {key: _rewrite_dataset_bindings(item, source_prefix, destination_prefix) for key, item in value.items()}
    return value


def _registration_payload(dataset_id: str, production_source_id: str, lineage: Mapping[str, str]) -> bytes:
    return _canonical_json(
        {
            "dataset_id": dataset_id,
            "registration_version": _REGISTRATION_VERSION,
            "manifest_schema_range": {"min": _MANIFEST_VERSION, "max": _MANIFEST_VERSION},
            "app_identifier": _APP_IDENTIFIER,
            "production_source_id": production_source_id,
            "initial_instance_id": "dataops-clone-rebind",
            "migration_source": "clone/rebind",
            "parent_dataset_id": lineage["parent_dataset_id"],
            "parent_generation_id": lineage["parent_generation_id"],
            "parent_manifest_sha256": lineage["parent_manifest_sha256"],
            "clone_reason": lineage["clone_reason"],
        }
    )


def _ensure_registration(
    client: Any,
    destination: ResolvedProfile,
    *,
    production_source_id: str,
    lineage: Mapping[str, str],
) -> dict[str, Any]:
    key = _registration_key(destination.dataset_id)
    body, _etag = _optional_object(client, bucket=destination.bucket, key=key, max_bytes=_MAX_CONTROL_OBJECT_BYTES)
    if body is not None:
        try:
            registration = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataOpsPipelineError("destination_registration_invalid", stage="transfer", retryable=False) from exc
        if not isinstance(registration, Mapping):
            raise DataOpsPipelineError("destination_registration_invalid", stage="transfer", retryable=False)
        if (
            registration.get("dataset_id") != destination.dataset_id
            or registration.get("registration_version") != _REGISTRATION_VERSION
            or registration.get("app_identifier") != _APP_IDENTIFIER
            or registration.get("production_source_id") != production_source_id
        ):
            raise DataOpsPipelineError("destination_registration_conflict", stage="transfer", retryable=False)
        schema_range = registration.get("manifest_schema_range")
        if (
            not isinstance(schema_range, Mapping)
            or not isinstance(schema_range.get("min"), int)
            or isinstance(schema_range.get("min"), bool)
            or not isinstance(schema_range.get("max"), int)
            or isinstance(schema_range.get("max"), bool)
            or schema_range.get("min") > _MANIFEST_VERSION
            or schema_range.get("max") < _MANIFEST_VERSION
        ):
            raise DataOpsPipelineError("destination_registration_schema_mismatch", stage="transfer", retryable=False)
        return dict(registration)
    payload = _registration_payload(destination.dataset_id, production_source_id, lineage)
    _immutable_put(client, bucket=destination.bucket, key=key, body=payload)
    verified = _required_object(client, bucket=destination.bucket, key=key, code="destination_registration_missing")
    try:
        registration = json.loads(verified.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataOpsPipelineError("destination_registration_invalid", stage="transfer", retryable=False) from exc
    if not isinstance(registration, Mapping) or registration.get("dataset_id") != destination.dataset_id:
        raise DataOpsPipelineError("destination_registration_invalid", stage="transfer", retryable=False)
    return dict(registration)


def _pointer_from_body(body: bytes, destination: ResolvedProfile) -> dict[str, Any]:
    try:
        pointer = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataOpsPipelineError("destination_pointer_invalid", stage="health_check", retryable=False) from exc
    if not isinstance(pointer, Mapping) or pointer.get("schema_version") != 1:
        raise DataOpsPipelineError("destination_pointer_invalid", stage="health_check", retryable=False)
    generation_id = str(pointer.get("generation_id") or "")
    if (
        pointer.get("dataset_id") != destination.dataset_id
        or not _SHA256_RE.fullmatch(str(pointer.get("manifest_sha256") or ""))
        or not generation_id
        or pointer.get("manifest_object_key") != _manifest_key(destination.dataset_id, generation_id)
    ):
        raise DataOpsPipelineError("destination_pointer_invalid", stage="health_check", retryable=False)
    if not isinstance(pointer.get("writer_epoch"), int) or pointer.get("writer_epoch") < 0:
        raise DataOpsPipelineError("destination_pointer_invalid", stage="health_check", retryable=False)
    return dict(pointer)


def _publish_pointer(
    client: Any,
    destination: ResolvedProfile,
    *,
    pointer: Mapping[str, Any],
) -> dict[str, Any]:
    key = _pointer_key(destination.dataset_id)
    existing_body, etag = _optional_object(client, bucket=destination.bucket, key=key, max_bytes=_MAX_CONTROL_OBJECT_BYTES)
    if existing_body is not None:
        existing = _pointer_from_body(existing_body, destination)
        if (
            existing.get("generation_id") == pointer.get("generation_id")
            and existing.get("manifest_sha256") == pointer.get("manifest_sha256")
        ):
            return existing
        if not etag:
            try:
                head = client.head_object(Bucket=destination.bucket, Key=key)
                etag = str(head.get("ETag", ""))
            except Exception as exc:
                raise DataOpsPipelineError("pointer_cas_unavailable", stage="health_check", retryable=False) from exc
        if not etag:
            raise DataOpsPipelineError("pointer_cas_unavailable", stage="health_check", retryable=False)
        kwargs = {"IfMatch": etag}
    else:
        kwargs = {"IfNoneMatch": "*"}
    body = _canonical_json(dict(pointer))
    try:
        client.put_object(
            Bucket=destination.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            Metadata={"sha256": hashlib.sha256(body).hexdigest(), "immutable": "true"},
            **kwargs,
        )
    except TypeError as exc:
        raise DataOpsPipelineError("conditional_write_unsupported", stage="health_check", retryable=False) from exc
    except Exception as exc:
        code = _provider_code(exc)
        if code in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
            raise DataOpsPipelineError("pointer_conflict", stage="health_check", retryable=False) from exc
        raise DataOpsPipelineError(
            "destination_permission_denied" if code in {"403", "AccessDenied"} else "destination_pointer_write_failed",
            stage="health_check",
            retryable=code not in {"403", "AccessDenied"},
        ) from exc
    verified = _required_object(client, bucket=destination.bucket, key=key, code="destination_pointer_missing")
    actual = _pointer_from_body(verified, destination)
    if actual.get("generation_id") != pointer.get("generation_id") or actual.get("manifest_sha256") != pointer.get("manifest_sha256"):
        raise DataOpsPipelineError("destination_pointer_verify_failed", stage="health_check", retryable=False)
    return actual


def clone_rebind_generation(
    source_client: Any,
    source: ResolvedProfile,
    destination_client: Any,
    destination: ResolvedProfile,
    source_generation_id: str,
    *,
    destination_generation_id: str = "",
    confirmation: str = "",
    clone_reason: str = "stage-rehearsal",
) -> dict[str, Any]:
    """Clone one verified legacy v1 generation into another dataset."""
    if not source.can_restore:
        raise DataOpsPipelineError("profile_role_disallows_restore", stage="preflight", retryable=False)
    if not destination.can_backup:
        raise DataOpsPipelineError("profile_role_disallows_backup", stage="preflight", retryable=False)
    if source.dataset_id == destination.dataset_id:
        raise DataOpsPipelineError("clone_requires_distinct_dataset", stage="preflight", retryable=False)
    source_generation_id = _safe_release_id(source_generation_id)
    destination_generation_id = clone_destination_generation_id(source_generation_id, destination_generation_id)
    destination_generation_id = _safe_release_id(destination_generation_id)
    clone_reason = str(clone_reason or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", clone_reason):
        raise DataOpsPipelineError("clone_reason_invalid", stage="preflight", retryable=False)
    expected_confirmation = clone_confirmation_phrase(
        source.key,
        source_generation_id,
        destination.key,
        destination_generation_id,
        clone_reason,
    )
    if confirmation != expected_confirmation:
        raise DataOpsPipelineError("clone_confirmation_required", stage="preflight", retryable=False)

    manifest_key = _manifest_key(source.dataset_id, source_generation_id)
    source_body = _required_object(source_client, bucket=source.bucket, key=manifest_key, code="source_manifest_missing")
    source_digest = hashlib.sha256(source_body).hexdigest()
    try:
        source_manifest = json.loads(source_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataOpsPipelineError("clone_manifest_invalid", stage="manifest_verification", retryable=False) from exc
    if not isinstance(source_manifest, Mapping):
        raise DataOpsPipelineError("clone_manifest_invalid", stage="manifest_verification", retryable=False)
    source_entries = _validate_custom_manifest(source_manifest, source.dataset_id, source_generation_id)

    source_prefix = f"datasets/{source.dataset_id}/"
    destination_prefix = f"datasets/{destination.dataset_id}/"
    destination_entries: list[dict[str, Any]] = []
    object_map: dict[str, tuple[str, int]] = {}
    total_bytes = 0
    for entry in source_entries:
        source_key = str(entry["object_key"])
        destination_key = destination_prefix + source_key[len(source_prefix):]
        signature = (entry["sha256"], entry["bytes"])
        if destination_key in object_map and object_map[destination_key] != signature:
            raise DataOpsPipelineError("destination_immutable_conflict", stage="transfer", retryable=False, details={"key": destination_key})
        object_map[destination_key] = signature
        data = _required_object(source_client, bucket=source.bucket, key=source_key, code="source_object_missing")
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise DataOpsPipelineError("manifest_checksum_mismatch", stage="manifest_verification", retryable=False, details={"key": source_key})
        _immutable_put(destination_client, bucket=destination.bucket, key=destination_key, body=data)
        verified = _required_object(destination_client, bucket=destination.bucket, key=destination_key, code="destination_verify_failed")
        if len(verified) != len(data) or hashlib.sha256(verified).hexdigest() != entry["sha256"]:
            raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False, details={"key": destination_key})
        rewritten = dict(entry)
        if "object_key" in rewritten:
            rewritten["object_key"] = destination_key
        if "key" in rewritten:
            rewritten["key"] = destination_key
        destination_entries.append(rewritten)
        total_bytes += len(data)

    rebased = _rewrite_dataset_bindings(source_manifest, source_prefix, destination_prefix)
    rebased["dataset_id"] = destination.dataset_id
    rebased["release_id"] = destination_generation_id
    rebased["files"] = destination_entries
    production_source_id = str(source_manifest.get("production_source_id") or source.source_id or destination.source_id).strip()
    source_info = dict(rebased.get("source") or {})
    source_info.update(
        {
            "profile": source.key,
            "source_id": source.source_id,
            "cloned_from_profile": source.key,
            "cloned_from_bucket": source.bucket,
        }
    )
    rebased["source"] = source_info
    lineage = {
        "parent_dataset_id": source.dataset_id,
        "parent_generation_id": source_generation_id,
        "parent_manifest_sha256": source_digest,
        "clone_reason": clone_reason,
    }
    rebased.update(lineage)
    # A clone/rebind is a read-only repack of the source generation.  Preserve
    # an explicit producer marker so the restore compatibility gate can accept
    # an intentionally repacked legacy generation without treating arbitrary
    # release or image mismatches as safe.
    rebased["repacked_from_generation_id"] = source_generation_id
    rebased["clone"] = {
        "operation": "clone/rebind",
        "source_profile": source.key,
        "destination_profile": destination.key,
        "destination_bucket": destination.bucket,
        **lineage,
    }
    _validate_custom_manifest(rebased, destination.dataset_id, destination_generation_id)
    destination_manifest_body = _canonical_json(rebased)
    destination_manifest_key = _manifest_key(destination.dataset_id, destination_generation_id)
    _immutable_put(destination_client, bucket=destination.bucket, key=destination_manifest_key, body=destination_manifest_body)
    verified_manifest = _required_object(destination_client, bucket=destination.bucket, key=destination_manifest_key, code="destination_manifest_missing")
    destination_digest = hashlib.sha256(destination_manifest_body).hexdigest()
    if verified_manifest != destination_manifest_body or hashlib.sha256(verified_manifest).hexdigest() != destination_digest:
        raise DataOpsPipelineError("destination_verify_failed", stage="manifest_verification", retryable=False, details={"key": destination_manifest_key})

    registration = _ensure_registration(
        destination_client,
        destination,
        production_source_id=production_source_id,
        lineage=lineage,
    )
    pointer_key = _pointer_key(destination.dataset_id)
    existing_pointer_body, _existing_etag = _optional_object(destination_client, bucket=destination.bucket, key=pointer_key, max_bytes=_MAX_CONTROL_OBJECT_BYTES)
    existing_pointer = _pointer_from_body(existing_pointer_body, destination) if existing_pointer_body is not None else None
    writer_epoch = int(existing_pointer.get("writer_epoch", -1)) + 1 if existing_pointer else 0
    pointer = {
        "schema_version": 1,
        "dataset_id": destination.dataset_id,
        "generation_id": destination_generation_id,
        "manifest_object_key": destination_manifest_key,
        "manifest_sha256": destination_digest,
        "production_source_id": production_source_id,
        "writer_epoch": writer_epoch,
        "published_at": datetime.now(timezone.utc).isoformat(),
        **lineage,
    }
    authoritative = _publish_pointer(destination_client, destination, pointer=pointer)
    return {
        "release_id": destination_generation_id,
        "source_generation_id": source_generation_id,
        "destination_generation_id": destination_generation_id,
        "manifest": rebased,
        "manifest_digest": destination_digest,
        "source_manifest_digest": source_digest,
        "manifest_key": destination_manifest_key,
        "pointer_key": pointer_key,
        "registration_key": _registration_key(destination.dataset_id),
        "objects": len(destination_entries),
        "bytes": total_bytes,
        "source_profile": source.key,
        "destination_profile": destination.key,
        "registration": {"dataset_id": registration.get("dataset_id", ""), "production_source_id": registration.get("production_source_id", "")},
        "authoritative_pointer": {"generation_id": authoritative.get("generation_id", ""), "manifest_sha256": authoritative.get("manifest_sha256", ""), "writer_epoch": authoritative.get("writer_epoch", 0)},
        "lineage": lineage,
    }


__all__ = ["clone_rebind_generation"]
