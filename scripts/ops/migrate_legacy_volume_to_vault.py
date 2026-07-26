#!/usr/bin/env python3
"""Publish a mutation-checked legacy snapshot to the artifact vault.

This operator-side utility is intentionally outside Django. Publication always
creates an immutable candidate. Moving the authoritative pointer is a separate,
explicitly confirmed operation protected by S3 compare-and-swap and a fenced
global-writer record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


DEFAULT_BUCKET = "ai-sahakar-prod-flowdocs-artifact-vault"
DEFAULT_DATASET_ID = "ai-sahakar-prod"
DEFAULT_PRODUCTION_SOURCE_ID = "ai-sahakar-prod"
APP_IDENTIFIER = "pdfsearch"
MANIFEST_VERSION = 1
REGISTRATION_VERSION = 1
WRITER_SCHEMA_VERSION = 1
MAX_CONTROL_OBJECT_BYTES = 8 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
RUNTIME_TREES = ("media", "faiss_indexes", "chroma_db", "pdf_cache")
OPTIONAL_CUSTODY_TREES = ("staticfiles",)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(r"(?:^|[-_])(\d{8}T\d{6}Z)(?:[-_]|$)")


class MigrationError(RuntimeError):
    """Safe, operator-facing migration failure."""


class SourceChangedError(MigrationError):
    """The source changed while the stable snapshot was being created."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def validate_id(value: str, label: str) -> str:
    if (
        not value
        or "/" in value
        or "\\" in value
        or ".." in value
        or any(ord(char) < 32 for char in value)
    ):
        raise MigrationError(f"Unsafe {label}")
    if len(value) > 120:
        raise MigrationError(f"{label} is too long")
    return value


def safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MigrationError(f"Path escapes source root: {path}") from exc


