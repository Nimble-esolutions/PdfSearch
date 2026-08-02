"""Read-only legacy S3 generation discovery and canonical DataOps v3 import."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .package_v3 import build_manifest, canonical_json_bytes
from .v3_backup import blob_key, publish_manifest_contract
from .v3_config import ConnectionView
from .v3_storage import put_file_immutable, verify_remote_object


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_DISCOVERED_GENERATIONS = 1_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
LEGACY_KIND_MAP = {
    "database": "database",
    "media": "media",
    "pdf_cache": "pdf_cache",
    "faiss_indexes": "faiss",
    "chroma_db": "chroma",
}


class V3LegacyImportError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class LegacyGeneration:
    bucket: str
    dataset_id: str
    generation_id: str
    manifest_key: str
    manifest_sha256: str
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class LegacyImportReceipt:
    recovery_point_id: str
    dataset_id: str
    manifest_sha256: str
    manifest_key: str
    recovery_point_key: str
    parent_dataset_id: str
    parent_generation_id: str
    parent_manifest_sha256: str
    uploaded_objects: int
    uploaded_bytes: int
    reused_objects: int
    reused_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


def legacy_manifest_key(dataset_id: str, generation_id: str) -> str:
    _safe_segment(dataset_id, "legacy_dataset_id_invalid")
    _safe_segment(generation_id, "legacy_generation_id_invalid")
    return f"datasets/{dataset_id}/generations/{generation_id}/manifest.json"


def _safe_segment(value: Any, code: str) -> str:
    value = str(value or "").strip()
    if not SAFE_SEGMENT_RE.fullmatch(value):
        raise V3LegacyImportError(code)
    return value


def _safe_path(value: Any) -> str:
    path = str(value or "")
    normalized = posixpath.normpath(path)
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized != path
    ):
        raise V3LegacyImportError("legacy_file_path_unsafe")
    return path


def _bounded_object(client, *, bucket: str, key: str, limit: int) -> bytes:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = body.read(min(1024 * 1024, limit + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > limit:
                raise V3LegacyImportError("legacy_manifest_too_large")
        return b"".join(chunks)
    except V3LegacyImportError:
        raise
    except Exception as exc:
        raise V3LegacyImportError(
            "legacy_manifest_read_failed", retryable=True
        ) from exc


def _expected_legacy_object_key(
    dataset_id: str,
    path: str,
    digest: str,
) -> str:
    if path.startswith("media/") and path.lower().endswith(".pdf"):
        return f"datasets/{dataset_id}/blobs/pdfs/sha256/{digest}.pdf"
    return f"datasets/{dataset_id}/blobs/files/{digest}"


def _validate_legacy_manifest(
    payload: Any,
    *,
    dataset_id: str,
    generation_id: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise V3LegacyImportError("legacy_manifest_invalid")
    if payload.get("manifest_version") != 1 or payload.get("read_only") is not True:
        raise V3LegacyImportError("legacy_manifest_contract_invalid")
    if payload.get("dataset_id") != dataset_id:
        raise V3LegacyImportError("legacy_manifest_dataset_mismatch")
    if payload.get("release_id") != generation_id:
        raise V3LegacyImportError("legacy_manifest_generation_mismatch")
    if not str(payload.get("production_source_id") or "").strip():
        raise V3LegacyImportError("legacy_source_identity_missing")
    source = payload.get("source")
    evidence = source.get("snapshot_evidence") if isinstance(source, Mapping) else None
    if (
        not isinstance(source, Mapping)
        or source.get("root_contract") != "read-only-volume"
        or not isinstance(evidence, Mapping)
        or evidence.get("stable") is not True
    ):
        raise V3LegacyImportError("legacy_source_stability_unproven")
    entries = payload.get("files")
    if not isinstance(entries, list) or not entries:
        raise V3LegacyImportError("legacy_files_missing")
    seen_paths: set[str] = set()
    seen_keys: dict[str, tuple[str, int]] = {}
    database_count = 0
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise V3LegacyImportError("legacy_file_entry_invalid")
        path = _safe_path(entry.get("path"))
        if path in seen_paths:
            raise V3LegacyImportError("legacy_file_path_duplicate")
        seen_paths.add(path)
        digest = str(entry.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(digest):
            raise V3LegacyImportError("legacy_file_sha256_invalid")
        size = entry.get("bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise V3LegacyImportError("legacy_file_size_invalid")
        artifact_type = str(entry.get("artifact_type") or "")
        if artifact_type not in LEGACY_KIND_MAP:
            raise V3LegacyImportError("legacy_artifact_type_unsupported")
        key = str(entry.get("object_key") or "")
        if key != _expected_legacy_object_key(dataset_id, path, digest):
            raise V3LegacyImportError("legacy_object_binding_invalid")
        previous = seen_keys.setdefault(key, (digest, size))
        if previous != (digest, size):
            raise V3LegacyImportError("legacy_object_key_conflict")
        if artifact_type == "database":
            database_count += 1
            if path != "db.sqlite3":
                raise V3LegacyImportError("legacy_database_binding_invalid")
        normalized.append(
            {
                "path": path,
                "sha256": digest,
                "size": size,
                "object_key": key,
                "kind": LEGACY_KIND_MAP[artifact_type],
            }
        )
    if database_count != 1:
        raise V3LegacyImportError("legacy_database_count_invalid")
    result = dict(payload)
    result["files"] = normalized
    return result


def load_legacy_generation(
    client,
    *,
    bucket: str,
    dataset_id: str,
    generation_id: str,
) -> LegacyGeneration:
    """Load one explicitly selected legacy generation without mutating it."""

    key = legacy_manifest_key(dataset_id, generation_id)
    raw = _bounded_object(
        client,
        bucket=bucket,
        key=key,
        limit=MAX_MANIFEST_BYTES,
    )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise V3LegacyImportError("legacy_manifest_json_invalid") from exc
    manifest = _validate_legacy_manifest(
        payload,
        dataset_id=dataset_id,
        generation_id=generation_id,
    )
    return LegacyGeneration(
        bucket=bucket,
        dataset_id=dataset_id,
        generation_id=generation_id,
        manifest_key=key,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        manifest=manifest,
    )


def discover_legacy_generations(
    client,
    *,
    bucket: str,
    dataset_id: str,
) -> list[dict[str, Any]]:
    """Read-only discovery. A separate explicit generation is still required."""

    dataset_id = _safe_segment(dataset_id, "legacy_dataset_id_invalid")
    prefix = f"datasets/{dataset_id}/generations/"
    continuation = None
    generations: list[str] = []
    try:
        while True:
            request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if continuation:
                request["ContinuationToken"] = continuation
            page = client.list_objects_v2(**request)
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if not key.endswith("/manifest.json"):
                    continue
                relative = key.removeprefix(prefix)
                parts = relative.split("/")
                if len(parts) != 2 or parts[1] != "manifest.json":
                    continue
                generations.append(
                    _safe_segment(parts[0], "legacy_generation_id_invalid")
                )
                if len(generations) > MAX_DISCOVERED_GENERATIONS:
                    raise V3LegacyImportError("legacy_generation_limit_exceeded")
            if not page.get("IsTruncated"):
                break
            continuation = str(page.get("NextContinuationToken") or "")
            if not continuation:
                raise V3LegacyImportError("legacy_discovery_pagination_invalid")
    except V3LegacyImportError:
        raise
    except Exception as exc:
        raise V3LegacyImportError("legacy_discovery_failed", retryable=True) from exc
    result = []
    for generation_id in sorted(set(generations), reverse=True):
        generation = load_legacy_generation(
            client,
            bucket=bucket,
            dataset_id=dataset_id,
            generation_id=generation_id,
        )
        result.append(
            {
                "dataset_id": dataset_id,
                "generation_id": generation_id,
                "manifest_sha256": generation.manifest_sha256,
                "created_at": str(generation.manifest.get("created_at") or ""),
                "object_count": len(generation.manifest["files"]),
            }
        )
    return result


def _stream_legacy_object(
    client,
    *,
    bucket: str,
    key: str,
    target: Path,
    expected_sha256: str,
    expected_size: int,
) -> None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        hasher = hashlib.sha256()
        observed = 0
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as output:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > expected_size:
                    raise V3LegacyImportError("legacy_object_digest_mismatch")
                output.write(chunk)
                hasher.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(target, 0o600)
    except V3LegacyImportError:
        raise
    except Exception as exc:
        raise V3LegacyImportError(
            "legacy_object_read_failed", retryable=True
        ) from exc
    if observed != expected_size or hasher.hexdigest() != expected_sha256:
        raise V3LegacyImportError("legacy_object_digest_mismatch")


def _database_evidence(path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

            def count(table: str) -> int:
                if table not in tables:
                    return 0
                return int(
                    db.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                )

            migrations = (
                [
                    {"app": row[0], "name": row[1]}
                    for row in db.execute(
                        "SELECT app, name FROM django_migrations ORDER BY app, name"
                    )
                ]
                if "django_migrations" in tables
                else []
            )
            documents = count("core_pdffile")
            folders = count("core_folder")
            users = count("auth_user")
    except sqlite3.Error as exc:
        raise V3LegacyImportError("legacy_database_invalid") from exc
    if integrity != "ok":
        raise V3LegacyImportError("legacy_database_integrity_failed")
    if foreign_keys:
        raise V3LegacyImportError("legacy_database_foreign_keys_failed")
    core_migrations = [item["name"] for item in migrations if item["app"] == "core"]
    return {
        "sqlite_integrity": "ok",
        "foreign_keys": "ok",
        "documents": documents,
        "folders": folders,
        "users": users,
        "migration_count": len(migrations),
        "database_schema": core_migrations[-1] if core_migrations else "legacy-verified",
    }


def _existing_materialization(
    final: Path,
    *,
    generation: LegacyGeneration,
) -> dict[str, Any] | None:
    receipt_path = final / ".dataops-legacy-import.json"
    if final.is_symlink() or not final.is_dir() or not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_bytes())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(receipt, dict)
        or receipt.get("verified") is not True
        or receipt.get("legacy_manifest_sha256") != generation.manifest_sha256
    ):
        return None
    for item in generation.manifest["files"]:
        path = final / item["path"]
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            return None
        hasher = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
                size += len(chunk)
        if size != item["size"] or hasher.hexdigest() != item["sha256"]:
            return None
    return receipt


def materialize_legacy_generation(
    generation: LegacyGeneration,
    *,
    client,
    quarantine_root: str | Path,
) -> dict[str, Any]:
    """Verify every source object in an isolated, retained-on-failure tree."""

    raw_root = Path(quarantine_root)
    if raw_root.is_symlink():
        raise V3LegacyImportError("legacy_quarantine_root_unsafe")
    root = raw_root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = root / (
        f"legacy-{generation.generation_id}-{generation.manifest_sha256[:12]}"
    )
    existing = _existing_materialization(final, generation=generation)
    if existing is not None:
        return {**existing, "workspace": str(final), "reused": True}
    if final.exists():
        raise V3LegacyImportError("legacy_quarantine_conflict")
    temporary = Path(tempfile.mkdtemp(prefix=".dataops-legacy-", dir=root))
    try:
        for item in generation.manifest["files"]:
            target = temporary / item["path"]
            try:
                target.resolve().relative_to(temporary)
            except ValueError as exc:
                raise V3LegacyImportError("legacy_file_path_unsafe") from exc
            _stream_legacy_object(
                client,
                bucket=generation.bucket,
                key=item["object_key"],
                target=target,
                expected_sha256=item["sha256"],
                expected_size=item["size"],
            )
        database = _database_evidence(temporary / "db.sqlite3")
        pdf_count = sum(
            item["kind"] == "media" and item["path"].lower().endswith(".pdf")
            for item in generation.manifest["files"]
        )
        if database["documents"] != pdf_count:
            raise V3LegacyImportError("legacy_document_count_mismatch")
        receipt = {
            "schema_version": 3,
            "verified": True,
            "legacy_dataset_id": generation.dataset_id,
            "legacy_generation_id": generation.generation_id,
            "legacy_manifest_sha256": generation.manifest_sha256,
            "object_count": len(generation.manifest["files"]),
            "byte_count": sum(item["size"] for item in generation.manifest["files"]),
            "database": database,
            "pdf_count": pdf_count,
        }
        receipt_path = temporary / ".dataops-legacy-import.json"
        receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")
        os.chmod(receipt_path, 0o600)
        os.replace(temporary, final)
        return {**receipt, "workspace": str(final), "reused": False}
    except Exception:
        failed = root / f"failed-{generation.generation_id}-{uuid.uuid4().hex[:12]}"
        try:
            os.replace(temporary, failed)
        except OSError:
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def import_legacy_generation(
    *,
    generation: LegacyGeneration,
    materialization: Mapping[str, Any],
    destination: ConnectionView,
    destination_client,
    recovery_point_id: str,
    destination_instance_id: str,
    destination_environment: str,
    signing_key: bytes,
    signing_key_id: str,
) -> tuple[dict[str, Any], LegacyImportReceipt]:
    """Publish one destination-owned v3 point from verified legacy bytes."""

    if materialization.get("verified") is not True:
        raise V3LegacyImportError("legacy_materialization_unverified")
    if materialization.get("legacy_manifest_sha256") != generation.manifest_sha256:
        raise V3LegacyImportError("legacy_materialization_manifest_mismatch")
    workspace = Path(str(materialization.get("workspace") or ""))
    if workspace.is_symlink() or not workspace.is_dir():
        raise V3LegacyImportError("legacy_materialization_missing")
    workspace = workspace.resolve()
    uploaded_objects = uploaded_bytes = reused_objects = reused_bytes = 0
    files = []
    for item in generation.manifest["files"]:
        path = workspace / item["path"]
        try:
            path.resolve().relative_to(workspace)
        except ValueError as exc:
            raise V3LegacyImportError("legacy_file_path_unsafe") from exc
        outcome = put_file_immutable(
            destination_client,
            bucket=destination.bucket,
            key=blob_key(destination, item["sha256"]),
            path=path,
            sha256=item["sha256"],
            size=item["size"],
        )
        verify_remote_object(
            destination_client,
            bucket=destination.bucket,
            key=blob_key(destination, item["sha256"]),
            sha256=item["sha256"],
            size=item["size"],
        )
        if outcome == "uploaded":
            uploaded_objects += 1
            uploaded_bytes += item["size"]
        else:
            reused_objects += 1
            reused_bytes += item["size"]
        files.append(
            {
                "path": item["path"],
                "kind": item["kind"],
                "size": item["size"],
                "sha256": item["sha256"],
                "blob": f"sha256:{item['sha256']}",
            }
        )
    database = materialization.get("database")
    if not isinstance(database, Mapping):
        raise V3LegacyImportError("legacy_database_evidence_missing")
    kinds = {item["kind"] for item in files}
    components = {
        "database": {"complete": True, "coherent": True, "rebuild_required": False},
        "media": {"complete": True, "coherent": True, "rebuild_required": False},
    }
    for name in ("pdf_cache", "faiss", "chroma"):
        components[name] = {
            "complete": True,
            "coherent": name not in {"faiss", "chroma"} and name in kinds,
            "rebuild_required": name in {"faiss", "chroma"},
        }
    application = {
        "image_digest": str(generation.manifest.get("image_digest") or "legacy-image-unknown"),
        "release_version": str(generation.manifest.get("app_release") or "legacy-release"),
        "database_schema": str(database.get("database_schema") or "legacy-verified"),
        "index_configuration_sha256": hashlib.sha256(
            canonical_json_bytes(
                {
                    "embedding_index": generation.manifest.get("embedding_index") or {},
                    "runtime_trees": generation.manifest.get("runtime_trees") or [],
                }
            )
        ).hexdigest(),
    }
    manifest = build_manifest(
        recovery_point_id=recovery_point_id,
        dataset_id=destination.dataset_id,
        source_instance_id=destination_instance_id,
        source_environment=destination_environment,
        backup_mode="import",
        captured_epoch=0,
        application=application,
        consistency={
            "sqlite_integrity": database.get("sqlite_integrity"),
            "foreign_keys": database.get("foreign_keys"),
            "source_stable": True,
        },
        components=components,
        files=files,
        counts={
            "documents": int(database.get("documents", 0)),
            "folders": int(database.get("folders", 0)),
            "users": int(database.get("users", 0)),
            "objects": len(files),
        },
        lineage={
            "transition": "legacy_import",
            "parent_dataset_id": generation.dataset_id,
            "parent_generation_id": generation.generation_id,
            "parent_manifest_sha256": generation.manifest_sha256,
        },
        created_at=str(generation.manifest.get("created_at") or "") or None,
    )
    signed, publication = publish_manifest_contract(
        connection=destination,
        client=destination_client,
        manifest=manifest,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
    )
    return signed, LegacyImportReceipt(
        recovery_point_id=publication.recovery_point_id,
        dataset_id=publication.dataset_id,
        manifest_sha256=publication.manifest_sha256,
        manifest_key=publication.manifest_key,
        recovery_point_key=publication.recovery_point_key,
        parent_dataset_id=generation.dataset_id,
        parent_generation_id=generation.generation_id,
        parent_manifest_sha256=generation.manifest_sha256,
        uploaded_objects=uploaded_objects,
        uploaded_bytes=uploaded_bytes,
        reused_objects=reused_objects,
        reused_bytes=reused_bytes,
    )
