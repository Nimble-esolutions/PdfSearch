import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.artifact_vault import object_metadata_value
from core.namespace import KeyBuilder
from vaultops.models import (
    ArtifactGeneration,
    VaultConnectionProfile,
    VaultDatasetProjection,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NATIVE_EXECUTABLE_SUFFIXES = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".msi",
    ".ps1",
    ".py",
    ".sh",
    ".so",
}
ALLOWED_PATH_PREFIXES = {
    "media",
    "pdf_cache",
    "faiss_indexes",
    "chroma_db",
    "staticfiles",
}


class InventoryError(RuntimeError):
    reason_code = "vault_inventory_invalid"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class VerifiedGeneration:
    registration: dict
    registration_digest: str
    pointer: dict
    pointer_digest: str
    pointer_etag: str
    manifest: dict
    manifest_digest: str
    manifest_key: str
    generation_id: str
    authoritative: bool
    file_count: int
    byte_count: int


def _provider_error_code(exc):
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    return str(error.get("Code", ""))


def _read_json(vault, key, *, kind, missing_allowed=False):
    try:
        response = vault.client.get_object(
            Bucket=vault.config.bucket,
            Key=key,
        )
    except Exception as exc:
        code = _provider_error_code(exc)
        if missing_allowed and code in {"404", "NoSuchKey", "NotFound"}:
            return None
        if code in {"403", "AccessDenied"}:
            raise InventoryError(f"{kind}_access_denied") from exc
        raise InventoryError(f"{kind}_read_failed") from exc
    body = response.get("Body")
    try:
        data = body.read(settings.VAULT_MAX_MANIFEST_BYTES + 1)
    except Exception as exc:
        raise InventoryError(f"{kind}_read_failed") from exc
    if len(data) > settings.VAULT_MAX_MANIFEST_BYTES:
        raise InventoryError(f"{kind}_too_large")
    digest = hashlib.sha256(data).hexdigest()
    stored_digest = object_metadata_value(response.get("Metadata"), "sha256", "")
    if not stored_digest or stored_digest != digest:
        raise InventoryError(f"{kind}_digest_mismatch")
    try:
        payload = json.loads(data)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise InventoryError(f"{kind}_malformed") from exc
    if not isinstance(payload, dict):
        raise InventoryError(f"{kind}_malformed")
    return payload, digest, response.get("ETag", "")


def _validate_registration(registration, profile):
    if (
        registration.get("dataset_id") != profile.dataset_id
        or registration.get("registration_version") != 1
        or registration.get("app_identifier") != "pdfsearch"
        or not registration.get("production_source_id")
        or (
            profile.production_source_id
            and registration.get("production_source_id")
            != profile.production_source_id
        )
    ):
        raise InventoryError("registration_identity_mismatch")
    schema_range = registration.get("manifest_schema_range")
    if (
        not isinstance(schema_range, dict)
        or schema_range.get("min") != 1
        or schema_range.get("max") != 1
    ):
        raise InventoryError("registration_schema_range_unsupported")


def _validate_pointer(pointer, profile, registration):
    generation_id = pointer.get("generation_id")
    manifest_digest = pointer.get("manifest_sha256")
    if (
        pointer.get("schema_version") != 1
        or not isinstance(generation_id, str)
        or not generation_id
        or not SHA256_RE.fullmatch(str(manifest_digest or ""))
        or pointer.get("production_source_id")
        != registration.get("production_source_id")
    ):
        raise InventoryError("authoritative_pointer_invalid")
    expected_key = KeyBuilder(profile.dataset_id).generation_manifest(
        generation_id
    )
    if pointer.get("manifest_object_key") != expected_key:
        raise InventoryError("authoritative_pointer_dataset_mismatch")


