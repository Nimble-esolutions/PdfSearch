"""Secret-free Data Operations v3 readiness and receipt projection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from django.conf import settings

from .models import DataOperation
from .public_auth import public_auth_gate
from .v3_config import V3ConfigurationError
from .v3_planning import action_status, runtime_config


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _indexing_ratio() -> float:
    try:
        from core.models import PDFFile, SEARCHABLE_PDF_LIFECYCLES

        total = PDFFile.objects.filter(
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
        ).count()
        if total == 0:
            return 1.0
        indexed = PDFFile.objects.filter(
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            indexed=True,
            processing_status="ready",
        ).count()
        return round(indexed / total, 4)
    except Exception:
        return 0.0


def _last_operation(
    kinds: str | Iterable[str],
    *,
    active_generation: str = "",
    active_manifest_digest: str = "",
) -> dict[str, Any] | None:
    requested_kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    try:
        operations = DataOperation.objects.using("control").filter(
            kind__in=requested_kinds,
            state=DataOperation.State.SUCCEEDED,
            lifecycle_plan__contract_version=3,
        )[:50]
    except Exception:
        return None
    for operation in operations:
        result = operation.result if isinstance(operation.result, dict) else {}
        if result.get("contract_version") != 3:
            continue
        manifest_digest = str(result.get("manifest_digest", "") or "").lower()
        if not _DIGEST_RE.fullmatch(manifest_digest):
            continue
        activation = result.get("activation")
        if not isinstance(activation, Mapping):
            activation = {}
        activation_generation = str(
            activation.get("generation_id")
            or activation.get("active_generation")
            or ""
        )
        activation_manifest = str(
            activation.get("manifest_digest") or manifest_digest
        ).lower()
        if active_generation and (
            activation_generation != active_generation
            or activation_manifest != active_manifest_digest
        ):
            continue
        return {
            "id": str(operation.public_id),
            "kind": operation.kind,
            "finished_at": operation.finished_at,
            "release_id": operation.release_id or result.get("release_id", ""),
            "manifest_digest": manifest_digest,
            "connection_id": str(operation.connection.public_id)
            if operation.connection_id
            else "",
            "activation_generation": activation_generation,
            "activation_manifest_digest": activation_manifest,
            "contract_version": 3,
        }
    return None


def _runtime_evidence() -> dict[str, Any]:
    """Project only the exact runtime identity verified from a signed pointer."""
    configured_generation = str(
        getattr(settings, "RUNTIME_GENERATION_ID", "") or ""
    )
    configured_digest = str(
        getattr(settings, "RUNTIME_MANIFEST_DIGEST", "") or ""
    ).lower()
    runtime = getattr(settings, "ACTIVE_RUNTIME", None)
    runtime_generation = str(getattr(runtime, "generation_id", "") or "")
    runtime_digest = str(getattr(runtime, "manifest_digest", "") or "").lower()
    pointer_digest = str(getattr(runtime, "pointer_digest", "") or "").lower()
    verified = bool(
        runtime is not None
        and runtime_generation
        and runtime_generation == configured_generation
        and runtime_digest == configured_digest
        and _DIGEST_RE.fullmatch(runtime_digest)
        and _DIGEST_RE.fullmatch(pointer_digest)
    )
    if verified:
        reason_code = "signed_runtime_pointer_verified"
    elif runtime is None:
        reason_code = "signed_runtime_pointer_missing"
    elif runtime_generation != configured_generation or runtime_digest != configured_digest:
        reason_code = "signed_runtime_identity_mismatch"
    else:
        reason_code = "signed_runtime_pointer_evidence_invalid"
    return {
        "verified": verified,
        "generation_id": runtime_generation if verified else "",
        "manifest_digest": runtime_digest if verified else "",
        "pointer_digest": pointer_digest if verified else "",
        "configured_generation_id": configured_generation,
        "configured_manifest_digest": configured_digest,
        "reason_code": reason_code,
    }


def _policy_receipts(config, runtime: Mapping[str, Any]) -> dict[str, Any]:
    backup_policy = config.policy.get("backup", {})
    restore_policy = config.policy.get("restore", {})
    backup_policy = backup_policy if isinstance(backup_policy, Mapping) else {}
    restore_policy = restore_policy if isinstance(restore_policy, Mapping) else {}
    backup_mode = str(backup_policy.get("mode", "manual") or "manual").lower()
    backup_required = bool(backup_policy.get("require_receipt", False)) or (
        backup_mode in {"scheduled", "changes"}
    )
    restore_required = bool(restore_policy.get("require_receipt", False))
    last_backup = _last_operation(DataOperation.Kind.BACKUP)
    last_restore = _last_operation(
        (DataOperation.Kind.RESTORE, DataOperation.Kind.IMPORT),
        active_generation=str(runtime.get("generation_id") or "")
        if restore_required
        else "",
        active_manifest_digest=str(runtime.get("manifest_digest") or "")
        if restore_required
        else "",
    )
    return {
        "backup": {
            "required": backup_required,
            "status": "present"
            if last_backup
            else "missing"
            if backup_required
            else "not_required",
            "receipt": last_backup,
        },
        "restore": {
            "required": restore_required,
            "status": "present"
            if last_restore
            else "missing"
            if restore_required
            else "not_required",
            "receipt": last_restore,
        },
    }


def readiness_payload() -> dict[str, Any]:
    enabled = bool(getattr(settings, "DATAOPS_ENABLED", False))
    try:
        config = runtime_config()
        actions = dict(action_status(config))
    except V3ConfigurationError as exc:
        config = None
        actions = {"backup": "blocked", "restore": "blocked", "import": "blocked"}
        config_error = exc.code
    except Exception:
        config = None
        actions = {"backup": "blocked", "restore": "blocked", "import": "blocked"}
        config_error = "dataops_v3_configuration_failed"
    else:
        config_error = ""

    runtime = _runtime_evidence()
    ratio = _indexing_ratio()
    public_authentication = public_auth_gate(settings)
    receipts = (
        _policy_receipts(config, runtime)
        if config is not None
        else {
            "backup": {"required": False, "status": "unknown", "receipt": None},
            "restore": {"required": False, "status": "unknown", "receipt": None},
        }
    )
    required_receipts_present = all(
        not item["required"] or item["status"] == "present"
        for item in receipts.values()
    )
    backup_action_required = bool(receipts["backup"]["required"])
    required_actions_ready = actions.get("restore") == "ready" and (
        not backup_action_required or actions.get("backup") == "ready"
    )

    if not enabled:
        status = "not_configured"
    elif config_error:
        status = "degraded"
    elif public_authentication["status"] == "blocked":
        status = "degraded"
    elif not runtime["verified"] or ratio < 1.0:
        status = "degraded"
    elif not required_actions_ready or not required_receipts_present:
        status = "degraded"
    else:
        status = "ok"

    connection_view = config.connection if config is not None else None
    connection = (
        {
            "public_id": connection_view.public_id,
            "configured": True,
            "source": connection_view.source,
            "provider": connection_view.provider,
            "dataset_id": connection_view.dataset_id,
            "capabilities": {
                name: bool(connection_view.capabilities.get(name, False))
                for name in (
                    "probed",
                    "read",
                    "write",
                    "conditional_write",
                )
            },
        }
        if connection_view is not None
        else None
    )
    return {
        "contract_version": 3,
        "status": status,
        "enabled": enabled,
        "environment": config.environment if config else "",
        "deployment_id": config.deployment_id if config else "",
        "dataset_id": config.dataset_id if config else "",
        "configuration_digest": config.digest if config else "",
        "configuration_error": config_error,
        "connection": connection,
        "connection_id": str(connection.get("public_id") or "") if connection else "",
        "actions": actions,
        "policy": dict(config.policy) if config else {},
        "runtime_evidence": runtime,
        "active_generation": runtime["generation_id"],
        "manifest_digest": runtime["manifest_digest"],
        "indexing_ratio": ratio,
        "receipts": receipts,
        "last_backup": receipts["backup"]["receipt"],
        "last_restore": receipts["restore"]["receipt"],
        "signed_active_generation": runtime["verified"],
        "public_authentication": public_authentication,
        # Compatibility fields remain empty: v3 uses one capability-bearing
        # connection and does not expose source/destination profile selectors.
        "source_profile": "",
        "destination_profile": "",
    }
