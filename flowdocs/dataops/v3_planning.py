"""Django adapters that feed trusted records into the pure v3 planner."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from django.conf import settings
from django.db import transaction

from .lifecycle import (
    ArtifactPassport,
    ArtifactTrust,
    InstanceIdentity,
    LifecycleCapabilities,
    LifecycleIntent,
    LifecycleRequest,
    SourceKind,
    compile_lifecycle_plan,
)
from .models import DataConnection, DataPolicy, RecoveryPoint
from .v3_config import (
    ConnectionView,
    RuntimeConfig,
    bootstrap_connection_from_environment,
    compile_runtime_config,
    connection_from_model,
    default_policy,
)


def _environment_name() -> str:
    identity = getattr(settings, "ENV_IDENTITY", None)
    app_env = getattr(identity, "app_env", None)
    return str(
        getattr(app_env, "value", app_env)
        or getattr(settings, "APP_ENV", "")
        or os.getenv("APP_ENV", "")
    )


def instance_identity() -> InstanceIdentity:
    identity = getattr(settings, "ENV_IDENTITY", None)
    return InstanceIdentity(
        environment=_environment_name(),
        deployment_id=str(
            getattr(identity, "deployment_id", "")
            or getattr(settings, "DEPLOYMENT_ID", "")
            or os.getenv("DEPLOYMENT_ID", "")
        ),
        dataset_id=str(
            getattr(identity, "dataset_id", "")
            or getattr(settings, "DATASET_ID", "")
            or os.getenv("DATASET_ID", "")
        ),
        active_generation_id=str(
            getattr(settings, "RUNTIME_GENERATION_ID", "") or ""
        ),
        active_manifest_sha256=str(
            getattr(settings, "RUNTIME_MANIFEST_DIGEST", "") or ""
        ),
    )


def _stored_primary_connection(dataset_id: str):
    return (
        DataConnection.objects.using("control")
        .filter(dataset_id=dataset_id, is_primary=True, enabled=True)
        .first()
    )


def runtime_config() -> RuntimeConfig:
    identity = instance_identity()
    stored = _stored_primary_connection(identity.dataset_id)
    connection = (
        connection_from_model(stored)
        if stored
        else bootstrap_connection_from_environment(dataset_id=identity.dataset_id)
    )
    policy_row = (
        DataPolicy.objects.using("control")
        .filter(deployment_id=identity.deployment_id)
        .first()
    )
    policy = (
        dict(policy_row.values or {})
        if policy_row
        else default_policy(identity.normalized_environment)
    )
    return compile_runtime_config(
        environment=identity.environment,
        deployment_id=identity.deployment_id,
        dataset_id=identity.dataset_id,
        enabled=bool(getattr(settings, "DATAOPS_ENABLED", False)),
        connection=connection,
        policy=policy,
    )


def materialize_primary_connection(config: RuntimeConfig):
    """Persist only non-secret bootstrap metadata on explicit operator action."""

    if config.connection is None:
        raise ValueError("owned_connection_missing")
    if config.connection.source == "control_database":
        return DataConnection.objects.using("control").get(
            public_id=config.connection.public_id
        )
    with transaction.atomic(using="control"):
        existing = (
            DataConnection.objects.using("control")
            .select_for_update()
            .filter(dataset_id=config.dataset_id, is_primary=True)
            .first()
        )
        if existing:
            if (
                existing.endpoint != config.connection.endpoint
                or existing.bucket != config.connection.bucket
                or existing.prefix != config.connection.prefix
            ):
                raise ValueError("owned_connection_bootstrap_conflict")
            return existing
        return DataConnection.objects.using("control").create(
            name=config.connection.name,
            provider=config.connection.provider,
            endpoint=config.connection.endpoint,
            bucket=config.connection.bucket,
            region=config.connection.region,
            prefix=config.connection.prefix,
            dataset_id=config.dataset_id,
            credential_ref=config.connection.credential_ref,
            enabled=True,
            is_primary=True,
            capabilities=dict(config.connection.capabilities),
            observation={
                "source": "environment_bootstrap",
                "status": "check_required",
                "failure_codes": [],
            },
        )


def passport_from_recovery_point(point: RecoveryPoint) -> ArtifactPassport:
    identity = dict(point.identity or {})
    evidence = dict(point.evidence or {})
    signature_valid: bool | None
    if point.format_version >= 3:
        signature_valid = bool(
            point.signature_key_id
            and evidence.get("signature_valid") is True
        )
    else:
        signature_valid = None
    return ArtifactPassport(
        source_kind=SourceKind.RECOVERY_POINT,
        dataset_id=point.dataset_id,
        generation_id=point.release_id,
        manifest_sha256=point.manifest_digest,
        format_version=point.format_version,
        trust=(
            ArtifactTrust.VERIFIED
            if point.state == RecoveryPoint.State.VERIFIED
            else ArtifactTrust.PROVISIONAL
        ),
        complete=(
            point.data_complete
            if point.format_version >= 3
            else point.state == RecoveryPoint.State.VERIFIED
        ),
        read_only=True,
        signature_valid=signature_valid,
        producer_release=str(identity.get("release") or identity.get("release_id") or ""),
        producer_image_digest=str(identity.get("image_digest") or identity.get("image") or ""),
        database_schema=str(identity.get("database_schema") or identity.get("schema") or ""),
        parent_dataset_id=str(identity.get("parent_dataset_id") or evidence.get("parent_dataset_id") or ""),
        parent_generation_id=str(identity.get("parent_generation_id") or evidence.get("parent_generation_id") or ""),
        parent_manifest_sha256=str(identity.get("parent_manifest_sha256") or evidence.get("parent_manifest_sha256") or ""),
    )


def legacy_mount_passport() -> ArtifactPassport:
    return ArtifactPassport(
        source_kind=SourceKind.LEGACY_MOUNT,
        trust=ArtifactTrust.UNKNOWN,
        complete=False,
        read_only=True,
    )


def _path_writable(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.exists() and candidate.is_dir() and os.access(candidate, os.W_OK)


def _connection_capability(
    connection: ConnectionView | None,
    name: str,
) -> bool:
    return bool(connection and connection.capabilities.get(name, False))


def capabilities_for(
    config: RuntimeConfig,
    *,
    point: RecoveryPoint | None = None,
    legacy_mount: bool = False,
) -> LifecycleCapabilities:
    source_connection = None
    if point and point.connection_id:
        source_connection = connection_from_model(point.connection)
    elif point and point.dataset_id == config.dataset_id:
        source_connection = config.connection
    data_root = Path(getattr(settings, "DATA_ROOT", Path("/nonexistent")))
    control_root = Path(
        getattr(settings, "DATA_CONTROL_ROOT", Path("/nonexistent"))
    )
    legacy_root = Path(
        getattr(settings, "LEGACY_DATA_ROOT", "")
        or os.getenv("LEGACY_DATA_ROOT", "/nonexistent")
    )
    return LifecycleCapabilities(
        owned_store_readable=_connection_capability(config.connection, "read"),
        owned_store_writable=(
            _connection_capability(config.connection, "write")
            and _connection_capability(config.connection, "conditional_write")
        ),
        source_readable=(
            legacy_root.exists() and os.access(legacy_root, os.R_OK)
            if legacy_mount
            else _connection_capability(source_connection, "read")
        ),
        quarantine_writable=_path_writable(data_root / "restore-quarantine"),
        signing_available=bool(
            str(getattr(settings, "ACTIVATION_INTENT_SIGNING_KEY", "") or "")
        ),
        activation_available=_path_writable(control_root) and _path_writable(data_root),
        isolated_restore_available=_path_writable(control_root) and _path_writable(data_root),
        previous_runtime_available=bool(
            str(getattr(settings, "PREVIOUS_RUNTIME_GENERATION_ID", "") or "")
        ),
    )


def compile_requested_plan(
    *,
    action: str,
    activate: bool,
    confirmation_present: bool,
    point: RecoveryPoint | None = None,
    source_kind: str = "",
) -> tuple[RuntimeConfig, Any]:
    try:
        intent = LifecycleIntent(str(action or "").strip().lower())
    except ValueError as exc:
        raise ValueError("action_invalid") from exc
    legacy_mount = source_kind == SourceKind.LEGACY_MOUNT.value
    passport = (
        passport_from_recovery_point(point)
        if point
        else legacy_mount_passport()
        if legacy_mount
        else None
    )
    config = runtime_config()
    plan = compile_lifecycle_plan(
        LifecycleRequest(
            intent=intent,
            activate=activate,
            confirmation_present=confirmation_present,
            previous_recovery_point_exists=RecoveryPoint.objects.using("control")
            .filter(
                dataset_id=config.dataset_id,
                state=RecoveryPoint.State.VERIFIED,
            )
            .exists(),
        ),
        instance_identity(),
        capabilities_for(
            config,
            point=point,
            legacy_mount=legacy_mount,
        ),
        passport,
    )
    return config, plan


def action_status(config: RuntimeConfig) -> Mapping[str, str]:
    probed = _connection_capability(config.connection, "probed")
    readable = _connection_capability(config.connection, "read")
    writable = _connection_capability(config.connection, "write") and _connection_capability(
        config.connection,
        "conditional_write",
    )
    return {
        "backup": (
            "ready"
            if config.enabled and writable
            else "check_required"
            if config.enabled and config.connection and not probed
            else "blocked"
        ),
        "restore": (
            "ready"
            if config.enabled and readable
            else "check_required"
            if config.enabled and config.connection and not probed
            else "blocked"
        ),
        "import": "ready" if config.enabled else "blocked",
    }
