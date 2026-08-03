"""Queued legacy object-store import into an owned DataOps v3 lineage."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DataConnection, DataOperation, RecoveryPoint, RestoreCandidate
from .v3_candidate import prepare_recovery_candidate
from .v3_config import connection_from_model
from .v3_executor import V3ExecutionError, validate_operation_plan
from .v3_legacy import (
    import_legacy_generation,
    load_legacy_generation,
    materialize_legacy_generation,
)
from .v3_restore import (
    load_verified_recovery_point,
    materialize_quarantine,
    rehearse_quarantine,
)
from .v3_signing import V3SigningError, manifest_signing_material
from .v3_storage import client_for_connection


def _source_connection(operation: DataOperation) -> DataConnection:
    raw = str((operation.checkpoint or {}).get("source_connection_id") or "")
    try:
        connection = DataConnection.objects.using("control").get(public_id=raw)
    except (DataConnection.DoesNotExist, ValueError) as exc:
        raise V3ExecutionError(
            "source_connection_not_found",
            stage="preflight",
        ) from exc
    if not connection.enabled or not (connection.capabilities or {}).get("read"):
        raise V3ExecutionError(
            "source_connection_not_readable",
            stage="preflight",
        )
    return connection


def _project_point(
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
                    "legacy_import_receipt": receipt.as_dict(),
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


def execute_legacy_import_operation(
    operation: DataOperation,
    *,
    lease=None,
    client_factory=client_for_connection,
    migration_runner=None,
    candidate_preparer=prepare_recovery_candidate,
) -> dict:
    validate_operation_plan(operation)
    if operation.kind != DataOperation.Kind.IMPORT:
        raise V3ExecutionError("operation_kind_invalid", stage="preflight")
    if operation.lifecycle_route != "legacy_import":
        raise V3ExecutionError("lifecycle_route_invalid", stage="preflight")
    if operation.lifecycle_plan.get("activation") != "none":
        raise V3ExecutionError(
            "signed_activation_executor_not_available",
            stage="preflight",
        )
    if operation.connection_id is None:
        raise V3ExecutionError("owned_connection_missing", stage="preflight")
    source_model = _source_connection(operation)
    source = connection_from_model(source_model)
    destination = connection_from_model(operation.connection)
    checkpoint = operation.checkpoint or {}
    source_dataset = str(checkpoint.get("source_dataset_id") or "")
    source_generation = str(checkpoint.get("source_generation_id") or "")
    if (
        source_dataset != operation.lifecycle_plan.get("source_dataset_id")
        or source_generation != operation.lifecycle_plan.get("source_generation_id")
    ):
        raise V3ExecutionError("legacy_source_plan_mismatch", stage="preflight")
    try:
        signing_key, signing_key_id = manifest_signing_material()
    except V3SigningError as exc:
        raise V3ExecutionError(exc.code, stage="preflight") from exc
    try:
        source_client = client_factory(source)
        destination_client = client_factory(destination)
        generation = load_legacy_generation(
            source_client,
            bucket=source.bucket,
            dataset_id=source_dataset,
            generation_id=source_generation,
        )
        if generation.manifest_sha256 != operation.lifecycle_plan.get(
            "source_manifest_sha256"
        ):
            raise V3ExecutionError(
                "legacy_manifest_plan_mismatch",
                stage="preflight",
            )
        if lease:
            lease()
        data_root = Path(getattr(settings, "DATA_ROOT", "/tmp"))
        materialized = materialize_legacy_generation(
            generation,
            client=source_client,
            quarantine_root=data_root / "legacy-import-quarantine",
        )
        if lease:
            lease()
        manifest, import_receipt = import_legacy_generation(
            generation=generation,
            materialization=materialized,
            destination=destination,
            destination_client=destination_client,
            recovery_point_id=(
                operation.release_id
                or f"import-{source_generation}"[:160]
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
        point = _project_point(
            operation,
            manifest=manifest,
            receipt=import_receipt,
            signing_key_id=signing_key_id,
        )
        verified = load_verified_recovery_point(
            point,
            signing_key=signing_key,
            client_factory=lambda _connection: destination_client,
        )
        restored = materialize_quarantine(
            verified,
            quarantine_root=data_root / "restore-quarantine",
        )
    except V3ExecutionError:
        raise
    except Exception as exc:
        raise V3ExecutionError(
            getattr(exc, "code", "legacy_import_failed"),
            stage="legacy_import",
            retryable=bool(getattr(exc, "retryable", False)),
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
        ) from exc
    derived_rebuild_required = any(
        component.get("rebuild_required") is True
        for component in manifest["components"].values()
    )
    observed_ratio = float(restored.get("evidence", {}).get("indexing_ratio", 0.0))
    effective_ratio = 0.0 if derived_rebuild_required else observed_ratio
    candidate_preparation = {}
    if derived_rebuild_required or effective_ratio < 1.0:
        try:
            candidate_preparation = candidate_preparer(restored)
        except Exception as exc:
            raise V3ExecutionError(
                getattr(exc, "code", "candidate_preparation_failed"),
                stage="candidate_preparation",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        effective_ratio = float(candidate_preparation.get("indexing_ratio", 0.0))
    candidate_ready = effective_ratio == 1.0
    candidate, created = RestoreCandidate.objects.using("control").get_or_create(
        operation=operation,
        defaults={
            "recovery_point": point,
            "environment": str(operation.lifecycle_plan.get("environment") or ""),
            "workspace": str(restored["workspace"]),
            "state": (
                RestoreCandidate.State.READY
                if candidate_ready
                else RestoreCandidate.State.VERIFIED
            ),
            "manifest_digest": point.manifest_digest,
            "evidence": {
                "legacy_materialization": {
                    key: value
                    for key, value in materialized.items()
                    if key != "workspace"
                },
                "legacy_import_receipt": import_receipt.as_dict(),
                "restore": {
                    key: value for key, value in restored.items() if key != "workspace"
                },
                "rehearsal": rehearsal,
                "observed_indexing_ratio": observed_ratio,
                "indexing_ratio": effective_ratio,
                "requires_reindex": not candidate_ready,
                "candidate_preparation": {
                    key: value
                    for key, value in candidate_preparation.items()
                    if key != "workspace"
                },
            },
        },
    )
    if not created and (
        candidate.manifest_digest != point.manifest_digest
        or Path(candidate.workspace).resolve()
        != Path(restored["workspace"]).resolve()
    ):
        raise V3ExecutionError(
            "restore_candidate_conflict",
            stage="publish_receipt",
        )
    return {
        "contract_version": 3,
        "status": "ready_for_activation" if candidate_ready else "verified_rehearsal",
        "route": "legacy_import",
        "effective_recovery_point_id": str(point.public_id),
        "candidate_id": candidate.pk,
        "manifest_digest": point.manifest_digest,
        "workspace": str(restored["workspace"]),
        "indexing_ratio": effective_ratio,
        "requires_reindex": not candidate_ready,
        "activation_performed": False,
        "legacy_import_receipt": import_receipt.as_dict(),
        "plan_digest": operation.lifecycle_plan_digest,
    }
