"""Canonical Data Operations v3 recovery-point manifest contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 3
SIGNATURE_ALGORITHM = "hmac-sha256"
BACKUP_MODES = frozenset({"smart", "deep", "import"})
COMPONENT_NAMES = frozenset({"database", "media", "pdf_cache", "faiss", "chroma"})
REQUIRED_COMPONENTS = frozenset({"database", "media"})
FORBIDDEN_KEY_PARTS = frozenset(
    {
        "access_key",
        "credential",
        "credentials",
        "password",
        "secret",
        "secret_key",
        "token",
    }
)


class ManifestV3Error(ValueError):
    """Typed contract failure safe to surface without payload values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def unsigned_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(manifest))
    payload.pop("signature", None)
    payload.pop("manifest_sha256", None)
    return payload


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(unsigned_manifest(manifest))).hexdigest()


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEY_PARTS or any(
                normalized.endswith(f"_{part}") for part in FORBIDDEN_KEY_PARTS
            ):
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _required_text(container: Mapping[str, Any], field: str, code: str) -> str:
    value = str(container.get(field) or "").strip()
    if not value:
        raise ManifestV3Error(code)
    return value


def _validate_sha256(value: Any, code: str, *, allow_empty: bool = False) -> str:
    digest = str(value or "").strip().lower()
    if allow_empty and not digest:
        return ""
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ManifestV3Error(code)
    return digest


