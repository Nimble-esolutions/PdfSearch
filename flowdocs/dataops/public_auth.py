"""Public-authentication exception gate for production-derived stage data."""

from __future__ import annotations

from typing import Any


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def public_auth_gate(settings_obj: Any) -> dict[str, Any]:
    """Return secret-free evidence for the stage public-authentication gate.

    The gate is deliberately opt-in.  A deployment that has not declared that
    it is carrying exact production authentication data does not get blocked by
    this policy.  The stage profile in the recovery runbook declares the gate
    explicitly and keeps anonymous search disabled.
    """
    required = bool(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED", False))
    public_enabled = bool(getattr(settings_obj, "PUBLIC_SEARCH_ENABLED", False))
    requested = required and public_enabled
    fields = {
        "owner": str(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_OWNER", "") or "").strip(),
        "monitoring": str(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_MONITORING", "") or "").strip(),
        "incident_response": str(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_INCIDENT_RESPONSE", "") or "").strip(),
        "rollback_authority": str(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_ROLLBACK_AUTHORITY", "") or "").strip(),
    }
    approved = bool(getattr(settings_obj, "STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED", False)) and all(fields.values())
    if not requested:
        status = "private_only" if required else "not_required"
        reason = "anonymous_stage_search_disabled" if required and not public_enabled else "production_auth_exception_not_requested"
    elif approved:
        status = "approved"
        reason = "security_exception_recorded"
    else:
        status = "blocked"
        reason = "security_owner_approval_required"
    return {
        "status": status,
        "required": required,
        "public_search_enabled": public_enabled,
        "requested": requested,
        "approved": approved,
        "recorded_controls": {name: bool(value) for name, value in fields.items()},
        "reason_code": reason,
    }


__all__ = ["public_auth_gate"]