def _safe_manifest_path(raw_path):
    if not isinstance(raw_path, str) or not raw_path:
        raise InventoryError("manifest_path_invalid")
    path = PurePosixPath(raw_path)
    if (
        path.is_absolute()
        or "\\" in raw_path
        or ".." in path.parts
        or any(not part or part in {".", ".."} for part in path.parts)
    ):
        raise InventoryError("manifest_path_unsafe")
    if raw_path == "db.sqlite3":
        return path
    if not path.parts or path.parts[0] not in ALLOWED_PATH_PREFIXES:
        raise InventoryError("manifest_category_unsupported")
    if path.suffix.lower() in NATIVE_EXECUTABLE_SUFFIXES:
        raise InventoryError("manifest_executable_rejected")
    return path


def _validate_manifest(manifest, profile, generation_id, manifest_digest):
    if (
        manifest.get("manifest_version") != 1
        or manifest.get("read_only") is not True
        or manifest.get("release_id") != generation_id
        or manifest.get("dataset_id") != profile.dataset_id
        or (
            profile.production_source_id
            and manifest.get("production_source_id")
            != profile.production_source_id
        )
    ):
        raise InventoryError("generation_manifest_identity_mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise InventoryError("generation_manifest_files_missing")
    if len(files) > settings.VAULT_MAX_MANIFEST_OBJECTS:
        raise InventoryError("generation_manifest_object_limit")

    seen_paths = set()
    database_count = 0
    byte_count = 0
    expected_prefix = f"datasets/{profile.dataset_id}/blobs/"
    normalized = []
    for entry in files:
        if not isinstance(entry, dict):
            raise InventoryError("generation_manifest_entry_invalid")
        path = _safe_manifest_path(entry.get("path"))
        path_key = path.as_posix().casefold()
        if path_key in seen_paths:
            raise InventoryError("manifest_path_collision")
        seen_paths.add(path_key)
        if path.as_posix() == "db.sqlite3":
            database_count += 1
        digest = entry.get("sha256")
        object_key = entry.get("object_key")
        size = entry.get("bytes")
        if (
            not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            or not isinstance(object_key, str)
            or not object_key.startswith(expected_prefix)
            or digest not in object_key
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise InventoryError("generation_manifest_entry_invalid")
        byte_count += size
        if byte_count > settings.VAULT_MAX_GENERATION_BYTES:
            raise InventoryError("generation_manifest_byte_limit")
        normalized.append(
            {
                **entry,
                "path": path.as_posix(),
                "bytes": size,
                "sha256": digest,
                "object_key": object_key,
            }
        )
    if database_count != 1:
        raise InventoryError("generation_database_cardinality_invalid")
    manifest["files"] = normalized
    manifest["_digest"] = manifest_digest
    return len(normalized), byte_count


def _verify_objects(vault, files):
    for entry in files:
        try:
            response = vault.client.head_object(
                Bucket=vault.config.bucket,
                Key=entry["object_key"],
            )
        except Exception as exc:
            code = _provider_error_code(exc)
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise InventoryError("generation_object_missing") from exc
            if code in {"403", "AccessDenied"}:
                raise InventoryError("generation_object_access_denied") from exc
            raise InventoryError("generation_object_head_failed") from exc
        if (
            object_metadata_value(response.get("Metadata"), "sha256")
            != entry["sha256"]
            or int(response.get("ContentLength", -1)) != entry["bytes"]
        ):
            raise InventoryError("generation_object_digest_mismatch")


def verify_generation(
    vault,
    profile,
    *,
    generation_id="",
    require_authoritative=False,
    verify_objects=True,
):
    """Verify registration → pointer → manifest → object metadata."""
    if not profile.enabled:
        raise InventoryError("vault_profile_disabled")
    keys = KeyBuilder(profile.dataset_id)
    registration_result = _read_json(
        vault,
        keys.control_registration(),
        kind="registration",
    )
    registration, registration_digest, _ = registration_result
    _validate_registration(registration, profile)

    pointer_result = _read_json(
        vault,
        keys.control_authoritative(),
        kind="authoritative_pointer",
    )
    pointer, pointer_digest, pointer_etag = pointer_result
    _validate_pointer(pointer, profile, registration)
    authoritative_generation = pointer["generation_id"]
    selected_generation = generation_id or authoritative_generation
    if require_authoritative and selected_generation != authoritative_generation:
        raise InventoryError("generation_not_authoritative")
    manifest_key = keys.generation_manifest(selected_generation)
    manifest, manifest_digest, _ = _read_json(
        vault,
        manifest_key,
        kind="generation_manifest",
    )
    if (
        selected_generation == authoritative_generation
        and (
            pointer["manifest_object_key"] != manifest_key
            or pointer["manifest_sha256"] != manifest_digest
        )
    ):
        raise InventoryError("authoritative_manifest_digest_mismatch")
    file_count, byte_count = _validate_manifest(
        manifest,
        profile,
        selected_generation,
        manifest_digest,
    )
    if verify_objects:
        _verify_objects(vault, manifest["files"])
    return VerifiedGeneration(
        registration=registration,
        registration_digest=registration_digest,
        pointer=pointer,
        pointer_digest=pointer_digest,
        pointer_etag=pointer_etag,
        manifest=manifest,
        manifest_digest=manifest_digest,
        manifest_key=manifest_key,
        generation_id=selected_generation,
        authoritative=selected_generation == authoritative_generation,
        file_count=file_count,
        byte_count=byte_count,
    )


def project_verified_generation(profile, verified):
    with transaction.atomic(using="control"):
        projection, _ = VaultDatasetProjection.objects.update_or_create(
            profile=profile,
            dataset_id=profile.dataset_id,
            defaults={
                "registration_digest": verified.registration_digest,
                "pointer_digest": verified.pointer_digest,
                "pointer_etag": verified.pointer_etag,
                "authoritative_generation_id": verified.pointer[
                    "generation_id"
                ],
                "inventory_state": "verified",
                "inventory_observed_at": timezone.now(),
            },
        )
        generation, _ = ArtifactGeneration.objects.update_or_create(
            profile=profile,
            dataset_id=profile.dataset_id,
            generation_id=verified.generation_id,
            defaults={
                "manifest_digest": verified.manifest_digest,
                "manifest": {
                    key: value
                    for key, value in verified.manifest.items()
                    if not key.startswith("_")
                },
                "vault_state": (
                    ArtifactGeneration.VaultState.AUTHORITATIVE
                    if verified.authoritative
                    else ArtifactGeneration.VaultState.CANDIDATE
                ),
                "source": "verified-inventory",
                "observed_at": timezone.now(),
            },
        )
    return projection, generation


def list_generation_ids(vault, profile, *, max_pages=100):
    """Paginate dataset-scoped manifest keys without synchronous HEAD calls."""
    prefix = f"{KeyBuilder(profile.dataset_id).prefix()}/generations/"
    ids = []
    token = None
    for _ in range(max_pages):
        params = {"Bucket": vault.config.bucket, "Prefix": prefix}
        if token:
            params["ContinuationToken"] = token
        try:
            response = vault.client.list_objects_v2(**params)
        except Exception as exc:
            raise InventoryError("generation_listing_failed") from exc
        for item in response.get("Contents", []):
            key = item.get("Key", "")
            generation_id = keysafe_generation_id(profile, key)
            if generation_id:
                ids.append(generation_id)
        if not response.get("IsTruncated"):
            return sorted(set(ids), reverse=True)
        token = response.get("NextContinuationToken")
        if not token:
            raise InventoryError("generation_listing_cursor_missing")
    raise InventoryError("generation_listing_page_limit")


def keysafe_generation_id(profile, key):
    if not isinstance(key, str) or not key.endswith("/manifest.json"):
        return ""
    value = KeyBuilder(profile.dataset_id).extract_generation_id(key)
    if not value:
        return ""
    try:
        expected = KeyBuilder(profile.dataset_id).generation_manifest(value)
    except ValueError:
        return ""
    return value if expected == key else ""
