"""Queued same-dataset restore, foreign import, and isolated rehearsal."""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DataOperation, RecoveryPoint, RestoreCandidate
from .v3_config import connection_from_model
from .v3_executor import V3ExecutionError, validate_operation_plan
from .v3_import import import_rebind_recovery_point
from .v3_restore import (
    load_verified_recovery_point,
    materialize_quarantine,
    rehearse_quarantine,
)
from .v3_storage import client_for_connection


def _signing_material() -> tuple[bytes, str]:
    value = str(getattr(settings, "ACTIVATION_INTENT_SIGNING_KEY", "") or "")
    if not value:
        raise V3ExecutionError(
            "manifest_signing_key_missing",
            stage="preflight",
        )
    key = value.encode("utf-8")
    return key, "dataops-manifest-" + hashlib.sha256(key).hexdigest()[:12]


def _source_point(operation: DataOperation) -> RecoveryPoint:
    point_id = str((operation.checkpoint or {}).get("recovery_point_id") or "")
    try:
        point = RecoveryPoint.objects.using("control").get(public_id=point_id)
    except (RecoveryPoint.DoesNotExist, ValueError) as exc:
        raise V3ExecutionError(
            "recovery_point_not_found",
            stage="preflight",
        ) from exc
    plan = operation.lifecycle_plan or {}
    if (
        point.dataset_id != plan.get("source_dataset_id")
        or point.release_id != plan.get("source_generation_id")
        or point.manifest_digest != plan.get("source_manifest_sha256")
    ):
        raise V3ExecutionError(
            "recovery_point_plan_mismatch",
            stage="preflight",
        )
    return point


