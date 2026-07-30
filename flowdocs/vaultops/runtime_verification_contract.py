"""Bounded cross-process evidence for activation runtime verification."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


SAFE_RUNTIME_VERIFICATION_REASONS = frozenset(
    {
        "activation_pdf_missing",
        "activation_pdf_path_invalid",
        "activation_recovery_superadmin_unproven",
        "activation_runtime_database_invalid",
        "activation_runtime_faiss_invalid",
        "activation_runtime_identity_mismatch",
        "activation_runtime_migrations_pending",
        "activation_runtime_smoke_failed",
        "activation_search_probe_failed",
        "activation_smoke_folder_missing",
        "activation_smoke_queries_changed",
        "activation_smoke_queries_invalid",
    }
)

GENERIC_RUNTIME_VERIFICATION_REASON = "activation_runtime_command_failed"
MAX_FAILURE_EVIDENCE_BYTES = 256


def safe_runtime_verification_reason(reason_code):
    if (
        isinstance(reason_code, str)
        and reason_code in SAFE_RUNTIME_VERIFICATION_REASONS
    ):
        return reason_code
    return GENERIC_RUNTIME_VERIFICATION_REASON


def write_runtime_verification_failure(path, reason_code):
    """Atomically write one allowlisted reason without exception detail."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schema_version": 1,
            "reason_code": safe_runtime_verification_reason(reason_code),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)


def read_runtime_verification_failure(path):
    """Read one strict evidence record and always remove the transient file."""
    target = Path(path)
    try:
        if not target.is_file() or target.stat().st_size > MAX_FAILURE_EVIDENCE_BYTES:
            return GENERIC_RUNTIME_VERIFICATION_REASON
        value = json.loads(target.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "reason_code"}
            or value.get("schema_version") != 1
        ):
            return GENERIC_RUNTIME_VERIFICATION_REASON
        return safe_runtime_verification_reason(value.get("reason_code"))
    except (OSError, UnicodeDecodeError, ValueError):
        return GENERIC_RUNTIME_VERIFICATION_REASON
    finally:
        target.unlink(missing_ok=True)
