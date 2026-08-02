"""Secret-free Data Operations readiness and dashboard projection."""

from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Any

from django.conf import settings

from .config import resolve_profiles, resolve_selectors, validate_profiles
from .models import DataOperation


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _indexing_ratio() -> float:
    try:
        from core.models import PDFFile

        total = PDFFile.objects.count()
        if total == 0:
            return 1.0
        indexed = PDFFile.objects.filter(indexed=True).count()
        return round(indexed / total, 4)
    except Exception:
        return 0.0


def _last_operation(kind: str) -> dict[str, Any] | None:
    try:
        operation = DataOperation.objects.using("control").filter(
            kind=kind,
            state=DataOperation.State.SUCCEEDED,
        ).first()
    except Exception:
        return None
    if not operation:
        return None
    result = operation.result if isinstance(operation.result, dict) else {}
    return {
        "id": str(operation.public_id),
        "finished_at": operation.finished_at,
        "release_id": operation.release_id or result.get("release_id", ""),
        "manifest_digest": result.get("manifest_digest", ""),
        "source_profile": result.get("source_profile", operation.source_profile_key),
        "destination_profile": result.get("destination_profile", operation.destination_profile_key),
    }


def _runtime_evidence() -> tuple[str, str]:
    """Read the atomically published runtime pointer without exposing files."""
    generation = str(getattr(settings, "RUNTIME_GENERATION_ID", "") or "")
    digest = str(getattr(settings, "RUNTIME_MANIFEST_DIGEST", "") or "").lower()
    configured_root = getattr(settings, "RUNTIME_GENERATIONS_ROOT", None)
    if not configured_root:
        return generation, digest
    root = Path(configured_root)
    try:
        pointer = root / ".active-generation"
        if pointer.is_file():
            value = pointer.read_text(encoding="utf-8").strip()
            if value:
                generation = value
        active = root / "active"
        receipt = active / "restore-receipt.json"
        if receipt.is_file():
            marker = json.loads(receipt.read_text(encoding="utf-8"))
            if isinstance(marker, dict) and re.fullmatch(r"[0-9a-f]{64}", str(marker.get("manifest_digest", "")).lower()):
                digest = str(marker["manifest_digest"]).lower()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return generation, digest


def readiness_payload() -> dict[str, Any]:
    enabled = bool(getattr(settings, "DATAOPS_ENABLED", False))
    try:
        profiles = resolve_profiles()
        selectors = resolve_selectors()
        issues = tuple(
            {
                issue.code: issue
                for issue in (
                    *validate_profiles(profiles, selectors=selectors, operation="backup", credential_environment=os.environ),
                    *validate_profiles(profiles, selectors=selectors, operation="restore", credential_environment=os.environ),
                )
            }.values()
        )
    except Exception as exc:
        profiles = ()
        selectors = {}
        issues = ()
        config_error = str(exc)
    else:
        config_error = ""
    active_generation, manifest_digest = _runtime_evidence()
    signed_generation = bool(active_generation and _DIGEST_RE.fullmatch(manifest_digest))
    ratio = _indexing_ratio()
    last_backup = _last_operation(DataOperation.Kind.BACKUP)
    last_restore = _last_operation(DataOperation.Kind.RESTORE)
    if not enabled:
        status = "not_configured"
    elif config_error or issues:
        status = "degraded"
    elif not signed_generation or ratio < 1.0:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "enabled": enabled,
        "profiles": [profile.redacted() for profile in profiles],
        "configuration_issues": [issue.__dict__ for issue in issues],
        "configuration_error": config_error,
        "active_generation": active_generation,
        "source_profile": selectors.get("restore_source") or selectors.get("restore", ""),
        "destination_profile": selectors.get("backup_destination") or selectors.get("backup", ""),
        "manifest_digest": manifest_digest,
        "indexing_ratio": ratio,
        "last_backup": last_backup,
        "last_restore": last_restore,
        "signed_active_generation": signed_generation,
    }
