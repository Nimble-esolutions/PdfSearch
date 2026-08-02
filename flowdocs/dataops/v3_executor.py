"""Resumable DataOps v3 operation executors.

VaultOps is not an operator surface here.  The snapshot adapter temporarily
invokes its proven consistency primitive while the primitive is extracted;
the DataOps operation, plan, recovery point, and receipt remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DataOperation, RecoveryPoint
from .v3_backup import publish_snapshot
from .v3_config import connection_from_model
from .v3_storage import client_for_connection


class V3ExecutionError(RuntimeError):
    def __init__(self, code: str, *, stage: str, retryable: bool = False):
        self.code = code
        self.stage = stage
        self.retryable = retryable
        super().__init__(code)


def _plan_digest(plan: dict) -> str:
    unsigned = dict(plan)
    unsigned.pop("plan_digest", None)
    return hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def validate_operation_plan(operation: DataOperation) -> None:
    plan = dict(operation.lifecycle_plan or {})
    digest = str(operation.lifecycle_plan_digest or "")
    if plan.get("contract_version") != 3:
        raise V3ExecutionError("lifecycle_plan_version_invalid", stage="preflight")
    if plan.get("plan_digest") != digest or _plan_digest(plan) != digest:
        raise V3ExecutionError("lifecycle_plan_digest_mismatch", stage="preflight")
    if plan.get("route") != operation.lifecycle_route:
        raise V3ExecutionError("lifecycle_plan_route_mismatch", stage="preflight")
    if operation.connection_id is None:
        raise V3ExecutionError("owned_connection_missing", stage="preflight")
    if plan.get("target_dataset_id") != operation.connection.dataset_id:
        raise V3ExecutionError("owned_connection_dataset_mismatch", stage="preflight")


def _source_roots() -> dict[str, Path]:
    configured = {
        "media": Path(settings.MEDIA_ROOT),
        "pdf_cache": Path(settings.PDF_CACHE_DIR),
        "faiss_indexes": Path(settings.FAISS_INDEX_DIR),
        "chroma_db": Path(settings.CHROMA_DIR),
    }
    return {
        category: path.resolve()
        for category, path in configured.items()
        if path.exists() and path.is_dir()
    }


def create_snapshot_for_operation(operation: DataOperation, *, lease=None):
    """Adapter to the current proven snapshot primitive; no Vault UI/profile."""

    from vaultops.models import SourceSnapshot, VaultJob
    from vaultops.services.snapshot import create_consistent_snapshot

    idempotency_key = f"dataops-v3:{operation.public_id}:attempt:{operation.attempt}"
    job, _ = VaultJob.objects.using("control").get_or_create(
        operation="dataops_v3_snapshot",
        idempotency_key=idempotency_key,
        defaults={
            "status": VaultJob.Status.RUNNING,
            "phase": "snapshot",
            "dataset_id": operation.connection.dataset_id,
            "requested_by_id": None,
            "requested_by_name": "dataops-v3",
        },
    )
    finalized = (
        SourceSnapshot.objects.using("control")
        .filter(job=job, state=SourceSnapshot.State.FINALIZED)
        .first()
    )
    if finalized:
        return finalized

    def progress_callback(**_kwargs):
        if lease:
            lease()

    snapshot = create_consistent_snapshot(
        job,
        progress_callback=progress_callback,
        source_roots=_source_roots(),
        database_path=Path(settings.DATABASES["default"]["NAME"]),
        snapshot_root=Path(settings.DATA_CONTROL_ROOT) / "v3-snapshots",
    )
    job.status = VaultJob.Status.SUCCEEDED
    job.phase = "snapshot_complete"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "phase", "finished_at", "updated_at"])
    return snapshot


def execute_backup_operation(
    operation: DataOperation,
    *,
    lease=None,
    snapshot_factory=create_snapshot_for_operation,
    client_factory=client_for_connection,
) -> dict:
    validate_operation_plan(operation)
    if operation.kind != DataOperation.Kind.BACKUP:
        raise V3ExecutionError("operation_kind_invalid", stage="preflight")
    connection = connection_from_model(operation.connection)
    signing_key_text = str(
        getattr(settings, "ACTIVATION_INTENT_SIGNING_KEY", "") or ""
    )
    if not signing_key_text:
        raise V3ExecutionError("manifest_signing_key_missing", stage="preflight")
    signing_key = signing_key_text.encode("utf-8")
    signing_key_id = "dataops-manifest-" + hashlib.sha256(signing_key).hexdigest()[:12]
    identity = getattr(settings, "ENV_IDENTITY", None)
    image_digest = str(
        getattr(identity, "app_image_digest", "")
        or getattr(settings, "APP_IMAGE_DIGEST", "")
        or ""
    )
    release_version = str(
        getattr(identity, "app_release_version", "")
        or getattr(settings, "APP_RELEASE_VERSION", "")
        or ""
    )
    if not image_digest or not release_version:
        raise V3ExecutionError("application_release_identity_missing", stage="preflight")
    if lease:
        lease()
    try:
        snapshot = snapshot_factory(operation, lease=lease)
    except V3ExecutionError:
        raise
    except Exception as exc:
        raise V3ExecutionError(
            getattr(exc, "reason_code", "consistent_snapshot_unproven"),
            stage="snapshot",
            retryable=True,
        ) from exc
    if lease:
        lease()
    try:
        client = client_factory(connection)
        recovery_point_id = operation.release_id or f"rp-{operation.public_id}"
        manifest, receipt = publish_snapshot(
            snapshot=snapshot,
            connection=connection,
            client=client,
            recovery_point_id=recovery_point_id,
            source_instance_id=str(
                getattr(identity, "deployment_id", "")
                or operation.lifecycle_plan.get("deployment_id", "")
            ),
            source_environment=str(operation.lifecycle_plan.get("environment", "")),
            image_digest=image_digest,
            release_version=release_version,
            signing_key=signing_key,
            signing_key_id=signing_key_id,
        )
    except Exception as exc:
        raise V3ExecutionError(
            getattr(exc, "code", "recovery_point_publication_failed"),
            stage="publication",
            retryable=True,
        ) from exc
    if lease:
        lease()
    with transaction.atomic(using="control"):
        point, created = RecoveryPoint.objects.using("control").get_or_create(
            connection=operation.connection,
            dataset_id=receipt.dataset_id,
            release_id=receipt.recovery_point_id,
            defaults={
                "profile_key": "",
                "format_version": 3,
                "prefix": receipt.recovery_point_key,
                "manifest_digest": receipt.manifest_sha256,
                "signature_key_id": signing_key_id,
                "data_complete": True,
                "activation_ready": bool(
                    all(
                        not component.get("rebuild_required", False)
                        for component in manifest["components"].values()
                    )
                ),
                "state": RecoveryPoint.State.VERIFIED,
                "counts": dict(manifest["counts"]),
                "identity": {
                    "release": release_version,
                    "image_digest": image_digest,
                    "database_schema": manifest["application"]["database_schema"],
                },
                "evidence": {
                    "signature_valid": True,
                    "backup_receipt": receipt.as_dict(),
                    "plan_digest": operation.lifecycle_plan_digest,
                },
                "observed_at": timezone.now(),
            },
        )
        if not created and point.manifest_digest != receipt.manifest_sha256:
            raise V3ExecutionError(
                "recovery_point_projection_conflict",
                stage="publish_receipt",
            )
    return {
        "contract_version": 3,
        "release_id": receipt.recovery_point_id,
        "manifest_digest": receipt.manifest_sha256,
        "recovery_point_id": str(point.public_id),
        "connection_id": str(operation.connection.public_id),
        "transfer": {
            "uploaded_objects": receipt.uploaded_objects,
            "uploaded_bytes": receipt.uploaded_bytes,
            "reused_objects": receipt.reused_objects,
            "reused_bytes": receipt.reused_bytes,
            "total_objects": receipt.total_objects,
            "total_bytes": receipt.total_bytes,
        },
        "plan_digest": operation.lifecycle_plan_digest,
    }
