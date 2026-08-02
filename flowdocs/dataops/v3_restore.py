"""Verified, quarantine-first recovery for DataOps v3 recovery points."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.rehearsal import RehearsalError, rehearse_migrations

from .models import RecoveryPoint
from .package_v3 import (
    ManifestV3Error,
    canonical_json_bytes,
    validate_manifest,
    verify_manifest_signature,
)
from .v3_backup import blob_key, dataset_root
from .v3_config import ConnectionView, connection_from_model
from .v3_storage import client_for_connection, read_object


SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class V3RestoreError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedRecoveryPoint:
    point: RecoveryPoint
    connection: ConnectionView
    client: Any
    descriptor: Mapping[str, Any]
    manifest: Mapping[str, Any]


def _json_object(raw: bytes, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise V3RestoreError(code) from exc
    if not isinstance(payload, dict):
        raise V3RestoreError(code)
    return payload


def _safe_object_key(key: str, *, root: str, code: str) -> str:
    value = str(key or "").strip()
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not value.startswith(root.rstrip("/") + "/")
    ):
        raise V3RestoreError(code)
    return value


def load_verified_recovery_point(
    point: RecoveryPoint,
    *,
    signing_key: bytes,
    client_factory=client_for_connection,
) -> VerifiedRecoveryPoint:
    """Resolve a local projection into a fully verified remote manifest."""

    if point.format_version != 3 or point.state != RecoveryPoint.State.VERIFIED:
        raise V3RestoreError("recovery_point_not_verified")
    if not point.connection_id:
        raise V3RestoreError("recovery_point_connection_missing")
    if not signing_key:
        raise V3RestoreError("manifest_signing_key_missing")
    connection = connection_from_model(point.connection)
    root = dataset_root(connection)
    descriptor_key = _safe_object_key(
        point.prefix,
        root=root,
        code="recovery_point_key_unsafe",
    )
    try:
        client = client_factory(connection)
        descriptor = _json_object(
            read_object(client, bucket=connection.bucket, key=descriptor_key),
            "recovery_point_descriptor_invalid",
        )
    except V3RestoreError:
        raise
    except Exception as exc:
        raise V3RestoreError(
            "recovery_point_read_failed", retryable=True
        ) from exc
    if (
        descriptor.get("schema_version") != 3
        or descriptor.get("dataset_id") != point.dataset_id
        or descriptor.get("recovery_point_id") != point.release_id
        or descriptor.get("manifest_sha256") != point.manifest_digest
        or descriptor.get("signature_key_id") != point.signature_key_id
        or descriptor.get("data_complete") is not True
    ):
        raise V3RestoreError("recovery_point_descriptor_mismatch")
    manifest_key = _safe_object_key(
        descriptor.get("manifest_key"),
        root=root,
        code="manifest_key_unsafe",
    )
    expected_manifest_key = f"{root}/manifests/{point.manifest_digest}.json"
    if manifest_key != expected_manifest_key:
        raise V3RestoreError("manifest_key_mismatch")
    try:
        manifest = _json_object(
            read_object(client, bucket=connection.bucket, key=manifest_key),
            "manifest_json_invalid",
        )
        validate_manifest(manifest)
    except ManifestV3Error as exc:
        raise V3RestoreError(exc.code) from exc
    except V3RestoreError:
        raise
    except Exception as exc:
        raise V3RestoreError("manifest_read_failed", retryable=True) from exc
    if (
        manifest.get("dataset_id") != point.dataset_id
        or manifest.get("recovery_point_id") != point.release_id
        or manifest.get("manifest_sha256") != point.manifest_digest
        or manifest.get("signature", {}).get("key_id") != point.signature_key_id
    ):
        raise V3RestoreError("manifest_projection_mismatch")
    if not verify_manifest_signature(manifest, key=signing_key):
        raise V3RestoreError("manifest_signature_invalid")
    return VerifiedRecoveryPoint(
        point=point,
        connection=connection,
        client=client,
        descriptor=descriptor,
        manifest=manifest,
    )


def _stream_verified_blob(
    verified: VerifiedRecoveryPoint,
    *,
    target: Path,
    digest: str,
    size: int,
) -> None:
    key = blob_key(verified.connection, digest)
    try:
        response = verified.client.get_object(
            Bucket=verified.connection.bucket,
            Key=key,
        )
        body = response["Body"]
        hasher = hashlib.sha256()
        observed = 0
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as output:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                hasher.update(chunk)
                observed += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(target, 0o600)
    except Exception as exc:
        raise V3RestoreError("recovery_blob_download_failed", retryable=True) from exc
    if observed != size or hasher.hexdigest() != digest:
        raise V3RestoreError("recovery_blob_digest_mismatch")


def _sqlite_evidence(database: Path) -> dict[str, Any]:
    if not database.is_file() or database.is_symlink():
        raise V3RestoreError("restored_database_missing")
    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as exc:
        raise V3RestoreError("restored_database_invalid") from exc
    if integrity != "ok":
        raise V3RestoreError("restored_database_integrity_failed")
    if foreign_keys:
        raise V3RestoreError("restored_database_foreign_keys_failed")
    return {"integrity": "ok", "foreign_keys": "ok"}


def _local_evidence(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    database = root / "db.sqlite3"
    sqlite_evidence = _sqlite_evidence(database)
    documents = (
        sum(
            1
            for path in (root / "media").rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        )
        if (root / "media").is_dir()
        else 0
    )
    expected_documents = int(manifest.get("counts", {}).get("documents", 0))
    if documents != expected_documents:
        raise V3RestoreError("restored_media_count_mismatch")
    indexed = 0
    try:
        uri = f"file:{database.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            indexed = int(
                connection.execute(
                    "SELECT COUNT(*) FROM core_pdffile "
                    "WHERE indexed = 1 AND processing_status = 'ready'"
                ).fetchone()[0]
            )
    except sqlite3.Error:
        indexed = 0
    ratio = 1.0 if documents == 0 else min(1.0, indexed / documents)
    return {
        "sqlite": sqlite_evidence,
        "documents": documents,
        "indexed_documents": indexed,
        "indexing_ratio": round(ratio, 4),
    }


def _existing_workspace(
    final: Path,
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not final.is_dir() or final.is_symlink():
        return None
    try:
        receipt = _json_object(
            (final / ".dataops-restore.json").read_bytes(),
            "restore_receipt_invalid",
        )
    except (OSError, V3RestoreError):
        return None
    if (
        receipt.get("verified") is not True
        or receipt.get("manifest_sha256") != manifest.get("manifest_sha256")
    ):
        return None
    for item in manifest["files"]:
        path = final / item["path"]
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
            return None
        hasher = hashlib.sha256()
        observed = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
                observed += len(chunk)
        if observed != item["size"] or hasher.hexdigest() != item["sha256"]:
            return None
    return receipt


def materialize_quarantine(
    verified: VerifiedRecoveryPoint,
    *,
    quarantine_root: str | Path,
) -> dict[str, Any]:
    """Download into a new generation; active data and pointers are untouched."""

    release_id = str(verified.manifest["recovery_point_id"])
    if not SAFE_SEGMENT.fullmatch(release_id):
        raise V3RestoreError("recovery_point_id_invalid")
    raw_root = Path(quarantine_root)
    if raw_root.is_symlink():
        raise V3RestoreError("quarantine_root_unsafe")
    root = raw_root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = root / f"restore-{release_id}-{verified.point.manifest_digest[:12]}"
    existing = _existing_workspace(final, manifest=verified.manifest)
    if existing is not None:
        return {**existing, "workspace": str(final), "reused": True}
    if final.exists():
        raise V3RestoreError("restore_workspace_conflict")
    temporary = Path(tempfile.mkdtemp(prefix=".dataops-v3-", dir=root))
    try:
        for item in verified.manifest["files"]:
            target = temporary / item["path"]
            try:
                target.resolve().relative_to(temporary)
            except ValueError as exc:
                raise V3RestoreError("restore_path_unsafe") from exc
            _stream_verified_blob(
                verified,
                target=target,
                digest=item["sha256"],
                size=item["size"],
            )
        evidence = _local_evidence(temporary, verified.manifest)
        receipt = {
            "schema_version": 3,
            "verified": True,
            "dataset_id": verified.point.dataset_id,
            "recovery_point_id": release_id,
            "manifest_sha256": verified.point.manifest_digest,
            "object_count": len(verified.manifest["files"]),
            "byte_count": sum(item["size"] for item in verified.manifest["files"]),
            "evidence": evidence,
        }
        receipt_path = temporary / ".dataops-restore.json"
        receipt_path.write_bytes(canonical_json_bytes(receipt) + b"\n")
        os.chmod(receipt_path, 0o600)
        os.replace(temporary, final)
        return {**receipt, "workspace": str(final), "reused": False}
    except Exception:
        failed = root / f"failed-{release_id}-{uuid.uuid4().hex[:12]}"
        try:
            os.replace(temporary, failed)
        except OSError:
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def rehearse_quarantine(
    restore_receipt: Mapping[str, Any],
    *,
    migration_runner=rehearse_migrations,
) -> dict[str, Any]:
    """Apply current migrations to the candidate only and persist safe evidence."""

    if restore_receipt.get("verified") is not True:
        raise V3RestoreError("restore_receipt_unverified")
    workspace = Path(str(restore_receipt.get("workspace") or ""))
    if not workspace.is_dir() or workspace.is_symlink():
        raise V3RestoreError("restore_workspace_invalid")
    workspace = workspace.resolve()
    database = workspace / "db.sqlite3"
    evidence_path = workspace / ".dataops-rehearsal.json"
    if evidence_path.is_file() and not evidence_path.is_symlink():
        try:
            existing = _json_object(
                evidence_path.read_bytes(),
                "rehearsal_receipt_invalid",
            )
        except (OSError, V3RestoreError):
            existing = None
        if (
            existing
            and existing.get("success") is True
            and existing.get("manifest_sha256")
            == restore_receipt.get("manifest_sha256")
        ):
            _sqlite_evidence(database)
            return {**existing, "reused": True}
        raise V3RestoreError("rehearsal_receipt_conflict")
    try:
        migration = migration_runner(
            database,
            workspace_path=workspace,
            promote_to=database,
        )
    except RehearsalError as exc:
        raise V3RestoreError(str(exc)) from exc
    except Exception as exc:
        raise V3RestoreError("migration_rehearsal_failed") from exc
    sqlite_evidence = _sqlite_evidence(database)
    receipt = {
        "schema_version": 3,
        "success": True,
        "manifest_sha256": restore_receipt.get("manifest_sha256"),
        "migration": dict(migration),
        "sqlite": sqlite_evidence,
    }
    temporary = evidence_path.with_name(f".{evidence_path.name}.partial")
    temporary.write_bytes(canonical_json_bytes(receipt) + b"\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, evidence_path)
    return {**receipt, "reused": False}
