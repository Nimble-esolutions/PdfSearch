"""Automatic foreign-dataset import/rebind for verified DataOps v3 points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .package_v3 import build_manifest
from .v3_backup import (
    blob_key,
    publish_manifest_contract,
)
from .v3_config import ConnectionView
from .v3_restore import VerifiedRecoveryPoint
from .v3_storage import put_file_immutable, verify_remote_object


class V3ImportError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImportReceipt:
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


def import_rebind_recovery_point(
    *,
    source: VerifiedRecoveryPoint,
    quarantine_receipt: Mapping[str, Any],
    destination: ConnectionView,
    destination_client,
    recovery_point_id: str,
    destination_instance_id: str,
    destination_environment: str,
    signing_key: bytes,
    signing_key_id: str,
) -> tuple[dict[str, Any], ImportReceipt]:
    """Copy verified blobs and publish a destination-owned lineage."""

    if source.point.dataset_id == destination.dataset_id:
        raise V3ImportError("import_rebind_dataset_unchanged")
    if quarantine_receipt.get("verified") is not True:
        raise V3ImportError("import_quarantine_unverified")
    if (
        quarantine_receipt.get("manifest_sha256")
        != source.point.manifest_digest
    ):
        raise V3ImportError("import_quarantine_manifest_mismatch")
    workspace = Path(str(quarantine_receipt.get("workspace") or ""))
    if workspace.is_symlink() or not workspace.is_dir():
        raise V3ImportError("import_quarantine_missing")
    workspace = workspace.resolve()
    uploaded_objects = uploaded_bytes = reused_objects = reused_bytes = 0
    for item in source.manifest["files"]:
        path = workspace / item["path"]
        try:
            path.resolve().relative_to(workspace)
        except ValueError as exc:
            raise V3ImportError("import_path_unsafe") from exc
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
    manifest = build_manifest(
        recovery_point_id=recovery_point_id,
        dataset_id=destination.dataset_id,
        source_instance_id=destination_instance_id,
        source_environment=destination_environment,
        backup_mode="import",
        captured_epoch=int(source.manifest["captured_epoch"]),
        application=dict(source.manifest["application"]),
        consistency=dict(source.manifest["consistency"]),
        components=dict(source.manifest["components"]),
        files=list(source.manifest["files"]),
        counts=dict(source.manifest["counts"]),
        lineage={
            "transition": "import_rebind",
            "parent_dataset_id": source.point.dataset_id,
            "parent_generation_id": source.point.release_id,
            "parent_manifest_sha256": source.point.manifest_digest,
        },
        created_at=str(source.manifest["created_at"]),
    )
    signed_manifest, publication = publish_manifest_contract(
        connection=destination,
        client=destination_client,
        manifest=manifest,
        signing_key=signing_key,
        signing_key_id=signing_key_id,
    )
    return signed_manifest, ImportReceipt(
        recovery_point_id=publication.recovery_point_id,
        dataset_id=publication.dataset_id,
        manifest_sha256=publication.manifest_sha256,
        manifest_key=publication.manifest_key,
        recovery_point_key=publication.recovery_point_key,
        parent_dataset_id=source.point.dataset_id,
        parent_generation_id=source.point.release_id,
        parent_manifest_sha256=source.point.manifest_digest,
        uploaded_objects=uploaded_objects,
        uploaded_bytes=uploaded_bytes,
        reused_objects=reused_objects,
        reused_bytes=reused_bytes,
    )