def _project_imported_point(
    operation: DataOperation,
    *,
    manifest,
    receipt,
    signing_key_id: str,
) -> RecoveryPoint:
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
                "activation_ready": False,
                "state": RecoveryPoint.State.VERIFIED,
                "counts": dict(manifest["counts"]),
                "identity": {
                    "release": manifest["application"]["release_version"],
                    "image_digest": manifest["application"]["image_digest"],
                    "database_schema": manifest["application"]["database_schema"],
                    "parent_dataset_id": receipt.parent_dataset_id,
                    "parent_generation_id": receipt.parent_generation_id,
                    "parent_manifest_sha256": receipt.parent_manifest_sha256,
                },
                "evidence": {
                    "signature_valid": True,
                    "import_receipt": receipt.as_dict(),
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
    return point


def execute_restore_operation(
    operation: DataOperation,
    *,
    lease=None,
    client_factory=client_for_connection,
    migration_runner=None,
) -> dict:
    validate_operation_plan(operation)
    if operation.kind not in {
        DataOperation.Kind.RESTORE,
        DataOperation.Kind.TEST_RECOVERY,
    }:
        raise V3ExecutionError("operation_kind_invalid", stage="preflight")
    if operation.lifecycle_plan.get("activation") != "none":
        raise V3ExecutionError(
            "signed_activation_executor_not_available",
            stage="preflight",
        )
    route = str(operation.lifecycle_route or "")
    if route not in {
        "same_dataset_restore",
        "import_rebind_restore",
        "isolated_rehearsal",
        "noop",
    }:
        raise V3ExecutionError("lifecycle_route_invalid", stage="preflight")
    if route == "noop":
        return {
            "contract_version": 3,
            "status": "already_active",
            "plan_digest": operation.lifecycle_plan_digest,
        }
    signing_key, signing_key_id = _signing_material()
    point = _source_point(operation)
    if lease:
        lease()
    try:
        source = load_verified_recovery_point(
            point,
            signing_key=signing_key,
            client_factory=client_factory,
        )
        configured_quarantine = str(
            getattr(settings, "DATAOPS_RESTORE_STAGING_ROOT", "") or ""
        ).strip()
        quarantine_root = (
            Path(configured_quarantine)
            if configured_quarantine
            else Path(settings.DATA_ROOT) / "restore-quarantine"
        )
        restored = materialize_quarantine(
            source,
            quarantine_root=quarantine_root,
        )
    except Exception as exc:
        raise V3ExecutionError(
            getattr(exc, "code", "restore_materialization_failed"),
            stage="restore",
            retryable=bool(getattr(exc, "retryable", False)),
        ) from exc
    effective_point = point
    import_receipt = None
    if route == "import_rebind_restore":
        if lease:
            lease()
        try:
            destination = connection_from_model(operation.connection)
            destination_client = client_factory(destination)
            imported_manifest, import_receipt = import_rebind_recovery_point(
                source=source,
                quarantine_receipt=restored,
                destination=destination,
                destination_client=destination_client,
                recovery_point_id=(
                    operation.release_id
                    or f"import-{point.release_id}"[:160]
                ),
                destination_instance_id=str(
                    operation.lifecycle_plan.get("deployment_id") or ""
                ),
                destination_environment=str(
                    operation.lifecycle_plan.get("environment") or ""
                ),
                signing_key=signing_key,
                signing_key_id=signing_key_id,
            )
            effective_point = _project_imported_point(
                operation,
                manifest=imported_manifest,
                receipt=import_receipt,
                signing_key_id=signing_key_id,
            )
            imported = load_verified_recovery_point(
                effective_point,
                signing_key=signing_key,
                client_factory=lambda _connection: destination_client,
            )
            restored = materialize_quarantine(
                imported,
                quarantine_root=quarantine_root,
            )
        except Exception as exc:
            if isinstance(exc, V3ExecutionError):
                raise
            raise V3ExecutionError(
                getattr(exc, "code", "import_rebind_failed"),
                stage="import_rebind",
                retryable=False,
            ) from exc
    if lease:
        lease()
    try:
        rehearsal = (
            rehearse_quarantine(restored)
            if migration_runner is None
            else rehearse_quarantine(
                restored,
                migration_runner=migration_runner,
            )
        )
    except Exception as exc:
        raise V3ExecutionError(
            getattr(exc, "code", "migration_rehearsal_failed"),
            stage="migration_rehearsal",
            retryable=False,
        ) from exc
    indexing_ratio = float(
        restored.get("evidence", {}).get("indexing_ratio", 0.0)
    )
    candidate, created = RestoreCandidate.objects.using("control").get_or_create(
        operation=operation,
        defaults={
            "recovery_point": effective_point,
            "environment": str(operation.lifecycle_plan.get("environment") or ""),
            "workspace": str(restored["workspace"]),
            "state": RestoreCandidate.State.VERIFIED,
            "manifest_digest": effective_point.manifest_digest,
            "evidence": {
                "restore": {
                    key: value
                    for key, value in restored.items()
                    if key != "workspace"
                },
                "rehearsal": rehearsal,
                "indexing_ratio": indexing_ratio,
                "requires_reindex": indexing_ratio < 1.0,
                "import_receipt": (
                    import_receipt.as_dict() if import_receipt else None
                ),
            },
        },
    )
    if not created and (
        candidate.manifest_digest != effective_point.manifest_digest
        or Path(candidate.workspace).resolve()
        != Path(restored["workspace"]).resolve()
    ):
        raise V3ExecutionError(
            "restore_candidate_conflict",
            stage="publish_receipt",
        )
    return {
        "contract_version": 3,
        "status": "verified_rehearsal",
        "route": route,
        "source_recovery_point_id": str(point.public_id),
        "effective_recovery_point_id": str(effective_point.public_id),
        "candidate_id": candidate.pk,
        "manifest_digest": effective_point.manifest_digest,
        "workspace": str(restored["workspace"]),
        "indexing_ratio": indexing_ratio,
        "requires_reindex": indexing_ratio < 1.0,
        "activation_performed": False,
        "plan_digest": operation.lifecycle_plan_digest,
    }
