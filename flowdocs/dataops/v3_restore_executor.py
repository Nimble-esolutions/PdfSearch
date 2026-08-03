"""Queued same-dataset restore, foreign import, and isolated rehearsal."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DataOperation, RecoveryPoint, RestoreCandidate
from .v3_activation import schedule_candidate_activation
from .v3_candidate import prepare_recovery_candidate
from .v3_config import connection_from_model
from .v3_executor import V3ExecutionError, validate_operation_plan
from .v3_import import import_rebind_recovery_point
from .v3_restore import (
    load_verified_recovery_point,
    materialize_quarantine,
    rehearse_quarantine,
)
from .v3_storage import client_for_connection
from .v3_signing import V3SigningError, manifest_signing_material


_RUNTIME_COMPONENT_PATHS = {
    "database": ("db.sqlite3", "file"),
    "media": ("media", "directory"),
    "pdf_cache": ("pdf_cache", "directory"),
    "faiss": ("faiss_indexes", "directory"),
    "chroma": ("chroma_db", "directory"),
}
_DERIVED_COMPONENTS = frozenset({"pdf_cache", "faiss", "chroma"})


def _path_ready(workspace: Path, relative: str, expected_kind: str) -> bool:
    path = workspace / relative
    if path.is_symlink():
        return False
    if expected_kind == "file":
        return path.is_file()
    return path.is_dir()


def _component_rebuild_requirements(
    manifest: Mapping,
    *,
    workspace: Path,
) -> dict[str, list[str]]:
    """Bind signed component state to the materialized runtime paths."""

    components = manifest.get("components")
    if not isinstance(components, Mapping):
        raise V3ExecutionError("manifest_components_invalid", stage="preflight")
    requirements: dict[str, list[str]] = {}
    for name, (relative, expected_kind) in _RUNTIME_COMPONENT_PATHS.items():
        component = components.get(name)
        reasons: list[str] = []
        if not isinstance(component, Mapping):
            if name in _DERIVED_COMPONENTS:
                reasons.append("component_not_declared")
            else:
                raise V3ExecutionError(
                    f"candidate_{name}_component_missing",
                    stage="candidate_preparation",
                )
        else:
            if component.get("complete") is not True:
                reasons.append("signed_component_incomplete")
            if component.get("coherent") is False:
                reasons.append("signed_component_incoherent")
            if component.get("rebuild_required") is True:
                reasons.append("signed_rebuild_required")
        if not _path_ready(workspace, relative, expected_kind):
            reasons.append("runtime_path_missing_or_unsafe")
        if reasons:
            requirements[name] = sorted(set(reasons))
    return requirements


def _component_reconciliation(
    manifest: Mapping,
    *,
    workspace: Path,
    requirements: Mapping[str, list[str]],
    preparation: Mapping,
) -> dict[str, dict]:
    """Prove every runtime component is ready after any required rebuild."""

    components = manifest.get("components")
    validation = preparation.get("validation")
    preparation_succeeded = preparation.get("success") is True
    rebuilt_components = preparation.get("rebuilt_components")
    if not isinstance(rebuilt_components, list):
        rebuilt_components = []
    reconciliation: dict[str, dict] = {}
    for name, (relative, expected_kind) in _RUNTIME_COMPONENT_PATHS.items():
        required_rebuild = name in requirements
        ready = _path_ready(workspace, relative, expected_kind)
        if required_rebuild:
            ready = bool(
                ready
                and name in _DERIVED_COMPONENTS
                and preparation_succeeded
                and name in rebuilt_components
            )
        if required_rebuild and name == "faiss":
            ready = bool(
                ready
                and isinstance(validation, Mapping)
                and isinstance(validation.get("faiss"), Mapping)
                and isinstance(validation.get("validated_folder_ids"), list)
            )
        if required_rebuild and name == "chroma":
            chroma = validation.get("chroma") if isinstance(validation, Mapping) else None
            ready = bool(
                ready
                and isinstance(chroma, Mapping)
                and chroma.get("ready") is True
                and isinstance(chroma.get("file_count"), int)
                and not isinstance(chroma.get("file_count"), bool)
                and chroma.get("file_count") >= 0
                and len(str(chroma.get("sha256") or "")) == 64
            )
        reconciliation[name] = {
            "ready": ready,
            "path": relative,
            "source": (
                "candidate_preparation"
                if required_rebuild
                else "signed_manifest_and_restore"
            ),
            "rebuild_reasons": list(requirements.get(name, [])),
            "signed": dict(components.get(name) or {}),
        }
        if not ready:
            raise V3ExecutionError(
                f"candidate_{name}_unresolved",
                stage="candidate_preparation",
            )
    return reconciliation


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
    candidate_preparer=prepare_recovery_candidate,
    activation_scheduler=schedule_candidate_activation,
) -> dict:
    validate_operation_plan(operation)
    if operation.kind not in {
        DataOperation.Kind.RESTORE,
        DataOperation.Kind.TEST_RECOVERY,
    }:
        raise V3ExecutionError("operation_kind_invalid", stage="preflight")
    activation_mode = str(operation.lifecycle_plan.get("activation") or "none")
    if activation_mode not in {"none", "signed_atomic"}:
        raise V3ExecutionError("activation_mode_invalid", stage="preflight")
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
    environment = str(
        operation.lifecycle_plan.get("environment") or ""
    ).strip().lower()
    if activation_mode == "signed_atomic" and environment == "production":
        raise V3ExecutionError(
            "production_restore_gates_unimplemented",
            stage="preflight",
        )
    try:
        signing_key, signing_key_id = manifest_signing_material()
    except V3SigningError as exc:
        raise V3ExecutionError(exc.code, stage="preflight") from exc
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
    effective_manifest = source.manifest
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
            effective_manifest = imported.manifest
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
    workspace = Path(str(restored["workspace"])).resolve()
    component_requirements = _component_rebuild_requirements(
        effective_manifest,
        workspace=workspace,
    )
    if indexing_ratio < 1.0:
        component_requirements.setdefault("faiss", []).append(
            "indexing_incomplete"
        )
        component_requirements["faiss"] = sorted(
            set(component_requirements["faiss"])
        )
    candidate_preparation = {}
    preparation_required = route != "isolated_rehearsal" and (
        indexing_ratio < 1.0 or component_requirements
    )
    if preparation_required:
        try:
            candidate_preparation = candidate_preparer(
                {
                    **restored,
                    "component_rebuild_requirements": component_requirements,
                }
            )
        except Exception as exc:
            raise V3ExecutionError(
                getattr(exc, "code", "candidate_preparation_failed"),
                stage="candidate_preparation",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        if (
            not isinstance(candidate_preparation, Mapping)
            or candidate_preparation.get("success") is not True
        ):
            raise V3ExecutionError(
                "candidate_preparation_unverified",
                stage="candidate_preparation",
            )
        indexing_ratio = float(candidate_preparation.get("indexing_ratio", 0.0))
    component_reconciliation = {}
    if route != "isolated_rehearsal":
        component_reconciliation = _component_reconciliation(
            effective_manifest,
            workspace=workspace,
            requirements=component_requirements,
            preparation=candidate_preparation,
        )
    candidate_ready = route != "isolated_rehearsal" and indexing_ratio == 1.0
    candidate, created = RestoreCandidate.objects.using("control").get_or_create(
        operation=operation,
        defaults={
            "recovery_point": effective_point,
            "environment": str(operation.lifecycle_plan.get("environment") or ""),
            "workspace": str(restored["workspace"]),
            "state": (
                RestoreCandidate.State.READY
                if candidate_ready
                else RestoreCandidate.State.VERIFIED
            ),
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
                "component_rebuild_requirements": component_requirements,
                "component_reconciliation": component_reconciliation,
                "candidate_preparation": {
                    key: value
                    for key, value in candidate_preparation.items()
                    if key != "workspace"
                },
                "import_receipt": (
                    import_receipt.as_dict() if import_receipt else None
                ),
            },
        },
    )
    if not created and (
        candidate.recovery_point_id != effective_point.pk
        or candidate.manifest_digest != effective_point.manifest_digest
        or Path(candidate.workspace).resolve()
        != Path(restored["workspace"]).resolve()
        or candidate.state
        != (
            RestoreCandidate.State.READY
            if candidate_ready
            else RestoreCandidate.State.VERIFIED
        )
        or candidate.evidence.get("indexing_ratio") != indexing_ratio
        or candidate.evidence.get("component_reconciliation")
        != component_reconciliation
    ):
        raise V3ExecutionError(
            "restore_candidate_conflict",
            stage="publish_receipt",
        )
    activation = None
    if activation_mode == "signed_atomic":
        if not candidate_ready:
            raise V3ExecutionError(
                "candidate_not_activation_ready",
                stage="activation",
            )
        try:
            activation = activation_scheduler(
                candidate,
                operation=operation,
                confirmed=True,
            )
        except Exception as exc:
            raise V3ExecutionError(
                getattr(exc, "code", "activation_scheduling_failed"),
                stage="activation",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
    return {
        "contract_version": 3,
        "status": (
            "ready_for_activation"
            if candidate_ready
            else "verified_rehearsal"
        ),
        "route": route,
        "source_recovery_point_id": str(point.public_id),
        "effective_recovery_point_id": str(effective_point.public_id),
        "candidate_id": candidate.pk,
        "manifest_digest": effective_point.manifest_digest,
        "workspace": str(restored["workspace"]),
        "indexing_ratio": indexing_ratio,
        "requires_reindex": indexing_ratio < 1.0,
        "component_reconciliation": component_reconciliation,
        "activation_performed": False,
        "activation": activation,
        "plan_digest": operation.lifecycle_plan_digest,
    }
