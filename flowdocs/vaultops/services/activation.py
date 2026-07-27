"""Staging-only activation coordinator and supervisor result projection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone

from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    RestoreWorkspace,
    RuntimePointerObservation,
)
from vaultops.runtime_control import (
    RuntimeControlError,
    atomic_write_json,
    read_runtime_pointer,
    runtime_control_paths,
    sign_document,
    validate_runtime_workspace,
    verify_document,
)
from vaultops.services.audit import append_event
from vaultops.services.lifecycle import transition_generation_runtime_state


class ActivationCoordinatorError(RuntimeError):
    reason_code = "activation_failed"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _guard_activation_enabled():
    identity = settings.ENV_IDENTITY
    if identity.is_production:
        raise ActivationCoordinatorError("production_activation_disabled")
    if (
        not settings.STAGING_RUNTIME_ACTIVATION_ENABLED
        or identity.app_env.value != "staging"
    ):
        raise ActivationCoordinatorError("staging_activation_disabled")
    if not settings.VAULT_ADMIN_MUTATIONS_ENABLED:
        raise ActivationCoordinatorError("vault_admin_mutations_disabled")


def _read_smoke_queries():
    path = Path(settings.ACTIVATION_SMOKE_QUERIES_FILE)
    if path.is_symlink() or not path.is_file():
        raise ActivationCoordinatorError("activation_smoke_queries_missing")
    try:
        raw = path.read_bytes()
        if len(raw) > 64 * 1024:
            raise ActivationCoordinatorError(
                "activation_smoke_queries_too_large"
            )
        payload = json.loads(raw)
    except ActivationCoordinatorError:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ActivationCoordinatorError(
            "activation_smoke_queries_invalid"
        ) from exc
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, list) or len(queries) < 2:
        raise ActivationCoordinatorError("activation_smoke_queries_invalid")
    locales = set()
    for item in queries:
        if (
            not isinstance(item, dict)
            or item.get("locale") not in {"en", "mr"}
            or not isinstance(item.get("query"), str)
            or not item["query"].strip()
            or len(item["query"]) > 240
        ):
            raise ActivationCoordinatorError(
                "activation_smoke_queries_invalid"
            )
        locales.add(item["locale"])
    if locales != {"en", "mr"}:
        raise ActivationCoordinatorError(
            "activation_smoke_locales_incomplete"
        )
    return hashlib.sha256(raw).hexdigest()


def _verify_recovery_superadmin(database_path):
    username = settings.ACTIVATION_RECOVERY_SUPERADMIN_USERNAME
    password = settings.ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD
    try:
        connection = sqlite3.connect(
            f"file:{Path(database_path)}?mode=ro", uri=True
        )
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("core_customuser")'
            ).fetchall()
        }
        required = {
            "username",
            "password",
            "is_active",
            "is_superuser",
            "role",
        }
        if not required.issubset(columns):
            raise ActivationCoordinatorError(
                "activation_recovery_superadmin_unproven"
            )
        row = connection.execute(
            "SELECT password, is_active, is_superuser, role "
            "FROM core_customuser WHERE username=? LIMIT 1",
            (username,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise ActivationCoordinatorError(
            "activation_recovery_superadmin_unproven"
        ) from exc
    finally:
        if "connection" in locals():
            connection.close()
    if (
        not row
        or not check_password(password, row[0])
        or not bool(row[1])
        or not bool(row[2])
        or row[3] != "superadmin"
    ):
        raise ActivationCoordinatorError(
            "activation_recovery_superadmin_unproven"
        )


def _verify_workspace(workspace):
    if workspace.state != RestoreWorkspace.State.ACTIVATION_READY:
        raise ActivationCoordinatorError("workspace_not_activation_ready")
    if not workspace.runtime_path or not workspace.prepared_at:
        raise ActivationCoordinatorError("runtime_workspace_unprepared")
    if (
        timezone.now() - workspace.prepared_at
    ).total_seconds() > settings.VAULT_VALIDATION_MAX_AGE_SECONDS:
        raise ActivationCoordinatorError("activation_validation_expired")
    rehearsal = workspace.rehearsal_evidence
    identity = settings.ENV_IDENTITY
    if (
        not rehearsal.get("success")
        or rehearsal.get("app_release") != identity.app_release_version
        or rehearsal.get("image_digest") != identity.app_image_digest
    ):
        raise ActivationCoordinatorError(
            "activation_rehearsal_evidence_mismatch"
        )
    runtime, database, _, _ = validate_runtime_workspace(
        workspace.runtime_path,
        runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
        generation_id=workspace.generation.generation_id,
        manifest_digest=workspace.manifest_digest,
    )
    _verify_recovery_superadmin(database)
    return runtime


def schedule_activation(
    workspace,
    *,
    actor_id=None,
    actor_name="",
    confirmed=False,
    expires_seconds=300,
):
    """Create one signed intent; the web supervisor performs the cutover."""
    _guard_activation_enabled()
    if not confirmed:
        raise ActivationCoordinatorError("typed_confirmation_required")
    if expires_seconds < 30 or expires_seconds > 900:
        raise ActivationCoordinatorError("activation_intent_expiry_invalid")
    runtime = _verify_workspace(workspace)
    from core.artifact_cleanup import capacity_report

    runtime_bytes = sum(
        path.stat().st_size
        for path in runtime.rglob("*")
        if path.is_file()
    )
    activation_capacity = capacity_report(
        source_bytes=runtime_bytes,
        operation="activation",
        target_root=settings.RUNTIME_GENERATIONS_ROOT,
    )
    if not (
        activation_capacity["byte_capacity_ok"]
        and activation_capacity["inode_capacity_ok"]
    ):
        raise ActivationCoordinatorError(
            "activation_capacity_insufficient"
        )
    smoke_queries_digest = _read_smoke_queries()
    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    try:
        current_pointer = read_runtime_pointer(
            paths["active"],
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
        )
    except RuntimeControlError as exc:
        raise ActivationCoordinatorError(
            "activation_previous_runtime_invalid"
        ) from exc
    if current_pointer.generation_id == workspace.generation.generation_id:
        raise ActivationCoordinatorError("generation_already_active")
    try:
        previous_generation = ArtifactGeneration.objects.get(
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            generation_id=current_pointer.generation_id,
        )
    except ArtifactGeneration.DoesNotExist as exc:
        raise ActivationCoordinatorError(
            "activation_previous_generation_unprojected"
        ) from exc
    if previous_generation.runtime_state != ArtifactGeneration.RuntimeState.ACTIVE:
        raise ActivationCoordinatorError(
            "activation_previous_runtime_unverified"
        )

    intent_public_id = uuid.uuid4()
    expires_at = timezone.now() + timedelta(seconds=expires_seconds)
    payload = {
        "schema_version": 1,
        "kind": "activation_intent",
        "intent_id": str(intent_public_id),
        "deployment_id": settings.ENV_IDENTITY.deployment_id,
        "target_generation_id": workspace.generation.generation_id,
        "target_manifest_digest": workspace.manifest_digest,
        "target_runtime_path": str(runtime),
        "previous_generation_id": current_pointer.generation_id,
        "previous_pointer_digest": current_pointer.pointer_digest,
        "smoke_queries_digest": smoke_queries_digest,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "expires_at_unix": int(expires_at.timestamp()),
        "state_version": 1,
    }
    signed_intent = sign_document(
        payload, settings.ACTIVATION_INTENT_SIGNING_KEY
    )
    with transaction.atomic(using="control"):
        if ActivationIntent.objects.select_for_update().filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            state__in=[
                ActivationIntent.State.PENDING,
                ActivationIntent.State.APPLYING,
            ],
        ).exists():
            raise ActivationCoordinatorError(
                "runtime_activation_in_progress"
            )
        intent = ActivationIntent.objects.create(
            public_id=intent_public_id,
            workspace=workspace,
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            target_generation_id=workspace.generation.generation_id,
            previous_generation_id=current_pointer.generation_id,
            manifest_digest=workspace.manifest_digest,
            intent_digest=signed_intent["document_digest"],
            checkpoint={
                "previous_pointer_digest": current_pointer.pointer_digest,
                "target_runtime_path": str(runtime),
                "smoke_queries_digest": smoke_queries_digest,
                "protocol_state": "scheduled",
                "capacity_plan": activation_capacity,
            },
            actor_id=actor_id,
            actor_name=actor_name,
            expires_at=expires_at,
        )
        generation = workspace.generation
        generation.deployment_id = settings.ENV_IDENTITY.deployment_id
        generation.save(update_fields=["deployment_id", "updated_at"])
        transition_generation_runtime_state(
            generation,
            ArtifactGeneration.RuntimeState.PENDING,
            correlation_id=intent.public_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )
    intent_path = paths["intents"] / f"{intent.public_id}.json"
    try:
        atomic_write_json(intent_path, signed_intent)
    except Exception as exc:
        intent.state = ActivationIntent.State.FAILED
        intent.safe_error_code = "activation_intent_write_failed"
        intent.save(
            update_fields=["state", "safe_error_code", "updated_at"]
        )
        raise ActivationCoordinatorError(
            "activation_intent_write_failed"
        ) from exc
    append_event(
        action="activation_scheduled",
        result="succeeded",
        correlation_id=intent.public_id,
        actor_id=actor_id,
        actor_name=actor_name,
        after_state={
            "intent_state": intent.state,
            "runtime_state": ArtifactGeneration.RuntimeState.PENDING,
        },
        evidence={
            "intent_id": str(intent.public_id),
            "intent_digest": intent.intent_digest,
            "target_generation_id": intent.target_generation_id,
            "previous_generation_id": intent.previous_generation_id,
        },
    )
    return intent


def reconcile_activation_result(intent):
    """Project a signed supervisor result into the stable control database."""
    result_path = runtime_control_paths(settings.DATA_CONTROL_ROOT)[
        "results"
    ] / f"{intent.public_id}.json"
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
        verify_document(
            document,
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            expected_kind="activation_result",
            deployment_id=intent.deployment_id,
        )
    except (OSError, ValueError, RuntimeControlError) as exc:
        raise ActivationCoordinatorError(
            "activation_result_invalid"
        ) from exc
    if (
        document.get("intent_id") != str(intent.public_id)
        or document.get("intent_digest") != intent.intent_digest
    ):
        raise ActivationCoordinatorError(
            "activation_result_identity_mismatch"
        )
    status = document.get("status")
    active_generation_id = document.get("active_generation_id", "")
    if status not in {"committed", "rolled_back", "failed", "rollback_failed"}:
        raise ActivationCoordinatorError("activation_result_invalid")
    if (
        status == "committed"
        and active_generation_id != intent.target_generation_id
    ) or (
        status in {"rolled_back", "failed"}
        and active_generation_id != intent.previous_generation_id
    ):
        raise ActivationCoordinatorError(
            "activation_result_runtime_mismatch"
        )
    now = timezone.now()
    with transaction.atomic(using="control"):
        intent = ActivationIntent.objects.select_for_update().get(pk=intent.pk)
        if (
            intent.state
            in {
                ActivationIntent.State.COMMITTED,
                ActivationIntent.State.ROLLED_BACK,
                ActivationIntent.State.FAILED,
            }
            and intent.checkpoint.get("active_pointer_digest")
            == document.get("active_pointer_digest", "")
            and intent.checkpoint.get("protocol_state") == status
        ):
            return intent
        target = ArtifactGeneration.objects.select_for_update().get(
            deployment_id=intent.deployment_id,
            generation_id=intent.target_generation_id,
        )
        previous = ArtifactGeneration.objects.select_for_update().get(
            deployment_id=intent.deployment_id,
            generation_id=intent.previous_generation_id,
        )
        if status == "committed":
            previous.runtime_state = ArtifactGeneration.RuntimeState.PREVIOUS
            previous.save(update_fields=["runtime_state", "updated_at"])
            target.runtime_state = ArtifactGeneration.RuntimeState.ACTIVE
            target.save(update_fields=["runtime_state", "updated_at"])
            intent.state = ActivationIntent.State.COMMITTED
            intent.committed_at = now
            intent.safe_error_code = ""
        elif status == "rolled_back":
            previous.runtime_state = ArtifactGeneration.RuntimeState.ACTIVE
            previous.save(update_fields=["runtime_state", "updated_at"])
            target.runtime_state = (
                ArtifactGeneration.RuntimeState.ACTIVATION_FAILED
            )
            target.save(update_fields=["runtime_state", "updated_at"])
            intent.state = ActivationIntent.State.ROLLED_BACK
            intent.safe_error_code = document.get(
                "safe_error_code", "activation_verification_failed"
            )
        elif status == "failed":
            previous.runtime_state = ArtifactGeneration.RuntimeState.ACTIVE
            previous.save(update_fields=["runtime_state", "updated_at"])
            target.runtime_state = (
                ArtifactGeneration.RuntimeState.ACTIVATION_FAILED
            )
            target.save(update_fields=["runtime_state", "updated_at"])
            intent.state = ActivationIntent.State.FAILED
            intent.safe_error_code = document.get(
                "safe_error_code", "activation_failed"
            )
        else:
            previous.runtime_state = ArtifactGeneration.RuntimeState.UNKNOWN
            previous.save(update_fields=["runtime_state", "updated_at"])
            target.runtime_state = ArtifactGeneration.RuntimeState.UNKNOWN
            target.save(update_fields=["runtime_state", "updated_at"])
            intent.state = ActivationIntent.State.FAILED
            intent.safe_error_code = document.get(
                "safe_error_code", "activation_rollback_failed"
            )
        intent.applied_at = now
        intent.checkpoint = {
            **intent.checkpoint,
            "protocol_state": status or "failed",
            "active_pointer_digest": document.get(
                "active_pointer_digest", ""
            ),
        }
        intent.save(
            update_fields=[
                "state",
                "safe_error_code",
                "applied_at",
                "committed_at",
                "checkpoint",
                "updated_at",
            ]
        )
        RuntimePointerObservation.objects.create(
            deployment_id=intent.deployment_id,
            active_generation_id=document.get(
                "active_generation_id", ""
            ),
            previous_generation_id=document.get(
                "previous_generation_id", ""
            ),
            pointer_digest=document.get("active_pointer_digest", ""),
            process_identity=document.get("process_identity", {}),
            readiness_evidence=document.get("readiness_evidence", {}),
            status=status or "failed",
            observed_at=now,
        )
    append_event(
        action="activation_result_reconciled",
        result=(
            "succeeded" if status == "committed" else "failed"
        ),
        correlation_id=intent.public_id,
        actor_id=intent.actor_id,
        actor_name=intent.actor_name,
        safe_error_code=intent.safe_error_code,
        after_state={
            "intent_state": intent.state,
            "active_generation_id": document.get(
                "active_generation_id", ""
            ),
        },
        evidence={"intent_digest": intent.intent_digest},
    )
    return intent