def _validate_timestamp(value: Any) -> None:
    timestamp = str(value or "").strip()
    if not timestamp:
        raise ManifestV3Error("created_at_missing")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestV3Error("created_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ManifestV3Error("created_at_timezone_missing")


def _validate_relative_path(value: Any) -> str:
    path = str(value or "")
    if not path or "\\" in path or path.startswith("/"):
        raise ManifestV3Error("file_path_unsafe")
    normalized = posixpath.normpath(path)
    if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized != path:
        raise ManifestV3Error("file_path_unsafe")
    return path


def _validate_files(files: Any) -> None:
    if not isinstance(files, list):
        raise ManifestV3Error("files_invalid")
    seen_paths: set[str] = set()
    seen_blobs: dict[str, int] = {}
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ManifestV3Error("file_entry_invalid")
        path = _validate_relative_path(entry.get("path"))
        if path in seen_paths:
            raise ManifestV3Error("file_path_duplicate")
        seen_paths.add(path)
        kind = str(entry.get("kind") or "").strip()
        if kind not in COMPONENT_NAMES:
            raise ManifestV3Error("file_kind_invalid")
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestV3Error("file_size_invalid")
        digest = _validate_sha256(entry.get("sha256"), "file_sha256_invalid")
        if str(entry.get("blob") or "") != f"sha256:{digest}":
            raise ManifestV3Error("file_blob_reference_invalid")
        previous_size = seen_blobs.setdefault(digest, size)
        if previous_size != size:
            raise ManifestV3Error("blob_size_conflict")


def _validate_components(components: Any, files: Any) -> None:
    if not isinstance(components, Mapping):
        raise ManifestV3Error("components_invalid")
    if set(components) - COMPONENT_NAMES:
        raise ManifestV3Error("component_unknown")
    if REQUIRED_COMPONENTS - set(components):
        raise ManifestV3Error("required_component_missing")
    for name, component in components.items():
        if not isinstance(component, Mapping):
            raise ManifestV3Error("component_invalid")
        complete = component.get("complete")
        if not isinstance(complete, bool):
            raise ManifestV3Error("component_completeness_invalid")
        if name in REQUIRED_COMPONENTS and complete is not True:
            raise ManifestV3Error("authoritative_component_incomplete")
        rebuild_required = component.get("rebuild_required", False)
        if not isinstance(rebuild_required, bool):
            raise ManifestV3Error("component_rebuild_flag_invalid")
        if component.get("coherent") is not None and not isinstance(component.get("coherent"), bool):
            raise ManifestV3Error("component_coherence_invalid")
    chroma = components.get("chroma")
    chroma_present = any(
        isinstance(entry, Mapping) and entry.get("kind") == "chroma"
        for entry in files
    )
    if chroma_present and (
        not isinstance(chroma, Mapping)
        or chroma.get("coherent") is not False
        or chroma.get("rebuild_required") is not True
    ):
        raise ManifestV3Error("chroma_component_evidence_invalid")


def _validate_lineage(lineage: Any, dataset_id: str) -> None:
    if not isinstance(lineage, Mapping):
        raise ManifestV3Error("lineage_invalid")
    transition = str(lineage.get("transition") or "").strip()
    if transition not in {"", "backup", "legacy_import", "import_rebind"}:
        raise ManifestV3Error("lineage_transition_invalid")
    if transition == "import_rebind":
        parent_dataset = _required_text(
            lineage,
            "parent_dataset_id",
            "lineage_parent_dataset_missing",
        )
        _required_text(
            lineage,
            "parent_generation_id",
            "lineage_parent_generation_missing",
        )
        _validate_sha256(
            lineage.get("parent_manifest_sha256"),
            "lineage_parent_manifest_invalid",
        )
        if parent_dataset == dataset_id:
            raise ManifestV3Error("lineage_rebind_dataset_unchanged")
    elif any(
        lineage.get(field)
        for field in (
            "parent_dataset_id",
            "parent_generation_id",
            "parent_manifest_sha256",
        )
    ):
        if not lineage.get("parent_dataset_id") or not lineage.get("parent_generation_id"):
            raise ManifestV3Error("lineage_parent_incomplete")
        _validate_sha256(
            lineage.get("parent_manifest_sha256"),
            "lineage_parent_manifest_invalid",
        )


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    require_signature: bool = True,
) -> None:
    if not isinstance(manifest, Mapping):
        raise ManifestV3Error("manifest_invalid")
    if _contains_forbidden_key(manifest):
        raise ManifestV3Error("manifest_contains_secret_field")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestV3Error("schema_version_invalid")
    _required_text(manifest, "recovery_point_id", "recovery_point_id_missing")
    dataset_id = _required_text(manifest, "dataset_id", "dataset_id_missing")
    _required_text(manifest, "source_instance_id", "source_instance_id_missing")
    source_environment = _required_text(
        manifest,
        "source_environment",
        "source_environment_missing",
    ).lower()
    if source_environment not in {
        "development",
        "staging",
        "production",
        "review",
        "test",
    }:
        raise ManifestV3Error("source_environment_invalid")
    _validate_timestamp(manifest.get("created_at"))
    if manifest.get("backup_mode") not in BACKUP_MODES:
        raise ManifestV3Error("backup_mode_invalid")
    if not isinstance(manifest.get("captured_epoch"), int) or isinstance(
        manifest.get("captured_epoch"), bool
    ) or manifest.get("captured_epoch") < 0:
        raise ManifestV3Error("captured_epoch_invalid")
    base_digest = manifest.get("base_manifest_sha256")
    if base_digest:
        _validate_sha256(base_digest, "base_manifest_invalid")

    application = manifest.get("application")
    if not isinstance(application, Mapping):
        raise ManifestV3Error("application_invalid")
    _required_text(application, "image_digest", "application_image_missing")
    _required_text(application, "release_version", "application_release_missing")
    _required_text(application, "database_schema", "database_schema_missing")
    _validate_sha256(
        application.get("index_configuration_sha256"),
        "index_configuration_invalid",
    )

    consistency = manifest.get("consistency")
    if not isinstance(consistency, Mapping):
        raise ManifestV3Error("consistency_invalid")
    if consistency.get("sqlite_integrity") != "ok":
        raise ManifestV3Error("sqlite_integrity_failed")
    if consistency.get("foreign_keys") != "ok":
        raise ManifestV3Error("sqlite_foreign_keys_failed")
    if consistency.get("source_stable") is not True:
        raise ManifestV3Error("source_not_stable")

    _validate_files(manifest.get("files"))
    _validate_components(manifest.get("components"), manifest.get("files"))
    if not isinstance(manifest.get("counts"), Mapping):
        raise ManifestV3Error("counts_invalid")
    for value in manifest["counts"].values():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ManifestV3Error("count_invalid")
    _validate_lineage(manifest.get("lineage"), dataset_id)

    declared_digest = _validate_sha256(
        manifest.get("manifest_sha256"),
        "manifest_digest_invalid",
    )
    if not hmac.compare_digest(declared_digest, manifest_digest(manifest)):
        raise ManifestV3Error("manifest_digest_mismatch")

    signature = manifest.get("signature")
    if require_signature and not isinstance(signature, Mapping):
        raise ManifestV3Error("manifest_signature_missing")
    if signature is not None:
        if not isinstance(signature, Mapping):
            raise ManifestV3Error("manifest_signature_invalid")
        if signature.get("algorithm") != SIGNATURE_ALGORITHM:
            raise ManifestV3Error("manifest_signature_algorithm_invalid")
        _required_text(signature, "key_id", "manifest_signature_key_missing")
        _validate_sha256(signature.get("value"), "manifest_signature_invalid")


def finalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy with the canonical digest and without a signature."""

    finalized = deepcopy(dict(manifest))
    finalized.pop("signature", None)
    finalized["manifest_sha256"] = manifest_digest(finalized)
    validate_manifest(finalized, require_signature=False)
    return finalized


def sign_manifest(
    manifest: Mapping[str, Any],
    *,
    key: bytes,
    key_id: str,
) -> dict[str, Any]:
    if not key:
        raise ManifestV3Error("manifest_signing_key_missing")
    if not str(key_id or "").strip():
        raise ManifestV3Error("manifest_signature_key_missing")
    finalized = finalize_manifest(manifest)
    digest = finalized["manifest_sha256"]
    signature_value = hmac.new(key, digest.encode("ascii"), hashlib.sha256).hexdigest()
    finalized["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": str(key_id).strip(),
        "value": signature_value,
    }
    validate_manifest(finalized)
    return finalized


def verify_manifest_signature(manifest: Mapping[str, Any], *, key: bytes) -> bool:
    try:
        validate_manifest(manifest)
    except ManifestV3Error:
        return False
    signature = manifest["signature"]
    expected = hmac.new(
        key,
        str(manifest["manifest_sha256"]).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(str(signature["value"]), expected)


def build_manifest(
    *,
    recovery_point_id: str,
    dataset_id: str,
    source_instance_id: str,
    source_environment: str,
    backup_mode: str,
    captured_epoch: int,
    application: Mapping[str, Any],
    consistency: Mapping[str, Any],
    components: Mapping[str, Any],
    files: Iterable[Mapping[str, Any]],
    counts: Mapping[str, int],
    lineage: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    base_manifest_sha256: str = "",
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "recovery_point_id": str(recovery_point_id),
        "dataset_id": str(dataset_id),
        "source_instance_id": str(source_instance_id),
        "source_environment": str(source_environment),
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "backup_mode": str(backup_mode),
        "base_manifest_sha256": str(base_manifest_sha256 or ""),
        "captured_epoch": captured_epoch,
        "application": deepcopy(dict(application)),
        "consistency": deepcopy(dict(consistency)),
        "components": deepcopy(dict(components)),
        "files": [deepcopy(dict(item)) for item in files],
        "counts": deepcopy(dict(counts)),
        "lineage": deepcopy(
            dict(
                lineage
                or {
                    "parent_dataset_id": "",
                    "parent_generation_id": "",
                    "parent_manifest_sha256": "",
                    "transition": "backup",
                }
            )
        ),
    }
    return finalize_manifest(payload)
