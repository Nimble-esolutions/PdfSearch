#!/usr/bin/env python3
"""Agent-run legacy-volume migration into the latest artifact-vault contract.

This script is intentionally outside Django and is not an application runtime
dependency. The agent runs it from a repository checkout against a read-only
source mount and an explicitly configured S3-compatible endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BUCKET = "ai-sahakar-prod-flowdocs-artifact-vault"
DEFAULT_DATASET_ID = "ai-sahakar-prod"
DEFAULT_PRODUCTION_SOURCE_ID = "ai-sahakar-prod"
RUNTIME_TREES = ("media", "faiss_indexes", "chroma_db", "pdf_cache", "staticfiles")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"SQLite database not found: {source}")
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
        integrity = target_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    finally:
        target_conn.close()
        source_conn.close()


def safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"Path escapes source root: {path}") from exc


def object_key(dataset_id: str, generation_id: str, relative_path: str, digest: str) -> str:
    if relative_path.lower().endswith(".pdf") and relative_path.startswith("media/"):
        return f"datasets/{dataset_id}/blobs/pdfs/sha256/{digest}.pdf"
    if relative_path == "db.sqlite3":
        return f"datasets/{dataset_id}/generations/{generation_id}/database.sqlite3"
    # Keep the original path in the manifest for restore, but use a digest key
    # for arbitrary legacy filenames (including Devanagari and spaces).
    return f"datasets/{dataset_id}/blobs/files/{digest}"


def validate_id(value: str, label: str) -> str:
    if not value or "/" in value or "\\" in value or ".." in value or any(ord(char) < 32 for char in value):
        raise RuntimeError(f"Unsafe {label}")
    if len(value) > 120:
        raise RuntimeError(f"{label} is too long")
    return value


def inventory_source(
    source_root: Path,
    snapshot_path: Path,
    generation_id: str,
    dataset_id: str,
    include_backups: bool,
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
                "object_key": object_key(dataset_id, generation_id, relative_path, digest),
                "artifact_type": artifact_type,
            }
        )

    add(snapshot_path, "db.sqlite3", "database")
    for tree in RUNTIME_TREES:
        root = source_root / tree
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = safe_relative(source_root, path)
            add(path, relative, tree)

    if include_backups:
        root = source_root / "backups"
        if root.is_dir():
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = safe_relative(source_root, path)
                add(path, relative, "backup")

    database_entry = next(item for item in files if item["artifact_type"] == "database")
    faiss_entries = [item for item in files if item["artifact_type"] == "faiss_indexes"]
    chroma_entries = [item for item in files if item["artifact_type"] == "chroma_db"]
    pdf_entries = [
        item for item in files
        if item["artifact_type"] == "media" and item["path"].lower().endswith(".pdf")
    ]
    return {
        "manifest_version": 1,
        "read_only": True,
        "release_id": generation_id,
        "dataset_id": dataset_id,
        "schema": {
            "inventory_schema": "legacy-volume-port/v1",
            "django_app": "core",
            "pdf_model": "core.PDFFile",
        },
        "source": {"kind": "legacy-data-root", "root_contract": "read-only-volume"},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {**sqlite_metadata(snapshot_path), "entry": database_entry},
        "pdf_storage": {"root": "media", "files": pdf_entries, "file_count": len(pdf_entries)},
        "faiss": {"root": "faiss_indexes", "files": faiss_entries, "count": len(faiss_entries)},
        "chroma": {"root": "chroma_db", "files": chroma_entries, "count": len(chroma_entries)},
        "embedding_index": {"model": "", "metadata_files": [], "database_rows_with_embeddings": None},
        "runtime_trees": list(RUNTIME_TREES),
        "included_backups": include_backups,
        "files": files,
        "counts": {
            "files": len(files),
            "pdfs": sum(item["artifact_type"] == "media" and item["path"].lower().endswith(".pdf") for item in files),
            "faiss": sum(item["artifact_type"] == "faiss_indexes" for item in files),
            "chroma": sum(item["artifact_type"] == "chroma_db" for item in files),
            "pdf_cache": sum(item["artifact_type"] == "pdf_cache" for item in files),
            "staticfiles": sum(item["artifact_type"] == "staticfiles" for item in files),
            "backups": sum(item["artifact_type"] == "backup" for item in files),
        },
    }


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sqlite_metadata(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        migrations: list[dict[str, str]] = []
        if "django_migrations" in tables:
            migrations = [
                {"app": row[0], "name": row[1]}
                for row in connection.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
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


def s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required in the agent execution environment") from exc
    endpoint = os.environ.get("ARTIFACT_VAULT_ENDPOINT", "").strip()
    region = os.environ.get("ARTIFACT_VAULT_REGION", "us-east-1").strip()
    access_key = os.environ.get("ARTIFACT_VAULT_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("ARTIFACT_VAULT_SECRET_KEY", "").strip()
    if not endpoint or not access_key or not secret_key:
        raise RuntimeError(
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


def put_immutable(client: Any, bucket: str, key: str, data: bytes, content_type: str) -> str:
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
        return "uploaded"
    except Exception as exc:
        try:
            head = client.head_object(Bucket=bucket, Key=key)
        except Exception as head_exc:
            raise RuntimeError("Immutable upload failed and existing-object verification was unavailable") from head_exc
        metadata_digest = (head.get("Metadata") or {}).get("sha256", "")
        if metadata_digest == digest and int(head.get("ContentLength", -1)) == len(data):
            return "already-present"
        raise RuntimeError(f"Immutable object conflict: {key}") from exc


def registration_payload(dataset_id: str, production_source_id: str, source_label: str) -> bytes:
    return canonical_json(
        {
            "dataset_id": dataset_id,
            "registration_version": 1,
            "manifest_schema_range": {"min": 1, "max": 1},
            "app_identifier": "pdfsearch",
            "production_source_id": production_source_id,
            "initial_instance_id": "legacy-migration-tool",
            "org_name": "Registrar Co-operative Societies, Maharashtra",
            "migration_source": source_label,
            "registration_nonce": secrets.token_hex(16),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def read_json_object(client: Any, bucket: str, key: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None, None
        raise RuntimeError(f"Unable to read destination control object: {key}") from exc
    body = response["Body"].read()
    return json.loads(body), response.get("ETag")


def write_registration(client: Any, bucket: str, dataset_id: str, payload: bytes) -> None:
    key = f"datasets/{dataset_id}/control/registration.json"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json", IfNoneMatch="*")
    except Exception:
        existing, _ = read_json_object(client, bucket, key)
        if not existing or existing.get("dataset_id") != dataset_id:
            raise RuntimeError(f"Destination registration conflict: {key}")


def write_pointer(
    client: Any,
    bucket: str,
    dataset_id: str,
    generation_id: str,
    manifest_key: str,
    manifest_digest: str,
    previous_generation_id: str,
    production_source_id: str,
) -> None:
    key = f"datasets/{dataset_id}/control/authoritative.json"
    previous, etag = read_json_object(client, bucket, key)
    pointer = {
        "dataset_id": dataset_id,
        "generation_id": generation_id,
        "manifest_object_key": manifest_key,
        "manifest_sha256": manifest_digest,
        "writer_epoch": 0,
        "production_source_id": production_source_id,
        "instance_id": "legacy-migration-tool",
        "app_release": "legacy-volume-port",
        "image_digest": "",
        "database_schema": "legacy-snapshot",
        "previous_generation_id": previous_generation_id or (previous or {}).get("generation_id", ""),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    data = canonical_json(pointer)
    params = {"Bucket": bucket, "Key": key, "Body": data, "ContentType": "application/json", "Metadata": {"sha256": hashlib.sha256(data).hexdigest(), "immutable": "true"}}
    if etag:
        params["IfMatch"] = etag
    else:
        params["IfNoneMatch"] = "*"
    try:
        client.put_object(**params)
    except Exception as exc:
        raise RuntimeError("Destination authoritative pointer changed during migration; retry after review") from exc


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    validate_id(args.dataset_id, "dataset id")
    validate_id(args.bucket, "bucket")
    validate_id(args.production_source_id, "production source id")
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"Source root not found: {source_root}")
    generation_id = args.generation_id or "legacy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    database = (args.database or source_root / "db.sqlite3").resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="pdfsearch-legacy-port-"))
    snapshot = temp_dir / "db.sqlite3"
    try:
        snapshot_sqlite(database, snapshot)
        manifest = inventory_source(source_root, snapshot, generation_id, args.dataset_id, args.include_backups)
        manifest["source"]["label"] = args.source_label
        manifest_data = canonical_json(manifest)
        output = {"bucket": args.bucket, "dataset_id": args.dataset_id, "generation_id": generation_id, "counts": manifest["counts"], "dry_run": not args.publish}
        if args.output:
            args.output.write_bytes(manifest_data)
        if not args.publish:
            return output

        client = s3_client()
        stats = {"uploaded": 0, "already_present": 0}
        for entry in manifest["files"]:
            path = snapshot if entry["artifact_type"] == "database" else source_root / entry["path"]
            status = put_immutable(client, args.bucket, entry["object_key"], path.read_bytes(), "application/octet-stream")
            stats[status.replace("-", "_")] += 1
        manifest_key = f"datasets/{args.dataset_id}/generations/{generation_id}/manifest.json"
        status = put_immutable(client, args.bucket, manifest_key, manifest_data, "application/json")
        stats[status.replace("-", "_")] += 1
        write_registration(
            client,
            args.bucket,
            args.dataset_id,
            registration_payload(args.dataset_id, args.production_source_id, args.source_label),
        )
        if not args.candidate_only:
            write_pointer(
                client,
                args.bucket,
                args.dataset_id,
                generation_id,
                manifest_key,
                hashlib.sha256(manifest_data).hexdigest(),
                "",
                args.production_source_id,
            )
        output.update(stats, pointer_updated=not args.candidate_only)
        return output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="Read-only mounted legacy data root")
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to SOURCE_ROOT/db.sqlite3")
    parser.add_argument("--dataset-id", default=os.environ.get("VAULT_DATASET_ID", DEFAULT_DATASET_ID))
    parser.add_argument("--bucket", default=os.environ.get("ARTIFACT_VAULT_BUCKET", DEFAULT_BUCKET))
    parser.add_argument(
        "--production-source-id",
        default=os.environ.get("PRODUCTION_SOURCE_ID", DEFAULT_PRODUCTION_SOURCE_ID),
    )
    parser.add_argument("--generation-id")
    parser.add_argument("--source-label", default="sahakar-dev-frontend-dockerfile-1cubi5")
    parser.add_argument("--output", type=Path, help="Write the manifest inventory locally")
    parser.add_argument("--include-backups", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Upload and update destination registration/pointer")
    parser.add_argument("--candidate-only", action="store_true", help="Upload and register without changing the destination pointer")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        print(json.dumps(migrate(parse_args()), sort_keys=True))
    except Exception as exc:
        raise SystemExit(f"migration failed: {exc}") from exc
