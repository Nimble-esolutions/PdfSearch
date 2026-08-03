"""Candidate-only preparation for verified Data Operations v3 restores."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from django.conf import settings


class V3CandidateError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise V3CandidateError(code) from exc
    if not isinstance(payload, dict):
        raise V3CandidateError(code)
    return payload


def prepare_recovery_candidate(
    restore_receipt: Mapping[str, Any],
    *,
    maximum_external_documents: int = 500,
    runner=subprocess.run,
) -> dict[str, Any]:
    """Prepare one verified restore without ever targeting active runtime data."""

    if restore_receipt.get("verified") is not True:
        raise V3CandidateError("candidate_restore_unverified")
    manifest_digest = str(restore_receipt.get("manifest_sha256") or "")
    if len(manifest_digest) != 64:
        raise V3CandidateError("candidate_manifest_digest_invalid")
    workspace = Path(str(restore_receipt.get("workspace") or ""))
    if workspace.is_symlink() or not workspace.is_dir():
        raise V3CandidateError("candidate_workspace_invalid")
    workspace = workspace.resolve()
    if maximum_external_documents < 0:
        raise V3CandidateError("candidate_budget_invalid")
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "flowdocs.settings_candidate",
            "MAINTENANCE_WORKSPACE_ROOT": str(workspace),
            "MAINTENANCE_CANDIDATE_EXECUTION": "1",
            "MAINTENANCE_CANDIDATE_PREPARATION_ENABLED": "0",
            "VAULT_SYNC_ENABLED": "0",
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "0",
        }
    )
    try:
        result = runner(
            [
                sys.executable,
                str(Path(settings.BASE_DIR) / "manage.py"),
                "prepare_dataops_candidate",
                "--manifest-digest",
                manifest_digest,
                "--maximum-external-documents",
                str(maximum_external_documents),
            ],
            cwd=settings.BASE_DIR,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=getattr(settings, "MAINTENANCE_JOB_TIMEOUT_SECONDS", 7200),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise V3CandidateError("candidate_preparation_timeout", retryable=True) from exc
    if result.returncode:
        raise V3CandidateError("candidate_preparation_failed", retryable=True)
    receipt = _json_object(
        workspace / ".dataops-candidate.json",
        "candidate_receipt_invalid",
    )
    if (
        receipt.get("schema_version") != 3
        or receipt.get("success") is not True
        or receipt.get("manifest_sha256") != manifest_digest
        or receipt.get("indexing_ratio") != 1.0
    ):
        raise V3CandidateError("candidate_receipt_mismatch")
    return {**receipt, "workspace": str(workspace)}
