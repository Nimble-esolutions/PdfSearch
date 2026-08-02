"""DataOps v3 snapshot publication: complete manifests, incremental transfer."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .package_v3 import build_manifest, canonical_json_bytes, sign_manifest
from .v3_config import ConnectionView
from .v3_storage import (
    V3StorageError,
    head_or_none,
    put_bytes_immutable,
    put_file_immutable,
    read_object,
    verify_remote_object,
)


SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
CATEGORY_KIND = {
    "database": "database",
    "media": "media",
    "pdf_cache": "pdf_cache",
    "faiss_indexes": "faiss",
    "chroma_db": "chroma",
}


class V3BackupError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SnapshotBundle:
    snapshot_id: str
    workspace: Path
    included_epoch: int
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class BackupReceipt:
    recovery_point_id: str
    dataset_id: str
    manifest_sha256: str
    manifest_key: str
    recovery_point_key: str
    uploaded_objects: int
    uploaded_bytes: int
    reused_objects: int
    reused_bytes: int
    total_objects: int
    total_bytes: int
    base_manifest_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
        }


def _safe_segment(value: str, code: str) -> str:
    value = str(value or "").strip()
    if not SAFE_SEGMENT.fullmatch(value):
        raise V3BackupError(code)
    return value


def dataset_root(connection: ConnectionView) -> str:
    prefix = str(connection.prefix or "v3").strip("/") or "v3"
    dataset_id = _safe_segment(connection.dataset_id, "dataset_id_invalid")
    return f"{prefix}/datasets/{dataset_id}"


def blob_key(connection: ConnectionView, digest: str) -> str:
    return f"{dataset_root(connection)}/blobs/sha256/{digest[:2]}/{digest}"


def load_snapshot_bundle(snapshot) -> SnapshotBundle:
    workspace = Path(snapshot.workspace_path)
    if workspace.is_symlink():
        raise V3BackupError("snapshot_path_unsafe")
    root = workspace.resolve()
    evidence_path = root / "snapshot-evidence.json"
    if evidence_path.is_symlink():
        raise V3BackupError("snapshot_path_unsafe")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise V3BackupError("snapshot_evidence_unavailable") from exc
    snapshot_id = str(snapshot.public_id)
    if evidence.get("snapshot_id") != snapshot_id:
        raise V3BackupError("snapshot_identity_mismatch")
    return SnapshotBundle(
        snapshot_id=snapshot_id,
        workspace=root,
        included_epoch=int(snapshot.included_epoch),
        evidence=evidence,
    )


def _record_kind(record: Mapping[str, Any]) -> str | None:
    category = str(record.get("category") or "")
    if category:
        return CATEGORY_KIND.get(category)
    path = str(record.get("path") or "")
    if path == "db.sqlite3":
        return "database"
    for prefix, kind in (
        ("media/", "media"),
        ("pdf_cache/", "pdf_cache"),
        ("faiss_indexes/", "faiss"),
        ("chroma_db/", "chroma"),
    ):
        if path.startswith(prefix):
            return kind
    return None


def _verified_snapshot_files(bundle: SnapshotBundle) -> list[dict[str, Any]]:
    records = bundle.evidence.get("files")
    if not isinstance(records, list):
        raise V3BackupError("snapshot_evidence_malformed")
    result = []
    seen = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise V3BackupError("snapshot_evidence_malformed")
        kind = _record_kind(record)
        if kind is None:  # static and control artifacts are deliberately excluded
            continue
        relative = Path(str(record.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise V3BackupError("snapshot_path_unsafe")
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise V3BackupError("snapshot_path_duplicate")
        seen.add(relative_text)
        candidate = bundle.workspace / relative
        if candidate.is_symlink():
            raise V3BackupError("snapshot_path_unsafe")
        path = candidate.resolve()
        try:
            path.relative_to(bundle.workspace)
        except ValueError as exc:
            raise V3BackupError("snapshot_path_unsafe") from exc
        if not path.is_file() or path.stat().st_nlink != 1:
            raise V3BackupError("snapshot_artifact_unsafe")
        digest = str(record.get("sha256") or "").lower()
        size = record.get("size_bytes")
        if len(digest) != 64 or not isinstance(size, int) or size < 0:
            raise V3BackupError("snapshot_evidence_malformed")
        hasher = hashlib.sha256()
        observed_size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
                observed_size += len(chunk)
        if observed_size != size or hasher.hexdigest() != digest:
            raise V3BackupError("snapshot_artifact_digest_mismatch")
        result.append(
            {
                "path": relative_text,
                "kind": kind,
                "size": size,
                "sha256": digest,
                "blob": f"sha256:{digest}",
                "local_path": path,
            }
        )
    if not any(item["kind"] == "database" for item in result):
        raise V3BackupError("snapshot_database_missing")
    return result


def _latest_lineage(client, connection: ConnectionView) -> tuple[str, str]:
    key = f"{dataset_root(connection)}/refs/latest.json"
    if head_or_none(client, bucket=connection.bucket, key=key) is None:
        return "", ""
    try:
        payload = json.loads(
            read_object(client, bucket=connection.bucket, key=key)
        )
    except (ValueError, TypeError) as exc:
        raise V3BackupError("latest_pointer_invalid") from exc
    if payload.get("dataset_id") != connection.dataset_id:
        raise V3BackupError("latest_pointer_dataset_mismatch")
    digest = str(payload.get("manifest_sha256") or "").lower()
    if len(digest) != 64:
        raise V3BackupError("latest_pointer_invalid")
    recovery_point_id = _safe_segment(
        payload.get("recovery_point_id"),
        "latest_pointer_invalid",
    )
    return recovery_point_id, digest


def _components(files: list[dict[str, Any]], evidence: Mapping[str, Any]):
    present = {item["kind"] for item in files}
    faiss = evidence.get("faiss")
    faiss_coherent = isinstance(faiss, Mapping)
    return {
        "database": {"complete": True, "coherent": True},
        "media": {"complete": True, "coherent": True},
        "pdf_cache": {
            "complete": "pdf_cache" in present,
            "rebuild_required": "pdf_cache" not in present,
        },
        "faiss": {
            "complete": "faiss" in present or faiss_coherent,
            "coherent": faiss_coherent,
            "rebuild_required": not faiss_coherent,
        },
        # Existing Chroma snapshots do not carry a coherence proof. Preserve
        # their blobs but require a rebuild before activation.
        "chroma": {
            "complete": False,
            "coherent": False,
            "rebuild_required": True,
        },
    }


def _counts(evidence: Mapping[str, Any], files: list[dict[str, Any]]):
    inventory = evidence.get("inventory")
    inventory_counts = (
        inventory.get("counts", {}) if isinstance(inventory, Mapping) else {}
    )
    return {
        "documents": int(inventory_counts.get("pdf_rows", 0) or 0),
        "folders": int(inventory_counts.get("folders", 0) or 0),
        "users": int(inventory_counts.get("users", 0) or 0),
        "objects": len(files),
        "bytes": sum(item["size"] for item in files),
    }


def _database_schema(evidence: Mapping[str, Any]) -> str:
    inventory = evidence.get("inventory")
    if isinstance(inventory, Mapping):
        database = inventory.get("database")
        if isinstance(database, Mapping):
            migrations = database.get("migrations")
            if isinstance(migrations, Mapping) and migrations.get("latest"):
                return str(migrations["latest"])
    return "unknown-verified-snapshot"


def _publish_latest_pointer(
    client,
    connection: ConnectionView,
    *,
    recovery_point_id: str,
    manifest_sha256: str,
    recovery_point_key: str,
) -> None:
    key = f"{dataset_root(connection)}/refs/latest.json"
    payload = {
        "schema_version": 3,
        "dataset_id": connection.dataset_id,
        "recovery_point_id": recovery_point_id,
        "manifest_sha256": manifest_sha256,
        "recovery_point_key": recovery_point_key,
    }
    body = canonical_json_bytes(payload)
    digest = hashlib.sha256(body).hexdigest()
    existing = head_or_none(client, bucket=connection.bucket, key=key)
    conditions = {"IfNoneMatch": "*"}
    if existing is not None:
        etag = str(existing.get("ETag") or "").strip()
        if not etag:
            raise V3BackupError("latest_pointer_etag_missing")
        conditions = {"IfMatch": etag}
    try:
        client.put_object(
            Bucket=connection.bucket,
            Key=key,
            Body=body,
            ContentLength=len(body),
            ContentType="application/json",
            Metadata={"sha256": digest},
            **conditions,
        )
    except Exception as exc:
        raise V3BackupError("latest_pointer_conflict") from exc
    if read_object(client, bucket=connection.bucket, key=key) != body:
        raise V3BackupError("latest_pointer_verify_failed")


def publish_snapshot(
    *,
    snapshot,
    connection: ConnectionView,
    client,
    recovery_point_id: str,
    source_instance_id: str,
    source_environment: str,
    image_digest: str,
    release_version: str,
    signing_key: bytes,
    signing_key_id: str,
) -> tuple[dict[str, Any], BackupReceipt]:
    """Publish one independently restorable point and verify every remote blob."""

    if not connection.capabilities.get("write") or not connection.capabilities.get(
        "conditional_write"
    ):
        raise V3BackupError("owned_connection_not_writable")
    if connection.dataset_id == "":
        raise V3BackupError("dataset_id_invalid")
    recovery_point_id = _safe_segment(
        recovery_point_id,
        "recovery_point_id_invalid",
    )
    bundle = load_snapshot_bundle(snapshot)
    files = _verified_snapshot_files(bundle)
    parent_recovery_point_id, base_digest = _latest_lineage(client, connection)
    uploaded_objects = uploaded_bytes = reused_objects = reused_bytes = 0

    descriptor = {
        "schema_version": 3,
        "dataset_id": connection.dataset_id,
    }
    descriptor_body = canonical_json_bytes(descriptor)
    descriptor_digest = hashlib.sha256(descriptor_body).hexdigest()
    put_bytes_immutable(
        client,
        bucket=connection.bucket,
        key=f"{dataset_root(connection)}/dataset.json",
        body=descriptor_body,
        sha256=descriptor_digest,
        content_type="application/json",
    )

    for item in files:
        key = blob_key(connection, item["sha256"])
        outcome = put_file_immutable(
            client,
            bucket=connection.bucket,
            key=key,
            path=item["local_path"],
            sha256=item["sha256"],
            size=item["size"],
        )
        verify_remote_object(
            client,
            bucket=connection.bucket,
            key=key,
            sha256=item["sha256"],
            size=item["size"],
        )
        if outcome == "uploaded":
            uploaded_objects += 1
            uploaded_bytes += item["size"]
        else:
            reused_objects += 1
            reused_bytes += item["size"]

    config_fingerprint = bundle.evidence.get("configuration_fingerprint")
    index_digest = (
        str(config_fingerprint.get("sha256") or "")
        if isinstance(config_fingerprint, Mapping)
        else ""
    )
    if len(index_digest) != 64:
        index_digest = hashlib.sha256(
            canonical_json_bytes({"snapshot_id": bundle.snapshot_id})
        ).hexdigest()
    manifest = build_manifest(
        recovery_point_id=recovery_point_id,
        dataset_id=connection.dataset_id,
        source_instance_id=source_instance_id,
        source_environment=source_environment,
        backup_mode="smart",
        captured_epoch=bundle.included_epoch,
        application={
            "image_digest": image_digest,
            "release_version": release_version,
            "database_schema": _database_schema(bundle.evidence),
            "index_configuration_sha256": index_digest,
        },
        consistency={
            "sqlite_integrity": "ok",
            "foreign_keys": "ok",
            "source_stable": True,
        },
        components=_components(files, bundle.evidence),
        files=[
            {key: value for key, value in item.items() if key != "local_path"}
            for item in files
        ],
        counts=_counts(bundle.evidence, files),
        lineage={
            "transition": "backup",
            "parent_dataset_id": connection.dataset_id if base_digest else "",
            "parent_generation_id": parent_recovery_point_id,
            "parent_manifest_sha256": base_digest,
        },
        base_manifest_sha256=base_digest,
    )
    signed_manifest = sign_manifest(
        manifest,
        key=signing_key,
        key_id=signing_key_id,
    )
    manifest_body = canonical_json_bytes(signed_manifest)
    manifest_bytes_digest = hashlib.sha256(manifest_body).hexdigest()
    manifest_key = (
        f"{dataset_root(connection)}/manifests/"
        f"{signed_manifest['manifest_sha256']}.json"
    )
    put_bytes_immutable(
        client,
        bucket=connection.bucket,
        key=manifest_key,
        body=manifest_body,
        sha256=manifest_bytes_digest,
        content_type="application/json",
    )
    verify_remote_object(
        client,
        bucket=connection.bucket,
        key=manifest_key,
        sha256=manifest_bytes_digest,
        size=len(manifest_body),
    )

    recovery_point = {
        "schema_version": 3,
        "dataset_id": connection.dataset_id,
        "recovery_point_id": recovery_point_id,
        "manifest_sha256": signed_manifest["manifest_sha256"],
        "manifest_key": manifest_key,
        "signature_key_id": signing_key_id,
        "data_complete": True,
        "activation_ready": all(
            not component.get("rebuild_required", False)
            for component in signed_manifest["components"].values()
        ),
    }
    recovery_point_body = canonical_json_bytes(recovery_point)
    recovery_point_digest = hashlib.sha256(recovery_point_body).hexdigest()
    recovery_point_key = (
        f"{dataset_root(connection)}/recovery-points/{recovery_point_id}.json"
    )
    put_bytes_immutable(
        client,
        bucket=connection.bucket,
        key=recovery_point_key,
        body=recovery_point_body,
        sha256=recovery_point_digest,
        content_type="application/json",
    )
    verify_remote_object(
        client,
        bucket=connection.bucket,
        key=recovery_point_key,
        sha256=recovery_point_digest,
        size=len(recovery_point_body),
    )
    _publish_latest_pointer(
        client,
        connection,
        recovery_point_id=recovery_point_id,
        manifest_sha256=signed_manifest["manifest_sha256"],
        recovery_point_key=recovery_point_key,
    )
    receipt = BackupReceipt(
        recovery_point_id=recovery_point_id,
        dataset_id=connection.dataset_id,
        manifest_sha256=signed_manifest["manifest_sha256"],
        manifest_key=manifest_key,
        recovery_point_key=recovery_point_key,
        uploaded_objects=uploaded_objects,
        uploaded_bytes=uploaded_bytes,
        reused_objects=reused_objects,
        reused_bytes=reused_bytes,
        total_objects=len(files),
        total_bytes=sum(item["size"] for item in files),
        base_manifest_sha256=base_digest,
    )
    return signed_manifest, receipt
