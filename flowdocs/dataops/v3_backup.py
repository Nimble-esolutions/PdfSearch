"""DataOps v3 snapshot publication: complete manifests, incremental transfer."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .package_v3 import (
    ManifestV3Error,
    build_manifest,
    canonical_json_bytes,
    sign_manifest,
    validate_manifest,
    verify_manifest_signature,
)
from .v3_config import ConnectionView
from .v3_storage import (
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
    evidence_sha256: str


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


@dataclass(frozen=True)
class ManifestPublication:
    recovery_point_id: str
    dataset_id: str
    manifest_sha256: str
    manifest_key: str
    recovery_point_key: str
    activation_ready: bool


@dataclass(frozen=True)
class LatestPointerObservation:
    """The exact latest pointer against which publication must be fenced."""

    key: str
    etag: str
    body: bytes
    recovery_point_id: str
    manifest_sha256: str
    recovery_point_key: str

    @property
    def exists(self) -> bool:
        return bool(self.etag)


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
    declared_evidence_sha256 = str(evidence.get("evidence_sha256") or "")
    unsigned_evidence = dict(evidence)
    unsigned_evidence.pop("evidence_sha256", None)
    observed_evidence_sha256 = hashlib.sha256(
        canonical_json_bytes(unsigned_evidence)
    ).hexdigest()
    if (
        declared_evidence_sha256 != observed_evidence_sha256
        or str(getattr(snapshot, "evidence_sha256", ""))
        != observed_evidence_sha256
    ):
        raise V3BackupError("snapshot_evidence_digest_mismatch")
    if evidence.get("source_stable") is not True:
        raise V3BackupError("snapshot_source_not_stable")
    consistency = evidence.get("consistency")
    if (
        not isinstance(consistency, Mapping)
        or consistency.get("sqlite_integrity") != "ok"
        or consistency.get("foreign_keys") != "ok"
    ):
        raise V3BackupError("snapshot_database_evidence_invalid")
    included_epoch = getattr(snapshot, "included_epoch", None)
    if (
        not isinstance(included_epoch, int)
        or isinstance(included_epoch, bool)
        or included_epoch < 0
    ):
        raise V3BackupError("snapshot_epoch_invalid")
    evidence_epoch = evidence.get("included_epoch")
    if evidence_epoch is not None and evidence_epoch != included_epoch:
        raise V3BackupError("snapshot_epoch_mismatch")
    return SnapshotBundle(
        snapshot_id=snapshot_id,
        workspace=root,
        included_epoch=included_epoch,
        evidence=evidence,
        evidence_sha256=observed_evidence_sha256,
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


def _observe_latest_pointer(
    client,
    connection: ConnectionView,
) -> LatestPointerObservation:
    key = f"{dataset_root(connection)}/refs/latest.json"
    head = head_or_none(client, bucket=connection.bucket, key=key)
    if head is None:
        return LatestPointerObservation(key, "", b"", "", "", "")
    etag = str(head.get("ETag") or "").strip()
    if not etag:
        raise V3BackupError("latest_pointer_etag_missing")
    try:
        body = read_object(client, bucket=connection.bucket, key=key)
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise V3BackupError("latest_pointer_invalid") from exc
    confirmed = head_or_none(client, bucket=connection.bucket, key=key)
    if confirmed is None or str(confirmed.get("ETag") or "").strip() != etag:
        raise V3BackupError("latest_pointer_changed_during_read")
    if payload.get("dataset_id") != connection.dataset_id:
        raise V3BackupError("latest_pointer_dataset_mismatch")
    digest = str(payload.get("manifest_sha256") or "").lower()
    if len(digest) != 64:
        raise V3BackupError("latest_pointer_invalid")
    recovery_point_id = _safe_segment(
        payload.get("recovery_point_id"),
        "latest_pointer_invalid",
    )
    recovery_point_key = str(payload.get("recovery_point_key") or "")
    expected_recovery_point_key = (
        f"{dataset_root(connection)}/recovery-points/{recovery_point_id}.json"
    )
    if recovery_point_key != expected_recovery_point_key:
        raise V3BackupError("latest_pointer_invalid")
    return LatestPointerObservation(
        key,
        etag,
        body,
        recovery_point_id,
        digest,
        recovery_point_key,
    )


def _table_exists(database: sqlite3.Connection, table: str) -> bool:
    return database.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_count(database: sqlite3.Connection, table: str) -> int:
    if not _table_exists(database, table):
        return 0
    return int(database.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _user_count(database: sqlite3.Connection) -> int:
    for table in ("core_customuser", "auth_user"):
        if _table_exists(database, table):
            return _table_count(database, table)
    return 0


def _database_facts(path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as database:
            integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = database.execute("PRAGMA foreign_key_check").fetchall()
            migration_count = _table_count(database, "django_migrations")
            latest_migration = ""
            if migration_count:
                row = database.execute(
                    "SELECT app, name FROM django_migrations "
                    "ORDER BY applied DESC, app DESC, name DESC LIMIT 1"
                ).fetchone()
                latest_migration = f"{row[0]}.{row[1]}" if row else ""
            facts = {
                "documents": _table_count(database, "core_pdffile"),
                "folders": _table_count(database, "core_folder"),
                "users": _user_count(database),
                "migrations": migration_count,
                "latest_migration": latest_migration,
            }
    except (sqlite3.Error, OSError, TypeError) as exc:
        raise V3BackupError("snapshot_database_invalid") from exc
    if integrity != "ok":
        raise V3BackupError("snapshot_database_integrity_failed")
    if foreign_keys:
        raise V3BackupError("snapshot_database_foreign_keys_failed")
    return facts


def _declared_count(counts: Mapping[str, Any], field: str) -> int:
    value = counts.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise V3BackupError("snapshot_inventory_malformed")
    return value


def _reconciled_counts(
    evidence: Mapping[str, Any],
    files: list[dict[str, Any]],
) -> dict[str, int]:
    databases = [item for item in files if item["kind"] == "database"]
    if len(databases) != 1:
        raise V3BackupError("snapshot_database_count_invalid")
    facts = _database_facts(databases[0]["local_path"])
    inventory = evidence.get("inventory")
    inventory_counts = (
        inventory.get("counts") if isinstance(inventory, Mapping) else None
    )
    if not isinstance(inventory_counts, Mapping):
        raise V3BackupError("snapshot_inventory_malformed")
    declarations = {
        "documents": _declared_count(inventory_counts, "pdf_rows"),
        "folders": _declared_count(inventory_counts, "folders"),
        "users": _declared_count(inventory_counts, "users"),
    }
    for field, code in (
        ("documents", "snapshot_document_count_mismatch"),
        ("folders", "snapshot_folder_count_mismatch"),
        ("users", "snapshot_user_count_mismatch"),
    ):
        if declarations[field] != facts[field]:
            raise V3BackupError(code)
    pdf_objects = sum(
        1
        for item in files
        if item["kind"] == "media" and Path(item["path"]).suffix.lower() == ".pdf"
    )
    if pdf_objects != facts["documents"]:
        raise V3BackupError("snapshot_pdf_object_count_mismatch")
    inventory_database = inventory.get("database")
    migrations = (
        inventory_database.get("migrations")
        if isinstance(inventory_database, Mapping)
        else None
    )
    declared_latest = (
        str(migrations.get("latest") or "") if isinstance(migrations, Mapping) else ""
    )
    if declared_latest != facts["latest_migration"]:
        raise V3BackupError("snapshot_migration_mismatch")
    declared_migration_count = (
        migrations.get("count") if isinstance(migrations, Mapping) else None
    )
    if declared_migration_count is not None and (
        not isinstance(declared_migration_count, int)
        or isinstance(declared_migration_count, bool)
        or declared_migration_count != facts["migrations"]
    ):
        raise V3BackupError("snapshot_migration_count_mismatch")
    return {
        "documents": facts["documents"],
        "folders": facts["folders"],
        "users": facts["users"],
        "migrations": facts["migrations"],
        "objects": len(files),
        "bytes": sum(item["size"] for item in files),
    }


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


def _database_schema(evidence: Mapping[str, Any]) -> str:
    inventory = evidence.get("inventory")
    if isinstance(inventory, Mapping):
        database = inventory.get("database")
        if isinstance(database, Mapping):
            migrations = database.get("migrations")
            if isinstance(migrations, Mapping) and migrations.get("latest"):
                return str(migrations["latest"])
    return "unknown-verified-snapshot"


def _created_at_from_epoch(included_epoch: int) -> str:
    try:
        seconds, nanoseconds = divmod(included_epoch, 1_000_000_000)
        captured = datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
            microseconds=nanoseconds // 1_000,
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise V3BackupError("snapshot_epoch_invalid") from exc
    return captured.isoformat().replace("+00:00", "Z")


def _load_retry_manifest(
    client,
    connection: ConnectionView,
    observation: LatestPointerObservation,
    *,
    signing_key: bytes,
    signing_key_id: str,
) -> dict[str, Any]:
    key = (
        f"{dataset_root(connection)}/manifests/"
        f"{observation.manifest_sha256}.json"
    )
    try:
        manifest = json.loads(read_object(client, bucket=connection.bucket, key=key))
        validate_manifest(manifest)
    except (ManifestV3Error, ValueError, TypeError) as exc:
        raise V3BackupError("recovery_point_retry_manifest_invalid") from exc
    if (
        manifest.get("dataset_id") != connection.dataset_id
        or manifest.get("recovery_point_id") != observation.recovery_point_id
        or manifest.get("manifest_sha256") != observation.manifest_sha256
        or manifest.get("signature", {}).get("key_id") != signing_key_id
        or manifest.get("backup_mode") != "smart"
        or manifest.get("lineage", {}).get("transition") != "backup"
        or not verify_manifest_signature(manifest, key=signing_key)
    ):
        raise V3BackupError("recovery_point_retry_manifest_mismatch")
    return manifest


def _publish_latest_pointer(
    client,
    connection: ConnectionView,
    *,
    observed: LatestPointerObservation,
    recovery_point_id: str,
    manifest_sha256: str,
    recovery_point_key: str,
) -> None:
    key = f"{dataset_root(connection)}/refs/latest.json"
    if observed.key != key:
        raise V3BackupError("latest_pointer_observation_invalid")
    payload = {
        "schema_version": 3,
        "dataset_id": connection.dataset_id,
        "recovery_point_id": recovery_point_id,
        "manifest_sha256": manifest_sha256,
        "recovery_point_key": recovery_point_key,
    }
    body = canonical_json_bytes(payload)
    digest = hashlib.sha256(body).hexdigest()
    if observed.body == body:
        current = head_or_none(client, bucket=connection.bucket, key=key)
        if (
            current is None
            or str(current.get("ETag") or "").strip() != observed.etag
            or read_object(client, bucket=connection.bucket, key=key) != body
        ):
            raise V3BackupError("latest_pointer_conflict")
        return
    conditions = {"IfNoneMatch": "*"}
    if observed.exists:
        conditions = {"IfMatch": observed.etag}
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


def publish_manifest_contract(
    *,
    connection: ConnectionView,
    client,
    manifest: Mapping[str, Any],
    signing_key: bytes,
    signing_key_id: str,
    latest_pointer: LatestPointerObservation | None = None,
) -> tuple[dict[str, Any], ManifestPublication]:
    """Publish one signed manifest, immutable descriptor, and fenced latest ref."""

    if not connection.capabilities.get("write") or not connection.capabilities.get(
        "conditional_write"
    ):
        raise V3BackupError("owned_connection_not_writable")
    recovery_point_id = _safe_segment(
        manifest.get("recovery_point_id"),
        "recovery_point_id_invalid",
    )
    if manifest.get("dataset_id") != connection.dataset_id:
        raise V3BackupError("manifest_dataset_mismatch")
    if latest_pointer is None:
        latest_pointer = _observe_latest_pointer(client, connection)
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
    activation_ready = all(
        not component.get("rebuild_required", False)
        for component in signed_manifest["components"].values()
    )
    recovery_point = {
        "schema_version": 3,
        "dataset_id": connection.dataset_id,
        "recovery_point_id": recovery_point_id,
        "manifest_sha256": signed_manifest["manifest_sha256"],
        "manifest_key": manifest_key,
        "signature_key_id": signing_key_id,
        "data_complete": True,
        "activation_ready": activation_ready,
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
        observed=latest_pointer,
        recovery_point_id=recovery_point_id,
        manifest_sha256=signed_manifest["manifest_sha256"],
        recovery_point_key=recovery_point_key,
    )
    return signed_manifest, ManifestPublication(
        recovery_point_id=recovery_point_id,
        dataset_id=connection.dataset_id,
        manifest_sha256=signed_manifest["manifest_sha256"],
        manifest_key=manifest_key,
        recovery_point_key=recovery_point_key,
        activation_ready=activation_ready,
    )


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
    counts = _reconciled_counts(bundle.evidence, files)
    latest_pointer = _observe_latest_pointer(client, connection)
    retry_manifest = None
    if latest_pointer.recovery_point_id == recovery_point_id:
        retry_manifest = _load_retry_manifest(
            client,
            connection,
            latest_pointer,
            signing_key=signing_key,
            signing_key_id=signing_key_id,
        )
        retry_lineage = retry_manifest.get("lineage", {})
        parent_recovery_point_id = str(
            retry_lineage.get("parent_generation_id") or ""
        )
        base_digest = str(retry_manifest.get("base_manifest_sha256") or "")
        if str(retry_lineage.get("parent_manifest_sha256") or "") != base_digest:
            raise V3BackupError("recovery_point_retry_manifest_mismatch")
        created_at = str(retry_manifest.get("created_at") or "")
    else:
        parent_recovery_point_id = latest_pointer.recovery_point_id
        base_digest = latest_pointer.manifest_sha256
        created_at = _created_at_from_epoch(bundle.included_epoch)

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
            "sqlite_integrity": bundle.evidence["consistency"]["sqlite_integrity"],
            "foreign_keys": bundle.evidence["consistency"]["foreign_keys"],
            "source_stable": bundle.evidence["source_stable"],
            "snapshot_evidence_sha256": bundle.evidence_sha256,
        },
        components=_components(files, bundle.evidence),
        files=[
            {key: value for key, value in item.items() if key != "local_path"}
            for item in files
        ],
        counts=counts,
        lineage={
            "transition": "backup",
            "parent_dataset_id": connection.dataset_id if base_digest else "",
            "parent_generation_id": parent_recovery_point_id,
            "parent_manifest_sha256": base_digest,
        },
        created_at=created_at,
        base_manifest_sha256=base_digest,
    )
    if (
        retry_manifest is not None
        and manifest["manifest_sha256"] != latest_pointer.manifest_sha256
    ):
        raise V3BackupError("recovery_point_retry_conflict")

    uploaded_objects = uploaded_bytes = reused_objects = reused_bytes = 0
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

    signed_manifest, publication = publish_manifest_contract(
        connection=connection,
        client=client,
        manifest=manifest,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
        latest_pointer=latest_pointer,
    )
    receipt = BackupReceipt(
        recovery_point_id=recovery_point_id,
        dataset_id=connection.dataset_id,
        manifest_sha256=signed_manifest["manifest_sha256"],
        manifest_key=publication.manifest_key,
        recovery_point_key=publication.recovery_point_key,
        uploaded_objects=uploaded_objects,
        uploaded_bytes=uploaded_bytes,
        reused_objects=reused_objects,
        reused_bytes=reused_bytes,
        total_objects=counts["objects"],
        total_bytes=counts["bytes"],
        base_manifest_sha256=base_digest,
    )
    return signed_manifest, receipt
