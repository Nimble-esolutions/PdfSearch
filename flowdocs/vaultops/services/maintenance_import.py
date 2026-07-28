"""Import a verified local-maintenance candidate into immutable runtime storage."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.candidate_maintenance import (
    WORKSPACE_MANIFEST,
    CandidateMaintenanceError,
    validate_candidate,
    workspace_root,
)
from core.emergency_recovery import RecoverySetError, verify_set
from core.rehearsal import RehearsalError, rehearse_migrations
from vaultops.models import (
    ArtifactGeneration,
    ArtifactValidation,
    RestoreWorkspace,
    VaultConnectionProfile,
)
from vaultops.runtime_control import (
    RuntimeControlError,
    atomic_write_json,
    canonical_bytes,
    read_runtime_pointer,
    runtime_control_paths,
    validate_runtime_workspace,
)
from vaultops.services.activation import verify_recovery_superadmin
from vaultops.services.audit import append_event


RUNTIME_SOURCE_ENTRIES = (
    "db.sqlite3",
    "media",
    "pdf_cache",
    "faiss_indexes",
    "chroma_db",
)
KNOWN_CANDIDATE_ONLY_ENTRIES = {WORKSPACE_MANIFEST, "backups"}


class MaintenanceImportError(RuntimeError):
    reason_code = "maintenance_candidate_import_failed"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_candidate_manifest(path):
    if path.is_symlink() or not path.is_file():
        raise MaintenanceImportError("maintenance_candidate_manifest_missing")
    file_stat = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        raise MaintenanceImportError("maintenance_candidate_manifest_unsafe")
    if file_stat.st_size > settings.VAULT_MAX_MANIFEST_BYTES:
        raise MaintenanceImportError("maintenance_candidate_manifest_too_large")
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise MaintenanceImportError(
            "maintenance_candidate_manifest_invalid"
        ) from exc
    if not isinstance(manifest, dict):
        raise MaintenanceImportError("maintenance_candidate_manifest_invalid")
    return manifest


def _resolve_candidate(job):
    candidate_id = str(job.options.get("candidate_workspace_id") or "")
    if (
        not candidate_id
        or candidate_id != Path(candidate_id).name
        or not candidate_id.startswith(f"mw-{job.public_id}-")
    ):
        raise MaintenanceImportError("maintenance_candidate_identity_invalid")
    root = workspace_root()
    candidate = root / candidate_id
    if candidate.is_symlink() or not candidate.is_dir():
        raise MaintenanceImportError("maintenance_candidate_workspace_unsafe")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MaintenanceImportError(
            "maintenance_candidate_workspace_unsafe"
        ) from exc
    return resolved


def _validate_job_and_manifest(job, candidate):
    if job.kind not in {
        "repair_indexes",
        "reindex_needed",
        "reindex_selected",
        "reindex_all",
    }:
        raise MaintenanceImportError("maintenance_candidate_kind_invalid")
    if job.status != "completed":
        raise MaintenanceImportError("maintenance_candidate_job_incomplete")
    if job.options.get("candidate_state") != "activation_ready":
        raise MaintenanceImportError("maintenance_candidate_not_ready")
    manifest = _read_candidate_manifest(candidate / WORKSPACE_MANIFEST)
    if (
        manifest.get("state") != "activation_ready"
        or manifest.get("workspace_id") != candidate.name
        or manifest.get("operation") != job.kind
        or manifest.get("source", {}).get("job_id") != str(job.public_id)
    ):
        raise MaintenanceImportError("maintenance_candidate_identity_invalid")
    try:
        expires_at = datetime.fromisoformat(manifest["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MaintenanceImportError(
            "maintenance_candidate_expiry_invalid"
        ) from exc
    if timezone.is_naive(expires_at):
        raise MaintenanceImportError("maintenance_candidate_expiry_invalid")
    if expires_at <= timezone.now():
        raise MaintenanceImportError("maintenance_candidate_expired")
    recovery_set_id = str(manifest.get("source", {}).get("recovery_set_id") or "")
    if recovery_set_id != str(job.options.get("recovery_set_id") or ""):
        raise MaintenanceImportError("maintenance_recovery_set_mismatch")
    try:
        recovery = verify_set(recovery_set_id)
    except RecoverySetError as exc:
        raise MaintenanceImportError(exc.reason_code) from exc
    if recovery.get("verification_state") != "verified":
        raise MaintenanceImportError("maintenance_recovery_set_unverified")
    return manifest, recovery


def _safe_files(candidate):
    top_level = {entry.name for entry in candidate.iterdir()}
    allowed = set(RUNTIME_SOURCE_ENTRIES) | KNOWN_CANDIDATE_ONLY_ENTRIES
    if top_level - allowed:
        raise MaintenanceImportError("maintenance_candidate_entries_unexpected")
    records = []
    total_bytes = 0
    for entry_name in RUNTIME_SOURCE_ENTRIES:
        source = candidate / entry_name
        if entry_name == "db.sqlite3":
            paths = [source]
        else:
            if source.is_symlink() or not source.is_dir():
                raise MaintenanceImportError(
                    "maintenance_candidate_structure_invalid"
                )
            paths = []
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise MaintenanceImportError(
                        "maintenance_candidate_file_unsafe"
                    )
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise MaintenanceImportError(
                        "maintenance_candidate_file_unsafe"
                    )
                paths.append(path)
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise MaintenanceImportError("maintenance_candidate_file_unsafe")
            file_stat = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                raise MaintenanceImportError("maintenance_candidate_file_unsafe")
            relative = path.relative_to(candidate).as_posix()
            record = {
                "path": relative,
                "bytes": file_stat.st_size,
                "sha256": _sha256(path),
            }
            records.append(record)
            total_bytes += file_stat.st_size
            if len(records) > settings.VAULT_MAX_MANIFEST_OBJECTS:
                raise MaintenanceImportError(
                    "maintenance_candidate_object_limit_exceeded"
                )
            if total_bytes > settings.VAULT_MAX_GENERATION_BYTES:
                raise MaintenanceImportError(
                    "maintenance_candidate_byte_limit_exceeded"
                )
    if not records or records[0]["path"] != "db.sqlite3":
        raise MaintenanceImportError("maintenance_candidate_database_missing")
    return records, total_bytes


def _copy_records(candidate, destination, records):
    for directory in RUNTIME_SOURCE_ENTRIES[1:]:
        (destination / directory).mkdir(parents=True, mode=0o700)
    for record in records:
        source = candidate / record["path"]
        target = destination / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, target, follow_symlinks=False)
        if (
            target.stat().st_size != record["bytes"]
            or _sha256(target) != record["sha256"]
            or _sha256(source) != record["sha256"]
        ):
            raise MaintenanceImportError("maintenance_candidate_changed")


def _make_read_only(root):
    for directory, directories, files in os.walk(
        root, topdown=False, followlinks=False
    ):
        for name in files:
            os.chmod(Path(directory) / name, 0o440, follow_symlinks=False)
        for name in directories:
            os.chmod(Path(directory) / name, 0o550, follow_symlinks=False)
        os.chmod(Path(directory), 0o550, follow_symlinks=False)


def _remove_runtime(root):
    if not Path(root).exists():
        return
    for directory, directories, files in os.walk(
        root, topdown=False, followlinks=False
    ):
        for name in files:
            os.chmod(Path(directory) / name, 0o600, follow_symlinks=False)
        for name in directories:
            os.chmod(Path(directory) / name, 0o700, follow_symlinks=False)
        os.chmod(Path(directory), 0o700, follow_symlinks=False)
    shutil.rmtree(root)


@contextmanager
def _import_lock(runtime_root):
    lock_path = Path(runtime_root) / ".maintenance-import.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _active_parent():
    identity = settings.ENV_IDENTITY
    try:
        return read_runtime_pointer(
            runtime_control_paths(settings.DATA_CONTROL_ROOT)["active"],
            deployment_id=identity.deployment_id,
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
        )
    except RuntimeControlError as exc:
        raise MaintenanceImportError(
            "maintenance_active_runtime_unverified"
        ) from exc


def _profile():
    try:
        profile = VaultConnectionProfile.objects.get(
            key=settings.VAULT_DEFAULT_PROFILE,
            enabled=True,
        )
    except VaultConnectionProfile.DoesNotExist as exc:
        raise MaintenanceImportError("maintenance_profile_unavailable") from exc
    if profile.dataset_id != settings.ENV_IDENTITY.dataset_id:
        raise MaintenanceImportError("maintenance_dataset_mismatch")
    return profile


def _manifest(job, candidate_manifest, parent, records, total_bytes):
    identity = settings.ENV_IDENTITY
    payload = {
        "manifest_version": 1,
        "origin": ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
        "read_only": True,
        "dataset_id": identity.dataset_id,
        "deployment_id": identity.deployment_id,
        "app_release": identity.app_release_version,
        "image_digest": identity.app_image_digest,
        "files": records,
        "total_bytes": total_bytes,
        "derived_from": {
            "generation_id": parent.generation_id,
            "manifest_digest": parent.manifest_digest,
            "pointer_digest": parent.pointer_digest,
        },
        "maintenance": {
            "job_public_id": str(job.public_id),
            "operation": job.kind,
            "workspace_id": candidate_manifest["workspace_id"],
            "recovery_set_id": candidate_manifest["source"]["recovery_set_id"],
            "validation_basis_digest": candidate_manifest.get("derived", {}).get(
                "manifest_basis_sha256", ""
            ),
        },
        "vault_authority": {
            "state": "unpublished",
            "stale": True,
        },
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    generation_id = f"lm-{job.public_id}-{digest[:12]}"
    return {**payload, "generation_id": generation_id}, digest, generation_id


def import_maintenance_candidate(job, *, actor_id=None, actor_name=""):
    """Prepare one local candidate for the existing signed activation flow."""
    candidate = _resolve_candidate(job)
    candidate_manifest, recovery = _validate_job_and_manifest(job, candidate)
    try:
        validation = validate_candidate(candidate)
    except CandidateMaintenanceError as exc:
        raise MaintenanceImportError(exc.reason_code) from exc
    parent = _active_parent()
    source = candidate_manifest["source"]
    if (
        source.get("runtime_generation_id") != parent.generation_id
        or source.get("runtime_manifest_digest") != parent.manifest_digest
    ):
        raise MaintenanceImportError("maintenance_candidate_parent_stale")
    profile = _profile()
    runtime_root = Path(settings.RUNTIME_GENERATIONS_ROOT)
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise MaintenanceImportError("runtime_root_unsafe")

    with _import_lock(runtime_root):
        existing = ArtifactGeneration.objects.filter(
            origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
            lineage_job_public_id=job.public_id,
        ).first()
        if existing:
            workspace = existing.restore_workspaces.filter(
                state=RestoreWorkspace.State.ACTIVATION_READY
            ).first()
            if not workspace:
                raise MaintenanceImportError(
                    "maintenance_candidate_projection_incomplete"
                )
            validate_runtime_workspace(
                workspace.runtime_path,
                runtime_root=runtime_root,
                generation_id=existing.generation_id,
                manifest_digest=existing.manifest_digest,
            )
            return workspace

        records, total_bytes = _safe_files(candidate)
        incomplete = runtime_root / f".maintenance-{job.public_id}.incomplete"
        if incomplete.exists():
            raise MaintenanceImportError("maintenance_runtime_target_exists")
        incomplete.mkdir(mode=0o700)
        try:
            _copy_records(candidate, incomplete, records)
            rehearsal = rehearse_migrations(
                incomplete / "db.sqlite3",
                workspace_path=incomplete / ".rehearsal-workspace",
                promote_to=incomplete / "db.sqlite3",
            )
            shutil.rmtree(incomplete / ".rehearsal-workspace", ignore_errors=True)
            verify_recovery_superadmin(incomplete / "db.sqlite3")
            post_records, post_bytes = _safe_files(incomplete)
            post_manifest, post_digest, post_generation_id = _manifest(
                job, candidate_manifest, parent, post_records, post_bytes
            )
            manifest = post_manifest
            manifest_digest = post_digest
            generation_id = post_generation_id
            runtime_name = f"{generation_id}-{manifest_digest[:12]}"
            final_runtime = runtime_root / runtime_name
            if final_runtime.exists():
                raise MaintenanceImportError(
                    "maintenance_runtime_target_exists"
                )
            atomic_write_json(
                incomplete / "runtime-evidence.json",
                {
                    "generation_id": generation_id,
                    "manifest_digest": manifest_digest,
                    "origin": ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
                    "parent_generation_id": parent.generation_id,
                    "parent_manifest_digest": parent.manifest_digest,
                    "maintenance_job_public_id": str(job.public_id),
                },
            )
            _make_read_only(incomplete)
            os.replace(incomplete, final_runtime)
            validate_runtime_workspace(
                final_runtime,
                runtime_root=runtime_root,
                generation_id=generation_id,
                manifest_digest=manifest_digest,
            )
        except Exception:
            shutil.rmtree(incomplete, ignore_errors=True)
            raise

        prepared_at = timezone.now()
        try:
            with transaction.atomic(using="control"):
                generation = ArtifactGeneration.objects.create(
                    profile=profile,
                    origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
                    dataset_id=profile.dataset_id,
                    generation_id=generation_id,
                    manifest_digest=manifest_digest,
                    manifest=manifest,
                    vault_state=ArtifactGeneration.VaultState.UNKNOWN,
                    runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
                    local_presence=ArtifactGeneration.LocalPresence.PREPARED,
                    source="maintenance_candidate",
                    lineage_job_public_id=job.public_id,
                    parent_generation_id=parent.generation_id,
                    parent_manifest_digest=parent.manifest_digest,
                    observed_at=prepared_at,
                )
                ArtifactValidation.objects.create(
                    generation=generation,
                    validation_type="maintenance_candidate_import",
                    status=ArtifactValidation.Status.PASSED,
                    manifest_digest=manifest_digest,
                    validator_version="vaultops-maintenance-import/v1",
                    evidence={
                        "candidate": validation,
                        "recovery_set_id": recovery["set_id"],
                        "origin": ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
                    },
                    validated_by_id=actor_id,
                    validated_by_name=actor_name,
                )
                workspace = RestoreWorkspace.objects.create(
                    generation=generation,
                    state=RestoreWorkspace.State.ACTIVATION_READY,
                    manifest_digest=manifest_digest,
                    profile_fingerprint=profile.fingerprint,
                    pointer_digest=parent.pointer_digest,
                    runtime_path=str(final_runtime),
                    validation_evidence={
                        "candidate": validation,
                        "origin": ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
                    },
                    rehearsal_evidence=rehearsal,
                    prepared_at=prepared_at,
                    expires_at=datetime.fromisoformat(
                        candidate_manifest["expires_at"]
                    ),
                )
                correlation_id = uuid.uuid4()
                append_event(
                    action="maintenance_candidate_prepared",
                    result="succeeded",
                    correlation_id=correlation_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    after_state={
                        "workspace_state": workspace.state,
                        "runtime_state": generation.runtime_state,
                    },
                    evidence={
                        "maintenance_job_public_id": str(job.public_id),
                        "generation_id": generation_id,
                        "manifest_digest": manifest_digest,
                        "origin": generation.origin,
                        "vault_authority_changed": False,
                    },
                )
        except Exception:
            _remove_runtime(final_runtime)
            raise
        return workspace