def _validate_manifest_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MigrationError("Manifest contains an invalid path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        raise MigrationError(f"Manifest path is unsafe: {value}")
    if parsed.as_posix() != value:
        raise MigrationError(f"Manifest path is not normalized: {value}")
    return value


def object_key(dataset_id: str, generation_id: str, relative_path: str, digest: str) -> str:
    if relative_path.lower().endswith(".pdf") and relative_path.startswith("media/"):
        return f"datasets/{dataset_id}/blobs/pdfs/sha256/{digest}.pdf"
    if relative_path == "db.sqlite3":
        return f"datasets/{dataset_id}/generations/{generation_id}/database.sqlite3"
    return f"datasets/{dataset_id}/blobs/files/{digest}"


def manifest_key(dataset_id: str, generation_id: str) -> str:
    return f"datasets/{dataset_id}/generations/{generation_id}/manifest.json"


def registration_key(dataset_id: str) -> str:
    return f"datasets/{dataset_id}/control/registration.json"


def pointer_key(dataset_id: str) -> str:
    return f"datasets/{dataset_id}/control/authoritative.json"


def writer_key(dataset_id: str) -> str:
    return f"datasets/{dataset_id}/control/writer.json"


def _stat_signature(path: Path) -> dict[str, int]:
    result = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(result.st_mode):
        raise MigrationError(f"Source entry is not a regular file: {path}")
    return {
        "device": result.st_dev,
        "inode": result.st_ino,
        "size": result.st_size,
        "mtime_ns": result.st_mtime_ns,
        "ctime_ns": result.st_ctime_ns,
    }


def _assert_no_symlink(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise MigrationError(f"Symlinks are not accepted in source snapshots: {relative}")


def _selected_source_files(
    source_root: Path,
    *,
    include_backups: bool,
    include_static: bool,
) -> list[tuple[Path, str, str]]:
    trees = list(RUNTIME_TREES)
    if include_static:
        trees.extend(OPTIONAL_CUSTODY_TREES)
    if include_backups:
        trees.append("backups")

    selected: list[tuple[Path, str, str]] = []
    for tree in trees:
        root = source_root / tree
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise MigrationError(f"Source tree is not a regular directory: {root}")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise MigrationError(f"Symlinks are not accepted in source snapshots: {path}")
            if not path.is_file():
                continue
            _assert_no_symlink(source_root, path)
            relative = safe_relative(source_root, path)
            artifact_type = "backup" if tree == "backups" else tree
            selected.append((path, relative, artifact_type))
    return selected


def _database_family(database: Path) -> list[Path]:
    candidates = [database, Path(f"{database}-wal"), Path(f"{database}-shm")]
    return [path for path in candidates if path.exists()]


def capture_source_state(
    source_root: Path,
    database: Path,
    *,
    include_backups: bool,
    include_static: bool,
) -> tuple[dict[str, dict[str, int]], list[tuple[Path, str, str]]]:
    selected = _selected_source_files(
        source_root,
        include_backups=include_backups,
        include_static=include_static,
    )
    state = {
        f"tree:{relative}": _stat_signature(path)
        for path, relative, _artifact_type in selected
    }
    for path in _database_family(database):
        state[f"database:{path.name}"] = _stat_signature(path)
    return state, selected


def _copy_stable_file(source: Path, destination: Path, expected: dict[str, int]) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    source_fd = os.open(source, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(source_fd)
        opened_signature = {
            "device": opened.st_dev,
            "inode": opened.st_ino,
            "size": opened.st_size,
            "mtime_ns": opened.st_mtime_ns,
            "ctime_ns": opened.st_ctime_ns,
        }
        if opened_signature != expected:
            raise SourceChangedError(f"Source changed before copy: {source}")
        with os.fdopen(source_fd, "rb", closefd=False) as input_stream:
            with destination.open("xb") as output_stream:
                for chunk in iter(lambda: input_stream.read(COPY_CHUNK_BYTES), b""):
                    output_stream.write(chunk)
                    digest.update(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        after = os.fstat(source_fd)
        after_signature = {
            "device": after.st_dev,
            "inode": after.st_ino,
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
        }
        if after_signature != expected or _stat_signature(source) != expected:
            raise SourceChangedError(f"Source changed during copy: {source}")
    finally:
        os.close(source_fd)
    return digest.hexdigest()


def snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise MigrationError(f"SQLite database not found: {source}")
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
        integrity = target_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError(f"SQLite integrity check failed: {integrity}")
        foreign_keys = target_conn.execute("PRAGMA foreign_key_check").fetchmany(1)
        if foreign_keys:
            raise MigrationError("SQLite foreign-key check failed")
    finally:
        target_conn.close()
        source_conn.close()


def create_stable_snapshot(
    source_root: Path,
    database: Path,
    workspace: Path,
    *,
    include_backups: bool,
    include_static: bool,
) -> dict[str, Any]:
    initial, selected = capture_source_state(
        source_root,
        database,
        include_backups=include_backups,
        include_static=include_static,
    )
    for source, relative, _artifact_type in selected:
        _copy_stable_file(source, workspace / relative, initial[f"tree:{relative}"])
    snapshot_sqlite(database, workspace / "db.sqlite3")
    final, final_selected = capture_source_state(
        source_root,
        database,
        include_backups=include_backups,
        include_static=include_static,
    )
    final_paths = [relative for _path, relative, _kind in final_selected]
    initial_paths = [relative for _path, relative, _kind in selected]
    if final != initial or final_paths != initial_paths:
        raise SourceChangedError(
            "consistent_snapshot_unproven: source changed while snapshot was created"
        )
    return {
        "algorithm": "two-scan-copy-plus-sqlite-backup/v1",
        "source_state_sha256": hashlib.sha256(canonical_json(initial)).hexdigest(),
        "source_file_count": len(selected),
        "database_family_count": len(_database_family(database)),
        "stable": True,
    }


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_writable(root: Path) -> None:
    if not root.exists():
        return
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def sqlite_metadata(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        migrations: list[dict[str, str]] = []
        if "django_migrations" in tables:
            migrations = [
                {"app": row[0], "name": row[1]}
                for row in connection.execute(
                    "SELECT app, name FROM django_migrations ORDER BY app, name"
                )
            ]
        core_migrations = [row["name"] for row in migrations if row["app"] == "core"]
        return {
            "path": "db.sqlite3",
            "contract": "in-contract",
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "sqlite": {"tables": tables, "table_count": len(tables)},
            "migrations": {
                "applied": migrations,
                "count": len(migrations),
                "latest": core_migrations[-1] if core_migrations else None,
            },
        }
    finally:
        connection.close()


def inventory_source(
    source_root: Path,
    snapshot_path: Path,
    generation_id: str,
    dataset_id: str,
    include_backups: bool,
    *,
    include_static: bool = False,
    created_at: str | None = None,
    source_label: str = "",
    source_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []

    def add(path: Path, relative_path: str, artifact_type: str) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        files.append(
            {
                "path": relative_path,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "object_key": object_key(
                    dataset_id, generation_id, relative_path, digest
                ),
                "artifact_type": artifact_type,
            }
        )

    add(snapshot_path, "db.sqlite3", "database")
    trees = list(RUNTIME_TREES)
    if include_static:
        trees.extend(OPTIONAL_CUSTODY_TREES)
    for tree in trees:
        root = source_root / tree
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            add(path, safe_relative(source_root, path), tree)
    if include_backups:
        root = source_root / "backups"
        if root.is_dir():
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                add(path, safe_relative(source_root, path), "backup")

    database_entry = next(item for item in files if item["artifact_type"] == "database")
    faiss_entries = [item for item in files if item["artifact_type"] == "faiss_indexes"]
    chroma_entries = [item for item in files if item["artifact_type"] == "chroma_db"]
    pdf_entries = [
        item
        for item in files
        if item["artifact_type"] == "media" and item["path"].lower().endswith(".pdf")
    ]
    included_trees = list(RUNTIME_TREES)
    if include_static:
        included_trees.extend(OPTIONAL_CUSTODY_TREES)
    return {
        "manifest_version": MANIFEST_VERSION,
        "read_only": True,
        "release_id": generation_id,
        "dataset_id": dataset_id,
        "schema": {
            "inventory_schema": "legacy-volume-port/v2",
            "django_app": "core",
            "pdf_model": "core.PDFFile",
        },
        "source": {
            "kind": "legacy-data-root",
            "root_contract": "read-only-volume",
            "label": source_label,
            "snapshot_evidence": source_evidence or {},
        },
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "database": {**sqlite_metadata(snapshot_path), "entry": database_entry},
        "pdf_storage": {
            "root": "media",
            "files": pdf_entries,
            "file_count": len(pdf_entries),
        },
        "faiss": {
            "root": "faiss_indexes",
            "files": faiss_entries,
            "count": len(faiss_entries),
        },
        "chroma": {
            "root": "chroma_db",
            "files": chroma_entries,
            "count": len(chroma_entries),
        },
        "embedding_index": {
            "model": "",
            "metadata_files": [],
            "database_rows_with_embeddings": None,
        },
        "runtime_trees": included_trees,
        "included_backups": include_backups,
        "files": files,
        "counts": {
            "files": len(files),
            "pdfs": len(pdf_entries),
            "faiss": len(faiss_entries),
            "chroma": len(chroma_entries),
            "pdf_cache": sum(
                item["artifact_type"] == "pdf_cache" for item in files
            ),
            "staticfiles": sum(
                item["artifact_type"] == "staticfiles" for item in files
            ),
            "backups": sum(item["artifact_type"] == "backup" for item in files),
        },
    }


def validate_manifest(
    manifest: Any,
    dataset_id: str,
    generation_id: str,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise MigrationError("Manifest is not a JSON object")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise MigrationError("Manifest version is unsupported")
    if manifest.get("dataset_id") != dataset_id:
        raise MigrationError("Manifest dataset identity does not match")
    if manifest.get("release_id") != generation_id:
        raise MigrationError("Manifest generation identity does not match")
    if not isinstance(manifest.get("schema"), dict):
        raise MigrationError("Manifest schema evidence is missing")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise MigrationError("Manifest contains no artifact files")

    paths: set[str] = set()
    objects: dict[str, tuple[str, int]] = {}
    database_entry: dict[str, Any] | None = None
    for entry in files:
        if not isinstance(entry, dict):
            raise MigrationError("Manifest file entry is not an object")
        path = _validate_manifest_path(entry.get("path"))
        digest = entry.get("sha256")
        size = entry.get("bytes")
        key = entry.get("object_key")
        artifact_type = entry.get("artifact_type")
        if path in paths:
            raise MigrationError(f"Manifest path is duplicated: {path}")
        paths.add(path)
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise MigrationError(f"Manifest digest is invalid: {path}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise MigrationError(f"Manifest size is invalid: {path}")
        if not isinstance(key, str) or not key.startswith(f"datasets/{dataset_id}/"):
            raise MigrationError(f"Manifest object key escapes dataset: {path}")
        signature = (digest, size)
        if key in objects and objects[key] != signature:
            raise MigrationError(f"Manifest object key has conflicting content: {key}")
        objects[key] = signature
        if artifact_type == "database":
            if database_entry is not None:
                raise MigrationError("Manifest contains more than one database")
            if path != "db.sqlite3" or key != object_key(
                dataset_id, generation_id, path, digest
            ):
                raise MigrationError("Manifest database binding is invalid")
            database_entry = entry
        elif key != object_key(dataset_id, generation_id, path, digest):
            raise MigrationError(f"Manifest object binding is invalid: {path}")

    if database_entry is None:
        raise MigrationError("Manifest database entry is missing")
    database = manifest.get("database")
    if not isinstance(database, dict) or database.get("entry") != database_entry:
        raise MigrationError("Manifest database evidence does not match file entry")
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or counts.get("files") != len(files):
        raise MigrationError("Manifest file count does not match entries")
    expected_counts = {
        "pdfs": sum(
            entry.get("artifact_type") == "media"
            and entry["path"].lower().endswith(".pdf")
            for entry in files
        ),
        "faiss": sum(
            entry.get("artifact_type") == "faiss_indexes" for entry in files
        ),
        "chroma": sum(
            entry.get("artifact_type") == "chroma_db" for entry in files
        ),
        "pdf_cache": sum(
            entry.get("artifact_type") == "pdf_cache" for entry in files
        ),
        "staticfiles": sum(
            entry.get("artifact_type") == "staticfiles" for entry in files
        ),
        "backups": sum(
            entry.get("artifact_type") == "backup" for entry in files
        ),
    }
    for field, expected in expected_counts.items():
        if counts.get(field) != expected:
            raise MigrationError(
                f"Manifest {field} count does not match artifact entries"
            )
    pdf_storage = manifest.get("pdf_storage")
    faiss = manifest.get("faiss")
    chroma = manifest.get("chroma")
    if (
        not isinstance(pdf_storage, dict)
        or pdf_storage.get("file_count") != expected_counts["pdfs"]
        or pdf_storage.get("files")
        != [
            entry
            for entry in files
            if entry.get("artifact_type") == "media"
            and entry["path"].lower().endswith(".pdf")
        ]
    ):
        raise MigrationError("Manifest PDF count does not match entries")
    if (
        not isinstance(faiss, dict)
        or faiss.get("count") != expected_counts["faiss"]
        or faiss.get("files")
        != [
            entry
            for entry in files
            if entry.get("artifact_type") == "faiss_indexes"
        ]
    ):
        raise MigrationError("Manifest FAISS count does not match entries")
    if (
        not isinstance(chroma, dict)
        or chroma.get("count") != expected_counts["chroma"]
        or chroma.get("files")
        != [
            entry
            for entry in files
            if entry.get("artifact_type") == "chroma_db"
        ]
    ):
        raise MigrationError("Manifest Chroma count does not match entries")
    return manifest


def validate_registration(
    registration: Any,
    dataset_id: str,
    production_source_id: str,
) -> dict[str, Any]:
    if not isinstance(registration, dict):
        raise MigrationError("Destination registration is not a JSON object")
    expected = {
        "dataset_id": dataset_id,
        "registration_version": REGISTRATION_VERSION,
        "app_identifier": APP_IDENTIFIER,
        "production_source_id": production_source_id,
    }
    for field, value in expected.items():
        if registration.get(field) != value:
            raise MigrationError(f"Destination registration {field} mismatch")
    schema_range = registration.get("manifest_schema_range")
    if not isinstance(schema_range, dict):
        raise MigrationError("Destination registration schema range is missing")
    minimum = schema_range.get("min")
    maximum = schema_range.get("max")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum > MANIFEST_VERSION
        or maximum < MANIFEST_VERSION
    ):
        raise MigrationError("Destination registration rejects manifest version")
    return registration


def validate_pointer(
    pointer: Any,
    dataset_id: str,
    production_source_id: str,
    *,
    allow_legacy_schema: bool = False,
) -> dict[str, Any]:
    if not isinstance(pointer, dict):
        raise MigrationError("Authoritative pointer is not a JSON object")
    schema_version = pointer.get("schema_version")
    if schema_version != 1 and not (allow_legacy_schema and schema_version is None):
        raise MigrationError("Authoritative pointer schema is unsupported")
    pointer_dataset = pointer.get("dataset_id")
    allowed_datasets = (
        (dataset_id,) if not allow_legacy_schema else (None, dataset_id)
    )
    if pointer_dataset not in allowed_datasets:
        raise MigrationError("Authoritative pointer dataset identity does not match")
    if pointer.get("production_source_id") != production_source_id:
        raise MigrationError("Authoritative pointer source identity does not match")
    generation_id = pointer.get("generation_id")
    validate_id(generation_id, "pointer generation id")
    expected_manifest_key = manifest_key(dataset_id, generation_id)
    if pointer.get("manifest_object_key") != expected_manifest_key:
        raise MigrationError("Authoritative pointer manifest key is invalid")
    digest = pointer.get("manifest_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise MigrationError("Authoritative pointer manifest digest is invalid")
    epoch = pointer.get("writer_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise MigrationError("Authoritative pointer writer epoch is invalid")
    return pointer


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise MigrationError("boto3 is required in the agent execution environment") from exc
    endpoint = os.environ.get("ARTIFACT_VAULT_ENDPOINT", "").strip()
    region = os.environ.get("ARTIFACT_VAULT_REGION", "us-east-1").strip()
    access_key = os.environ.get("ARTIFACT_VAULT_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("ARTIFACT_VAULT_SECRET_KEY", "").strip()
    if not endpoint or not access_key or not secret_key:
        raise MigrationError(
            "ARTIFACT_VAULT_ENDPOINT, ARTIFACT_VAULT_ACCESS_KEY and "
            "ARTIFACT_VAULT_SECRET_KEY must be provided through secure agent environment"
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def read_object(client: Any, bucket: str, key: str) -> tuple[bytes | None, str | None]:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
            return None, None
        raise MigrationError(f"Unable to read destination object: {key}") from exc
    body = response["Body"].read(MAX_CONTROL_OBJECT_BYTES + 1)
    if len(body) > MAX_CONTROL_OBJECT_BYTES:
        raise MigrationError(f"Destination object exceeds safety limit: {key}")
    return body, response.get("ETag")


def read_json_object(
    client: Any, bucket: str, key: str
) -> tuple[dict[str, Any] | None, str | None]:
    body, etag = read_object(client, bucket, key)
    if body is None:
        return None, None
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MigrationError(f"Destination control object is malformed: {key}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"Destination control object is not a JSON object: {key}")
    return value, etag


def verify_remote_object(
    client: Any,
    bucket: str,
    key: str,
    digest: str,
    size: int,
) -> None:
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise MigrationError(f"Remote object verification failed: {key}") from exc
    metadata_digest = (head.get("Metadata") or {}).get("sha256", "")
    if metadata_digest != digest or int(head.get("ContentLength", -1)) != size:
        raise MigrationError(f"Remote object digest or size mismatch: {key}")


def probe_conditional_writes(
    client: Any,
    bucket: str,
    dataset_id: str,
) -> None:
    """Prove create/replace preconditions before relying on them for safety."""
    key = (
        f"datasets/{dataset_id}/control/capability-probes/"
        f"legacy-migration-{secrets.token_hex(8)}"
    )
    first = b"conditional-create-v1"
    second = b"conditional-replace-v2"
    try:
        created = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=first,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
        )
        created_etag = created.get("ETag", "")
        if not created_etag:
            raise MigrationError("Conditional-write probe did not return an ETag")
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=b"unsafe-overwrite",
                ContentType="application/octet-stream",
                IfNoneMatch="*",
            )
        except Exception:
            pass
        else:
            raise MigrationError("Object store ignored conditional create")
        body, etag = read_object(client, bucket, key)
        if body != first or etag != created_etag:
            raise MigrationError("Conditional-create probe changed unexpectedly")
        replaced = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=second,
            ContentType="application/octet-stream",
            IfMatch=created_etag,
        )
        replaced_etag = replaced.get("ETag", "")
        if not replaced_etag or replaced_etag == created_etag:
            raise MigrationError("Conditional replace did not advance the ETag")
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=b"unsafe-stale-replace",
                ContentType="application/octet-stream",
                IfMatch=created_etag,
            )
        except Exception:
            pass
        else:
            raise MigrationError("Object store ignored stale conditional replace")
        body, etag = read_object(client, bucket, key)
        if body != second or etag != replaced_etag:
            raise MigrationError("Conditional-replace probe changed unexpectedly")
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError("Object store conditional-write probe failed") from exc
    finally:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass


def put_immutable_bytes(
    client: Any,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
) -> str:
    digest = hashlib.sha256(data).hexdigest()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest, "immutable": "true"},
            IfNoneMatch="*",
        )
        status = "uploaded"
    except Exception as exc:
        try:
            verify_remote_object(client, bucket, key, digest, len(data))
        except MigrationError as verify_exc:
            raise MigrationError(f"Immutable object conflict: {key}") from exc
        status = "already-present"
    verify_remote_object(client, bucket, key, digest, len(data))
    return status


def put_immutable_file(
    client: Any,
    bucket: str,
    key: str,
    path: Path,
    digest: str,
    size: int,
) -> str:
    try:
        with path.open("rb") as stream:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=stream,
                ContentLength=size,
                ContentType="application/octet-stream",
                Metadata={"sha256": digest, "immutable": "true"},
                IfNoneMatch="*",
            )
        status = "uploaded"
    except Exception as exc:
        try:
            verify_remote_object(client, bucket, key, digest, size)
        except MigrationError:
            raise MigrationError(f"Immutable object conflict: {key}") from exc
        status = "already-present"
    verify_remote_object(client, bucket, key, digest, size)
    return status


def registration_payload(
    dataset_id: str,
    production_source_id: str,
    source_label: str,
) -> bytes:
    return canonical_json(
        {
            "dataset_id": dataset_id,
            "registration_version": REGISTRATION_VERSION,
            "manifest_schema_range": {
                "min": MANIFEST_VERSION,
                "max": MANIFEST_VERSION,
            },
            "app_identifier": APP_IDENTIFIER,
            "production_source_id": production_source_id,
            "initial_instance_id": "legacy-migration-tool",
            "org_name": "Registrar Co-operative Societies, Maharashtra",
            "migration_source": source_label,
            "registration_nonce": secrets.token_hex(16),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def ensure_registration(
    client: Any,
    bucket: str,
    dataset_id: str,
    production_source_id: str,
    source_label: str,
    *,
    allow_create: bool,
) -> dict[str, Any]:
    key = registration_key(dataset_id)
    existing, _etag = read_json_object(client, bucket, key)
    if existing is not None:
        return validate_registration(existing, dataset_id, production_source_id)
    if not allow_create:
        raise MigrationError(
            "Destination dataset is unregistered; pass --register-dataset "
            "only after reviewing the empty destination"
        )
    payload = registration_payload(dataset_id, production_source_id, source_label)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            Metadata={"sha256": hashlib.sha256(payload).hexdigest(), "immutable": "true"},
            IfNoneMatch="*",
        )
    except Exception as exc:
        existing, _etag = read_json_object(client, bucket, key)
        if existing is None:
            raise MigrationError("Destination registration creation failed") from exc
        return validate_registration(existing, dataset_id, production_source_id)
    created, _etag = read_json_object(client, bucket, key)
    return validate_registration(created, dataset_id, production_source_id)


def verify_remote_generation(
    client: Any,
    bucket: str,
    dataset_id: str,
    generation_id: str,
) -> tuple[dict[str, Any], str]:
    key = manifest_key(dataset_id, generation_id)
    data, _etag = read_object(client, bucket, key)
    if data is None:
        raise MigrationError(f"Generation manifest is missing: {generation_id}")
    digest = hashlib.sha256(data).hexdigest()
    try:
        manifest = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MigrationError("Generation manifest is malformed") from exc
    validate_manifest(manifest, dataset_id, generation_id)
    verify_remote_object(client, bucket, key, digest, len(data))
    for entry in manifest["files"]:
        verify_remote_object(
            client,
            bucket,
            entry["object_key"],
            entry["sha256"],
            entry["bytes"],
        )
    return manifest, digest


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def acquire_writer(
    client: Any,
    bucket: str,
    dataset_id: str,
    production_source_id: str,
    *,
    ttl_seconds: int,
    minimum_epoch: int = 1,
) -> dict[str, Any]:
    key = writer_key(dataset_id)
    now = time.time()
    existing, etag = read_json_object(client, bucket, key)
    if existing is not None:
        if existing.get("dataset_id") != dataset_id:
            raise MigrationError("Existing writer dataset identity is invalid")
        if existing.get("production_source_id") != production_source_id:
            raise MigrationError("Existing writer source identity is invalid")
        expiry = existing.get("expires_at")
        if not isinstance(expiry, (int, float)):
            raise MigrationError("Existing writer expiry is invalid")
        if expiry > now:
            raise MigrationError("Destination writer lease is currently held")
        epoch = existing.get("writer_epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise MigrationError("Existing writer epoch is invalid")
        next_epoch = max(epoch + 1, minimum_epoch)
    else:
        next_epoch = max(1, minimum_epoch)

    token = secrets.token_hex(32)
    record = {
        "schema_version": WRITER_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "production_source_id": production_source_id,
        "instance_id": "legacy-migration-tool",
        "deployment_id": "operator-side",
        "replica_id": "",
        "owner_token_hash": _token_hash(token),
        "writer_epoch": next_epoch,
        "acquired_at": now,
        "heartbeat_at": now,
        "expires_at": now + ttl_seconds,
        "previous_epoch": existing.get("writer_epoch", 0) if existing else 0,
        "app_release": "legacy-volume-port",
        "image_digest": "",
        "force_takeover": False,
        "takeover_reason": "",
    }
    payload = canonical_json(record)
    params = {
        "Bucket": bucket,
        "Key": key,
        "Body": payload,
        "ContentType": "application/json",
        "Metadata": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "control": "writer",
        },
    }
    if etag:
        params["IfMatch"] = etag
    else:
        params["IfNoneMatch"] = "*"
    try:
        response = client.put_object(**params)
    except Exception as exc:
        raise MigrationError("Global writer acquisition lost a compare-and-swap race") from exc
    record["_etag"] = response.get("ETag", "")
    record["_token"] = token
    validate_writer(client, bucket, dataset_id, production_source_id, record)
    return record


def validate_writer(
    client: Any,
    bucket: str,
    dataset_id: str,
    production_source_id: str,
    writer: dict[str, Any],
) -> dict[str, Any]:
    current, etag = read_json_object(client, bucket, writer_key(dataset_id))
    if current is None:
        raise MigrationError("Global writer record disappeared")
    if current.get("dataset_id") != dataset_id:
        raise MigrationError("Global writer dataset identity mismatch")
    if current.get("production_source_id") != production_source_id:
        raise MigrationError("Global writer source identity mismatch")
    if current.get("instance_id") != writer.get("instance_id"):
        raise MigrationError("Global writer instance identity mismatch")
    if current.get("writer_epoch") != writer.get("writer_epoch"):
        raise MigrationError("Global writer fencing epoch mismatch")
    if current.get("owner_token_hash") != _token_hash(writer.get("_token", "")):
        raise MigrationError("Global writer ownership token mismatch")
    if current.get("expires_at", 0) <= time.time():
        raise MigrationError("Global writer lease expired")
    current["_etag"] = etag or ""
    return current


def release_writer(
    client: Any,
    bucket: str,
    dataset_id: str,
    production_source_id: str,
    writer: dict[str, Any],
) -> bool:
    try:
        current = validate_writer(
            client, bucket, dataset_id, production_source_id, writer
        )
        etag = current.pop("_etag")
        now = time.time()
        current["heartbeat_at"] = now
        current["expires_at"] = now
        current["released_at"] = now
        payload = canonical_json(current)
        client.put_object(
            Bucket=bucket,
            Key=writer_key(dataset_id),
            Body=payload,
            ContentType="application/json",
            Metadata={
                "sha256": hashlib.sha256(payload).hexdigest(),
                "control": "writer",
            },
            IfMatch=etag,
        )
        return True
    except Exception:
        # Never delete blindly: a failed conditional expiry leaves only this
        # holder's bounded TTL and cannot remove a successor's authority.
        return False


def verify_pointer_chain(
    client: Any,
    bucket: str,
    dataset_id: str,
    production_source_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    pointer, etag = read_json_object(client, bucket, pointer_key(dataset_id))
    if pointer is None:
        return None, None
    validate_pointer(
        pointer,
        dataset_id,
        production_source_id,
        allow_legacy_schema=True,
    )
    data, _manifest_etag = read_object(
        client, bucket, pointer["manifest_object_key"]
    )
    if data is None:
        raise MigrationError("Authoritative pointer references a missing manifest")
    if hashlib.sha256(data).hexdigest() != pointer["manifest_sha256"]:
        raise MigrationError("Authoritative pointer manifest digest does not match")
    try:
        pointed_manifest = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MigrationError("Authoritative pointer manifest is malformed") from exc
    validate_manifest(pointed_manifest, dataset_id, pointer["generation_id"])
    return pointer, etag


def write_pointer(
    client: Any,
    bucket: str,
    dataset_id: str,
    generation_id: str,
    manifest_digest: str,
    production_source_id: str,
    writer: dict[str, Any],
) -> dict[str, Any]:
    current_writer = validate_writer(
        client, bucket, dataset_id, production_source_id, writer
    )
    previous, etag = verify_pointer_chain(
        client, bucket, dataset_id, production_source_id
    )
    previous_epoch = previous.get("writer_epoch", 0) if previous else 0
    writer_epoch = current_writer["writer_epoch"]
    if writer_epoch < previous_epoch:
        raise MigrationError("Writer fencing epoch is incompatible with current pointer")
    pointer = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "generation_id": generation_id,
        "manifest_object_key": manifest_key(dataset_id, generation_id),
        "manifest_sha256": manifest_digest,
        "writer_epoch": writer_epoch,
        "writer_token_hash": current_writer["owner_token_hash"],
        "production_source_id": production_source_id,
        "instance_id": current_writer["instance_id"],
        "app_release": "legacy-volume-port",
        "image_digest": "",
        "database_schema": "legacy-snapshot",
        "previous_generation_id": previous.get("generation_id", "") if previous else "",
        "previous_pointer_hash": previous.get("manifest_sha256", "") if previous else "",
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = canonical_json(pointer)
    params = {
        "Bucket": bucket,
        "Key": pointer_key(dataset_id),
        "Body": payload,
        "ContentType": "application/json",
        "Metadata": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "control": "authoritative-pointer",
        },
    }
    if etag:
        params["IfMatch"] = etag
    else:
        params["IfNoneMatch"] = "*"
    try:
        client.put_object(**params)
    except Exception as exc:
        raise MigrationError(
            "Destination authoritative pointer changed during promotion"
        ) from exc
    stored, _new_etag = read_json_object(client, bucket, pointer_key(dataset_id))
    validate_pointer(stored, dataset_id, production_source_id)
    if stored.get("generation_id") != generation_id:
        raise MigrationError("Authoritative pointer verification selected another generation")
    return stored


def _write_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _checkpoint_created_at(generation_id: str) -> str:
    match = TIMESTAMP_RE.search(generation_id)
    if match:
        parsed = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ")
        return parsed.replace(tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def load_or_create_checkpoint(
    path: Path,
    *,
    dataset_id: str,
    generation_id: str,
    source_root: Path,
) -> dict[str, Any]:
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MigrationError("Checkpoint is malformed") from exc
        expected = {
            "checkpoint_version": 1,
            "dataset_id": dataset_id,
            "generation_id": generation_id,
            "source_root": str(source_root),
        }
        if not isinstance(state, dict) or any(
            state.get(field) != value for field, value in expected.items()
        ):
            raise MigrationError("Checkpoint identity does not match this migration")
        return state
    state = {
        "checkpoint_version": 1,
        "dataset_id": dataset_id,
        "generation_id": generation_id,
        "source_root": str(source_root),
        "created_at": _checkpoint_created_at(generation_id),
        "objects": {},
        "manifest_published": False,
        "registration_verified": False,
    }
    _write_checkpoint(path, state)
    return state


def publish_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if args.source_root is None:
        raise MigrationError("--source-root is required for inventory and publication")
    validate_id(args.dataset_id, "dataset id")
    validate_id(args.bucket, "bucket")
    validate_id(args.production_source_id, "production source id")
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise MigrationError(f"Source root not found: {source_root}")
    database = (args.database or source_root / "db.sqlite3").resolve()
    configured_database = args.database or source_root / "db.sqlite3"
    if configured_database.is_symlink():
        raise MigrationError("SQLite source must not be a symlink")
    if args.publish and args.checkpoint is None:
        raise MigrationError("--checkpoint is required for resumable publication")

    generation_id = args.generation_id
    if generation_id is None and args.checkpoint is not None and args.checkpoint.exists():
        try:
            checkpoint_identity = json.loads(args.checkpoint.read_text())
            generation_id = checkpoint_identity.get("generation_id")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # The normal checkpoint loader below emits the stable safe error.
            generation_id = None
    generation_id = generation_id or (
        "legacy-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(4)
    )
    validate_id(generation_id, "generation id")
    checkpoint: dict[str, Any] | None = None
    if args.checkpoint is not None:
        checkpoint = load_or_create_checkpoint(
            args.checkpoint.resolve(),
            dataset_id=args.dataset_id,
            generation_id=generation_id,
            source_root=source_root,
        )
    created_at = (
        checkpoint["created_at"]
        if checkpoint is not None
        else _checkpoint_created_at(generation_id)
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="pdfsearch-legacy-port-"))
    snapshot_root = temp_dir / "snapshot"
    snapshot_root.mkdir()
    try:
        evidence = create_stable_snapshot(
            source_root,
            database,
            snapshot_root,
            include_backups=args.include_backups,
            include_static=args.include_static,
        )
        manifest = inventory_source(
            snapshot_root,
            snapshot_root / "db.sqlite3",
            generation_id,
            args.dataset_id,
            args.include_backups,
            include_static=args.include_static,
            created_at=created_at,
            source_label=args.source_label,
            source_evidence=evidence,
        )
        validate_manifest(manifest, args.dataset_id, generation_id)
        manifest_data = canonical_json(manifest)
        manifest_digest = hashlib.sha256(manifest_data).hexdigest()
        if checkpoint is not None:
            checkpoint_digest = checkpoint.get("manifest_sha256")
            if checkpoint_digest and checkpoint_digest != manifest_digest:
                raise MigrationError(
                    "checkpoint_manifest_mismatch: retry source does not match "
                    "the original immutable generation"
                )
            checkpoint["manifest_sha256"] = manifest_digest
            _write_checkpoint(args.checkpoint.resolve(), checkpoint)
        if args.output:
            args.output.write_bytes(manifest_data)
        output = {
            "bucket": args.bucket,
            "dataset_id": args.dataset_id,
            "generation_id": generation_id,
            "manifest_sha256": manifest_digest,
            "counts": manifest["counts"],
            "dry_run": not args.publish,
            "candidate_published": False,
            "pointer_updated": False,
        }
        if not args.publish:
            return output

        _make_read_only(snapshot_root)
        client = s3_client()
        probe_conditional_writes(client, args.bucket, args.dataset_id)
        stats = {"uploaded": 0, "already_present": 0}
        for entry in manifest["files"]:
            path = snapshot_root / entry["path"]
            status = put_immutable_file(
                client,
                args.bucket,
                entry["object_key"],
                path,
                entry["sha256"],
                entry["bytes"],
            )
            stats[status.replace("-", "_")] += 1
            checkpoint["objects"][entry["object_key"]] = {
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "verified": True,
            }
            _write_checkpoint(args.checkpoint.resolve(), checkpoint)

        key = manifest_key(args.dataset_id, generation_id)
        status = put_immutable_bytes(
            client, args.bucket, key, manifest_data, "application/json"
        )
        stats[status.replace("-", "_")] += 1
        remote_manifest, remote_digest = verify_remote_generation(
            client, args.bucket, args.dataset_id, generation_id
        )
        if remote_digest != manifest_digest or canonical_json(remote_manifest) != manifest_data:
            raise MigrationError("Published manifest re-read does not match local manifest")
        checkpoint["manifest_published"] = True
        _write_checkpoint(args.checkpoint.resolve(), checkpoint)

        ensure_registration(
            client,
            args.bucket,
            args.dataset_id,
            args.production_source_id,
            args.source_label,
            allow_create=args.register_dataset,
        )
        checkpoint["registration_verified"] = True
        _write_checkpoint(args.checkpoint.resolve(), checkpoint)
        output.update(stats, candidate_published=True)
        return output
    finally:
        _make_writable(snapshot_root)
        shutil.rmtree(temp_dir, ignore_errors=True)


def promote_candidate(args: argparse.Namespace) -> dict[str, Any]:
    generation_id = args.promote_generation
    validate_id(args.dataset_id, "dataset id")
    validate_id(args.bucket, "bucket")
    validate_id(args.production_source_id, "production source id")
    validate_id(generation_id, "generation id")
    expected_confirmation = f"{args.dataset_id}:{generation_id}"
    if args.confirm_promotion != expected_confirmation:
        raise MigrationError(
            "Promotion confirmation mismatch; pass "
            f"--confirm-promotion {expected_confirmation}"
        )
    client = s3_client()
    probe_conditional_writes(client, args.bucket, args.dataset_id)
    ensure_registration(
        client,
        args.bucket,
        args.dataset_id,
        args.production_source_id,
        args.source_label,
        allow_create=False,
    )
    _manifest, manifest_digest = verify_remote_generation(
        client, args.bucket, args.dataset_id, generation_id
    )
    previous_pointer, _previous_etag = verify_pointer_chain(
        client, args.bucket, args.dataset_id, args.production_source_id
    )
    writer = acquire_writer(
        client,
        args.bucket,
        args.dataset_id,
        args.production_source_id,
        ttl_seconds=args.writer_ttl_seconds,
        minimum_epoch=(
            previous_pointer.get("writer_epoch", 0) + 1
            if previous_pointer
            else 1
        ),
    )
    pointer_written = False
    released = False
    try:
        # Re-read every authority input after fencing and immediately before CAS.
        ensure_registration(
            client,
            args.bucket,
            args.dataset_id,
            args.production_source_id,
            args.source_label,
            allow_create=False,
        )
        _manifest, fresh_digest = verify_remote_generation(
            client, args.bucket, args.dataset_id, generation_id
        )
        if fresh_digest != manifest_digest:
            raise MigrationError("Candidate manifest changed before promotion")
        write_pointer(
            client,
            args.bucket,
            args.dataset_id,
            generation_id,
            fresh_digest,
            args.production_source_id,
            writer,
        )
        pointer_written = True
    finally:
        released = release_writer(
            client,
            args.bucket,
            args.dataset_id,
            args.production_source_id,
            writer,
        )
    return {
        "bucket": args.bucket,
        "dataset_id": args.dataset_id,
        "generation_id": generation_id,
        "manifest_sha256": manifest_digest,
        "candidate_published": True,
        "pointer_updated": pointer_written,
        "writer_released": released,
    }


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "promote_generation", None):
        return promote_candidate(args)
    return publish_candidate(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="Read-only mounted legacy data root")
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to SOURCE_ROOT/db.sqlite3")
    parser.add_argument("--dataset-id", default=os.environ.get("VAULT_DATASET_ID", DEFAULT_DATASET_ID))
    parser.add_argument("--bucket", default=os.environ.get("ARTIFACT_VAULT_BUCKET", DEFAULT_BUCKET))
    parser.add_argument(
        "--production-source-id",
        default=os.environ.get("PRODUCTION_SOURCE_ID", DEFAULT_PRODUCTION_SOURCE_ID),
    )
    parser.add_argument("--generation-id", help="Immutable candidate generation ID")
    parser.add_argument("--source-label", default="sahakar-dev-frontend-dockerfile-1cubi5")
    parser.add_argument("--output", type=Path, help="Write the manifest inventory locally")
    parser.add_argument("--checkpoint", type=Path, help="Durable, non-secret resume checkpoint")
    parser.add_argument("--include-backups", action="store_true")
    parser.add_argument("--include-static", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--publish",
        action="store_true",
        help="Upload an immutable candidate; never moves the authoritative pointer",
    )
    actions.add_argument(
        "--promote-generation",
        metavar="GENERATION_ID",
        help="CAS-promote an already published and verified candidate",
    )
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--register-dataset",
        action="store_true",
        help="Conditionally create registration after empty-destination review",
    )
    parser.add_argument(
        "--confirm-promotion",
        default="",
        help="Exact DATASET_ID:GENERATION_ID confirmation for promotion",
    )
    parser.add_argument("--writer-ttl-seconds", type=int, default=120)
    args = parser.parse_args()
    if args.candidate_only and not args.publish:
        parser.error("--candidate-only is obsolete; --publish is always candidate-only")
    if args.writer_ttl_seconds < 30:
        parser.error("--writer-ttl-seconds must be at least 30")
    return args


if __name__ == "__main__":
    try:
        print(json.dumps(migrate(parse_args()), sort_keys=True))
    except Exception as exc:
        raise SystemExit(f"migration failed: {exc}") from exc
