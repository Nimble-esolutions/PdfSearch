"""DataOps v3 adapter for the signed runtime activation protocol.

DataOps owns candidate selection and identity.  The process supervisor remains
the deliberately small cutover authority because it can restart both runtime
roles and roll back after process death.  The temporary VaultOps model
projection in this module is an internal compatibility bridge; no profile or
VaultOps operator configuration is consulted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.media_quarantine import build_unavailable_attestation
from vaultops.models import (
    ArtifactGeneration,
    ArtifactValidation,
    RestoreWorkspace,
    VaultConnectionProfile,
)
from vaultops.runtime_control import (
    RuntimeControlError,
    atomic_write_json,
    validate_runtime_workspace,
)
from vaultops.services.activation import (
    ActivationCoordinatorError,
    schedule_activation,
)

from .models import DataOperation, RestoreCandidate


class V3ActivationError(RuntimeError):
    code = "dataops_activation_failed"
    retryable = False

    def __init__(self, code=None, *, retryable=False):
        self.code = code or self.code
        self.retryable = retryable
        super().__init__(self.code)


_RUNTIME_COMPONENTS = (
    "database",
    "media",
    "pdf_cache",
    "faiss",
    "chroma",
)


def _candidate_index_evidence(candidate: RestoreCandidate) -> dict:
    evidence = candidate.evidence
    if not isinstance(evidence, Mapping) or evidence.get("indexing_ratio") != 1.0:
        raise V3ActivationError("candidate_index_evidence_missing")
    reconciliation = evidence.get("component_reconciliation")
    if not isinstance(reconciliation, Mapping):
        raise V3ActivationError("candidate_component_evidence_missing")
    components = {}
    for name in _RUNTIME_COMPONENTS:
        component = reconciliation.get(name)
        if not isinstance(component, Mapping) or component.get("ready") is not True:
            raise V3ActivationError(f"candidate_{name}_unresolved")
        reasons = component.get("rebuild_reasons")
        if not isinstance(reasons, list) or any(
            not isinstance(reason, str) for reason in reasons
        ):
            raise V3ActivationError("candidate_component_evidence_invalid")
        source = str(component.get("source") or "")
        if source not in {"candidate_preparation", "signed_manifest_and_restore"}:
            raise V3ActivationError("candidate_component_evidence_invalid")
        if (source == "candidate_preparation") != bool(reasons):
            raise V3ActivationError("candidate_component_evidence_invalid")
        signed = component.get("signed")
        if source == "signed_manifest_and_restore" and (
            not isinstance(signed, Mapping)
            or signed.get("complete") is not True
            or signed.get("coherent") is False
            or signed.get("rebuild_required") is True
        ):
            raise V3ActivationError(f"candidate_{name}_unresolved")
        components[name] = {
            "ready": True,
            "source": source,
            "rebuild_reasons": list(reasons),
        }

    preparation = evidence.get("candidate_preparation")
    validation = (
        preparation.get("validation")
        if isinstance(preparation, Mapping)
        else None
    )
    faiss = validation.get("faiss") if isinstance(validation, Mapping) else {}
    chroma = validation.get("chroma") if isinstance(validation, Mapping) else {}
    folder_ids = (
        validation.get("validated_folder_ids")
        if isinstance(validation, Mapping)
        else []
    )
    if (
        not isinstance(faiss, Mapping)
        or not isinstance(chroma, Mapping)
        or not isinstance(folder_ids, list)
    ):
        raise V3ActivationError("candidate_index_evidence_invalid")
    if any(
        not isinstance(folder_id, int)
        or isinstance(folder_id, bool)
        or folder_id < 0
        for folder_id in folder_ids
    ):
        raise V3ActivationError("candidate_index_evidence_invalid")
    rebuilt_components = (
        preparation.get("rebuilt_components")
        if isinstance(preparation, Mapping)
        else None
    )
    prepared_components = {
        name
        for name, component in components.items()
        if component["source"] == "candidate_preparation"
    }
    if prepared_components and (
        not isinstance(preparation, Mapping)
        or preparation.get("success") is not True
        or not isinstance(rebuilt_components, list)
        or any(not isinstance(name, str) for name in rebuilt_components)
        or not prepared_components.issubset(set(rebuilt_components))
    ):
        raise V3ActivationError("candidate_rebuild_evidence_missing")
    if (
        components["faiss"]["source"] == "candidate_preparation"
        and not isinstance(validation, Mapping)
    ):
        raise V3ActivationError("candidate_faiss_evidence_missing")
    if components["chroma"]["source"] == "candidate_preparation" and (
        chroma.get("ready") is not True
        or not isinstance(chroma.get("file_count"), int)
        or isinstance(chroma.get("file_count"), bool)
        or chroma.get("file_count") < 0
        or len(str(chroma.get("sha256") or "")) != 64
    ):
        raise V3ActivationError("candidate_chroma_evidence_missing")
    serialized_faiss = json.dumps(
        dict(faiss),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    serialized_chroma = json.dumps(
        dict(chroma),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "indexing_ratio": 1.0,
        "components": components,
        "validated_folder_ids": list(folder_ids),
        "faiss_index_count": len(faiss),
        "faiss_evidence_sha256": hashlib.sha256(serialized_faiss).hexdigest(),
        "chroma_file_count": int(chroma.get("file_count", 0)),
        "chroma_evidence_sha256": hashlib.sha256(serialized_chroma).hexdigest(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_records(root: Path) -> tuple[list[tuple[str, int, str]], int]:
    configured = Path(root)
    if configured.is_symlink() or not configured.is_dir():
        raise V3ActivationError("candidate_workspace_unsafe")
    root = configured.resolve()
    records = []
    total_bytes = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        if directory_path.is_symlink():
            raise V3ActivationError("candidate_workspace_unsafe")
        for name in directories:
            if (directory_path / name).is_symlink():
                raise V3ActivationError("candidate_workspace_unsafe")
        for name in files:
            path = directory_path / name
            stat_result = os.stat(path, follow_symlinks=False)
            if path.is_symlink() or not path.is_file() or stat_result.st_nlink != 1:
                raise V3ActivationError("candidate_workspace_unsafe")
            relative = path.relative_to(root).as_posix()
            if relative == "runtime-evidence.json":
                continue
            records.append((relative, stat_result.st_size, _sha256(path)))
            total_bytes += stat_result.st_size
    records.sort()
    return records, total_bytes


def _copy_candidate(source: Path, destination: Path, records) -> None:
    destination.mkdir(mode=0o700)
    for directory, directories, _files in os.walk(source, followlinks=False):
        relative_directory = Path(directory).resolve().relative_to(source.resolve())
        (destination / relative_directory).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
        for name in directories:
            (destination / relative_directory / name).mkdir(
                parents=True, exist_ok=True, mode=0o700
            )
    for relative, _size, _digest in records:
        source_path = source / relative
        target_path = destination / relative
        target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source_path, target_path, follow_symlinks=False)
    copied, _ = _safe_records(destination)
    if copied != records:
        raise V3ActivationError("candidate_changed_during_projection")


def _freeze(root: Path) -> None:
    for directory, directories, files in os.walk(
        root, topdown=False, followlinks=False
    ):
        path = Path(directory)
        for name in files:
            os.chmod(path / name, 0o440, follow_symlinks=False)
        for name in directories:
            os.chmod(path / name, 0o550, follow_symlinks=False)
        os.chmod(path, 0o550, follow_symlinks=False)


def _generation_id(candidate: RestoreCandidate) -> str:
    raw = f"dataops-{candidate.recovery_point.release_id}"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    if not normalized:
        raise V3ActivationError("runtime_generation_id_invalid")
    return normalized[:160]


def _compatibility_profile(candidate: RestoreCandidate):
    point = candidate.recovery_point
    deployment_id = settings.ENV_IDENTITY.deployment_id
    profile_identity = f"{deployment_id}:{point.dataset_id}"
    key = f"dataops-v3-{hashlib.sha256(profile_identity.encode()).hexdigest()[:12]}"
    profile, _ = VaultConnectionProfile.objects.using("control").update_or_create(
        key=key,
        defaults={
            "display_name": "DataOps v3 runtime bridge",
            "source": VaultConnectionProfile.Source.STORED,
            "enabled": True,
            "read_only": True,
            "environment_locked": True,
            "endpoint_origin": "",
            "bucket": "",
            "region": "",
            "dataset_id": point.dataset_id,
            "credential_alias": "",
            "capability_evidence": {"runtime_bridge": True, "schema_version": 3},
            "fingerprint": hashlib.sha256(
                profile_identity.encode()
            ).hexdigest(),
        },
    )
    return profile


def project_candidate_runtime(candidate: RestoreCandidate):
    """Copy one READY candidate into immutable runtime storage idempotently."""
    if candidate.state != RestoreCandidate.State.READY:
        raise V3ActivationError("candidate_not_activation_ready")
    if len(candidate.manifest_digest) != 64:
        raise V3ActivationError("candidate_manifest_digest_invalid")
    index_evidence = _candidate_index_evidence(candidate)
    configured_source = Path(candidate.workspace)
    if configured_source.is_symlink():
        raise V3ActivationError("candidate_workspace_unsafe")
    source = configured_source.resolve()
    records, total_bytes = _safe_records(source)
    required = {"db.sqlite3", "media", "pdf_cache", "faiss_indexes", "chroma_db"}
    present = {item.name for item in source.iterdir()}
    if not required.issubset(present):
        raise V3ActivationError("candidate_runtime_components_missing")
    generation_id = _generation_id(candidate)
    runtime_root = Path(settings.RUNTIME_GENERATIONS_ROOT)
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise V3ActivationError("runtime_root_unsafe")
    target = runtime_root / f"{generation_id}-{candidate.manifest_digest[:12]}"
    incomplete = runtime_root / f".{generation_id}-{candidate.pk}.incomplete"
    if incomplete.exists():
        raise V3ActivationError("runtime_projection_incomplete_exists")
    if not target.exists():
        try:
            _copy_candidate(source, incomplete, records)
            atomic_write_json(
                incomplete / "runtime-evidence.json",
                {
                    "schema_version": 3,
                    "generation_id": generation_id,
                    "manifest_digest": candidate.manifest_digest,
                    "recovery_point_id": str(candidate.recovery_point.public_id),
                    "candidate_id": candidate.pk,
                    "file_count": len(records),
                    "total_bytes": total_bytes,
                    "index_evidence": index_evidence,
                },
            )
            _freeze(incomplete)
            os.replace(incomplete, target)
        except Exception:
            shutil.rmtree(incomplete, ignore_errors=True)
            raise
    published_records, published_bytes = _safe_records(target)
    if published_records != records or published_bytes != total_bytes:
        raise V3ActivationError("runtime_generation_collision")
    try:
        _runtime, _database, _directories, runtime_evidence = validate_runtime_workspace(
            target,
            runtime_root=runtime_root,
            generation_id=generation_id,
            manifest_digest=candidate.manifest_digest,
        )
    except RuntimeControlError as exc:
        raise V3ActivationError(exc.reason_code) from exc
    if runtime_evidence.get("index_evidence") != index_evidence:
        raise V3ActivationError("runtime_index_evidence_mismatch")

    profile = _compatibility_profile(candidate)
    point = candidate.recovery_point
    unavailable = build_unavailable_attestation(())
    now = timezone.now()
    with transaction.atomic(using="control"):
        generation, created = ArtifactGeneration.objects.using(
            "control"
        ).get_or_create(
            profile=profile,
            dataset_id=point.dataset_id,
            generation_id=generation_id,
            defaults={
                "origin": ArtifactGeneration.Origin.VAULT_GENERATION,
                "manifest_digest": candidate.manifest_digest,
                "manifest": {
                    "schema_version": 3,
                    "generation_id": generation_id,
                    "manifest_digest": candidate.manifest_digest,
                    "unavailable_documents": unavailable,
                    "dataops_recovery_point_id": str(point.public_id),
                },
                "vault_state": ArtifactGeneration.VaultState.UNKNOWN,
                "runtime_state": ArtifactGeneration.RuntimeState.INACTIVE,
                "local_presence": ArtifactGeneration.LocalPresence.PREPARED,
                "source": "dataops_v3_candidate",
                "observed_at": now,
            },
        )
        if not created and generation.manifest_digest != candidate.manifest_digest:
            raise V3ActivationError("runtime_generation_collision")
        workspace, workspace_created = RestoreWorkspace.objects.using(
            "control"
        ).get_or_create(
            import_idempotency_key=f"dataops-v3-candidate-{candidate.pk}",
            defaults={
                "generation": generation,
                "state": RestoreWorkspace.State.ACTIVATION_READY,
                "manifest_digest": candidate.manifest_digest,
                "profile_fingerprint": profile.fingerprint,
                "runtime_path": str(target),
                "validation_evidence": {
                    "schema_version": 3,
                    "dataops_candidate_id": candidate.pk,
                    "indexing_ratio": candidate.evidence.get("indexing_ratio"),
                    "index_evidence": index_evidence,
                },
                "rehearsal_evidence": {
                    "success": True,
                    "app_release": settings.ENV_IDENTITY.app_release_version,
                    "image_digest": settings.ENV_IDENTITY.app_image_digest,
                    "dataops_candidate_id": candidate.pk,
                },
                "prepared_at": now,
                "expires_at": now + timedelta(days=7),
            },
        )
        if not workspace_created and (
            workspace.generation_id != generation.pk
            or workspace.manifest_digest != candidate.manifest_digest
            or Path(workspace.runtime_path).resolve() != target.resolve()
            or workspace.validation_evidence.get("index_evidence")
            != index_evidence
        ):
            raise V3ActivationError("runtime_workspace_collision")
        if created:
            ArtifactValidation.objects.using("control").create(
                generation=generation,
                validation_type="dataops_v3_candidate",
                status=ArtifactValidation.Status.PASSED,
                manifest_digest=candidate.manifest_digest,
                validator_version="dataops-v3/1",
                evidence={
                    "candidate_id": candidate.pk,
                    "recovery_point_id": str(point.public_id),
                    "indexing_ratio": candidate.evidence.get("indexing_ratio"),
                    "index_evidence": index_evidence,
                },
            )
    return workspace


def schedule_candidate_activation(
    candidate: RestoreCandidate,
    *,
    operation: DataOperation,
    confirmed: bool,
):
    """Project and schedule one exact DataOps candidate for signed cutover."""
    if not confirmed:
        raise V3ActivationError("operator_confirmation_required")
    workspace = project_candidate_runtime(candidate)
    request_digest = hashlib.sha256(
        f"{operation.lifecycle_plan_digest}:{candidate.pk}:{candidate.manifest_digest}".encode()
    ).hexdigest()
    try:
        intent = schedule_activation(
            workspace,
            confirmed=True,
            idempotency_key=f"dataops-v3-{operation.public_id}",
            request_state_digest=request_digest,
        )
    except ActivationCoordinatorError as exc:
        raise V3ActivationError(exc.reason_code) from exc
    evidence = dict(candidate.evidence or {})
    evidence["activation"] = {
        "state": "scheduled",
        "intent_id": str(intent.public_id),
        "intent_digest": intent.intent_digest,
        "generation_id": intent.target_generation_id,
        "manifest_digest": intent.manifest_digest,
        "runtime_path": str(workspace.runtime_path),
    }
    candidate.evidence = evidence
    candidate.save(update_fields=["evidence", "updated_at"])
    return evidence["activation"]
