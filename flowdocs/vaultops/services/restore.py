"""Verified, resumable restore into quarantine and immutable runtime storage."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import stat
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.compatibility import check_generation_compatibility
from core.environment_policy import EnvironmentDirectionPolicy, Operation
from core.rehearsal import RehearsalError, rehearse_migrations
from core.sanitize import (
    provision_recovery_superadmin,
    sanitize_database,
    validate_sanitization,
)
from vaultops.models import (
    ArtifactGeneration,
    ArtifactValidation,
    RestoreWorkspace,
    VaultJob,
    VaultJobStep,
)
from vaultops.services.audit import append_event
from vaultops.services.inventory import (
    InventoryError,
    project_verified_generation,
    verify_generation,
)
from vaultops.services.lifecycle import transition_workspace
from vaultops.services.profiles import profile_fingerprint, vault_for_profile


class RestoreError(RuntimeError):
    reason_code = "restore_failed"
    retryable = False

    def __init__(self, reason_code=None, *, retryable=None):
        self.reason_code = reason_code or self.reason_code
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.reason_code)


class RestoreCancelled(RestoreError):
    reason_code = "restore_cancelled"
    retryable = True


def _guard_restore_direction(profile):
    if not profile.read_only:
        raise RestoreError("restore_profile_read_only_required")
    direction = EnvironmentDirectionPolicy.from_identity(
        settings.ENV_IDENTITY
    ).decision(Operation.RESTORE, remote_dataset_id=profile.dataset_id)
    if not direction.allowed:
        raise RestoreError(direction.reason_code)


def queue_restore_job(
    *,
    profile,
    idempotency_key,
    generation_id="",
    requested_by_id=None,
    requested_by_name="",
):
    """Queue a restore without resolving or persisting profile credentials."""
    if not settings.VAULT_RESTORE_ENABLED:
        raise RestoreError("vault_restore_disabled")
    if not settings.VAULT_ADMIN_MUTATIONS_ENABLED:
        raise RestoreError("vault_admin_mutations_disabled")
    if not idempotency_key:
        raise RestoreError("idempotency_key_required")
    fingerprint = profile_fingerprint(profile)
    if not profile.enabled or profile.fingerprint != fingerprint:
        raise RestoreError("profile_fingerprint_changed")
    _guard_restore_direction(profile)
    with transaction.atomic(using="control"):
        job, created = VaultJob.objects.select_for_update().get_or_create(
            operation="restore_generation",
            idempotency_key=idempotency_key,
            defaults={
                "profile": profile,
                "profile_fingerprint": fingerprint,
                "dataset_id": profile.dataset_id,
                "generation_id": generation_id,
                "requested_by_id": requested_by_id,
                "requested_by_name": requested_by_name,
                "progress": {
                    "requested_generation": (
                        generation_id or "authoritative"
                    )
                },
            },
        )
        if not created and (
            job.profile_id != profile.pk
            or job.profile_fingerprint != fingerprint
            or job.dataset_id != profile.dataset_id
            or job.generation_id != generation_id
        ):
            raise RestoreError("idempotency_conflict")
        if created:
            append_event(
                action="job_queued",
                result="succeeded",
                correlation_id=job.correlation_id,
                actor_id=requested_by_id,
                actor_name=requested_by_name,
                job_public_id=job.public_id,
                after_state={
                    "job_state": job.status,
                    "operation": job.operation,
                },
                evidence={
                    "requested_generation": (
                        generation_id or "authoritative"
                    )
                },
            )
    return job


def _safe_root(configured_root):
    configured = Path(configured_root)
    configured.mkdir(parents=True, exist_ok=True, mode=0o700)
    if configured.is_symlink() or not configured.is_dir():
        raise RestoreError("restore_root_unsafe")
    root = configured.resolve()
    return root


def _capacity(root, required_bytes):
    from core.artifact_cleanup import capacity_report

    evidence = capacity_report(
        source_bytes=int(required_bytes),
        operation="restore",
        target_root=root,
        minimum_free_bytes=int(settings.VAULT_RESTORE_MIN_FREE_BYTES),
        minimum_free_inodes=int(settings.VAULT_RESTORE_MIN_FREE_INODES),
    )
    evidence.update(
        {
            "available_bytes": evidence["free_bytes"],
            "required_inodes": evidence["inode_reserve"],
            "available_inodes": evidence["free_inodes"],
        }
    )
    if not evidence["byte_capacity_ok"]:
        raise RestoreError("restore_capacity_bytes_insufficient")
    if (
        evidence["inode_check"] == "not_reported"
        and int(settings.VAULT_RESTORE_MIN_FREE_INODES) > 0
    ):
        raise RestoreError("restore_capacity_inodes_unknown")
    if not evidence["inode_capacity_ok"]:
        raise RestoreError("restore_capacity_inodes_insufficient")
    return evidence


def _sha256_file(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _target_path(root, relative_path):
    root = root.resolve()
    parts = relative_path.split("/")
    current = root
    for part in parts[:-1]:
        candidate = current / part
        if os.path.lexists(candidate) and (
            candidate.is_symlink() or not candidate.is_dir()
        ):
            raise RestoreError("restore_path_unsafe")
        candidate.mkdir(mode=0o700, exist_ok=True)
        current = candidate
    target = current / parts[-1]
    if os.path.lexists(target) and target.is_symlink():
        raise RestoreError("restore_path_unsafe")
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise RestoreError("restore_path_unsafe") from exc
    return target


def _download_object(vault, entry, target):
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise RestoreError("restore_path_unsafe")
        digest, size = _sha256_file(target)
        if digest != entry["sha256"] or size != entry["bytes"]:
            raise RestoreError("restore_checkpoint_digest_mismatch")
        return "reused"
    temporary = target.with_name(
        f".{target.name}.{secrets.token_hex(6)}.partial"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(temporary, flags, 0o600)
    digest = hashlib.sha256()
    size = 0
    try:
        try:
            response = vault.client.get_object(
                Bucket=vault.config.bucket,
                Key=entry["object_key"],
            )
            body = response["Body"]
            with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
                file_descriptor = -1
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > entry["bytes"]:
                        raise RestoreError("restore_object_size_mismatch")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except RestoreError:
            raise
        except Exception as exc:
            raise RestoreError(
                "restore_object_read_failed", retryable=True
            ) from exc
        if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
            raise RestoreError("restore_object_digest_mismatch")
        os.replace(temporary, target)
        os.chmod(target, 0o440)
        return "downloaded"
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)


def _validate_database(database_path):
    try:
        connection_value = sqlite3.connect(
            f"file:{database_path}?mode=ro", uri=True
        )
        integrity = connection_value.execute(
            "PRAGMA integrity_check"
        ).fetchone()
        foreign_keys = connection_value.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreError("restored_database_invalid") from exc
    finally:
        if "connection_value" in locals():
            connection_value.close()
    if not integrity or integrity[0] != "ok":
        raise RestoreError("restored_database_integrity_failed")
    if foreign_keys:
        raise RestoreError("restored_database_foreign_keys_failed")


def _validate_download(root, verified):
    category_counts = {}
    for entry in verified.manifest["files"]:
        target = _target_path(root, entry["path"])
        if target.is_symlink() or not target.is_file():
            raise RestoreError("restored_object_missing")
        file_stat = os.stat(target, follow_symlinks=False)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise RestoreError("restored_object_unsafe")
        digest, size = _sha256_file(target)
        if digest != entry["sha256"] or size != entry["bytes"]:
            raise RestoreError("restored_object_digest_mismatch")
        category = (
            "database"
            if entry["path"] == "db.sqlite3"
            else entry["path"].split("/", 1)[0]
        )
        category_counts[category] = category_counts.get(category, 0) + 1
    _validate_database(root / "db.sqlite3")
    expected_faiss = verified.manifest.get("faiss", {}).get(
        "file_count",
        verified.manifest.get("faiss", {}).get("count"),
    )
    if expected_faiss != category_counts.get("faiss_indexes", 0):
        raise RestoreError("restored_faiss_count_mismatch")
    return {
        "manifest_digest": verified.manifest_digest,
        "files_verified": verified.file_count,
        "bytes_verified": verified.byte_count,
        "category_counts": category_counts,
        "database_integrity": "ok",
        "foreign_keys": "ok",
    }


def _make_read_only(root):
    for directory, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(directory) / name, 0o440)
        for name in directories:
            os.chmod(Path(directory) / name, 0o550)
    os.chmod(root, 0o550)


def _copy_quarantine(source, target):
    if target.exists():
        raise RestoreError("runtime_workspace_exists")
    for directory, directories, files in os.walk(source, followlinks=False):
        for name in directories:
            if (Path(directory) / name).is_symlink():
                raise RestoreError("restore_path_unsafe")
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or candidate.stat().st_nlink != 1:
                raise RestoreError("restored_object_unsafe")
    shutil.copytree(source, target, symlinks=False)
    for directory, directories, files in os.walk(target):
        os.chmod(directory, 0o700)
        for name in directories:
            os.chmod(Path(directory) / name, 0o700)
        for name in files:
            os.chmod(Path(directory) / name, 0o600)


def _requires_sanitization(verified):
    production_derived = (
        verified.registration.get("production_source_id") == "ai-sahakar-prod"
        or verified.registration.get("dataset_id") == "ai-sahakar-prod"
    )
    return production_derived and not settings.ENV_IDENTITY.is_production


def _workspace_for(job, generation, verified, quarantine_root):
    workspace = (
        RestoreWorkspace.objects.filter(
            job=job,
            generation=generation,
            manifest_digest=verified.manifest_digest,
            profile_fingerprint=job.profile_fingerprint,
        )
        .exclude(
            state__in=[
                RestoreWorkspace.State.FAILED,
                RestoreWorkspace.State.EXPIRED,
            ]
        )
        .order_by("-created_at")
        .first()
    )
    if workspace:
        if workspace.pointer_digest != verified.pointer_digest:
            raise RestoreError("restore_source_pointer_changed")
        return workspace
    workspace = RestoreWorkspace.objects.create(
        job=job,
        generation=generation,
        manifest_digest=verified.manifest_digest,
        profile_fingerprint=job.profile_fingerprint,
        pointer_digest=verified.pointer_digest,
    )
    workspace.quarantine_path = str(
        quarantine_root / str(workspace.public_id) / "quarantine"
    )
    workspace.save(update_fields=["quarantine_path", "updated_at"])
    return workspace


def _fail_workspace(workspace, reason_code, *, correlation_id):
    workspace.refresh_from_db()
    workspace.safe_error_code = reason_code
    workspace.save(update_fields=["safe_error_code", "updated_at"])
    if workspace.state not in {
        RestoreWorkspace.State.FAILED,
        RestoreWorkspace.State.EXPIRED,
        RestoreWorkspace.State.ACTIVATION_READY,
    }:
        transition_workspace(
            workspace,
            RestoreWorkspace.State.FAILED,
            correlation_id=correlation_id,
            reason_code=reason_code,
        )


def run_restore_job(
    job,
    *,
    vault=None,
    cancellation_check=None,
    heartbeat=None,
    run_rehearsal=True,
):
    """Verify, download, sanitize, rehearse, and prepare without activation."""
    if not settings.VAULT_RESTORE_ENABLED:
        raise RestoreError("vault_restore_disabled")
    if not job.profile_id:
        raise RestoreError("profile_required")
    if job.dataset_id != job.profile.dataset_id:
        raise RestoreError("profile_identity_mismatch")
    if job.profile_fingerprint != profile_fingerprint(job.profile):
        raise RestoreError("profile_fingerprint_changed")
    if not job.profile.enabled:
        raise RestoreError("profile_fingerprint_changed")
    _guard_restore_direction(job.profile)
    cancellation_check = cancellation_check or (lambda: False)
    heartbeat = heartbeat or (lambda **kwargs: None)
    vault = vault or vault_for_profile(
        job.profile,
        expected_fingerprint=job.profile_fingerprint,
    )
    try:
        verified = verify_generation(
            vault,
            job.profile,
            generation_id=job.generation_id,
            verify_objects=True,
        )
    except InventoryError:
        raise
    _, generation = project_verified_generation(job.profile, verified)
    compatibility = check_generation_compatibility(
        verified.manifest,
        app_release=settings.ENV_IDENTITY.app_release_version,
        image_digest=settings.ENV_IDENTITY.app_image_digest,
        allow_repacked_release_mismatch=(
            settings.VAULT_RESTORE_ALLOW_REPACKED_RELEASE_MISMATCH
        ),
    )
    if not compatibility.compatible:
        raise RestoreError("generation_compatibility_failed")
    if _requires_sanitization(verified) and not (
        settings.VAULT_RESTORE_REQUIRE_SANITIZATION
    ):
        raise RestoreError("production_restore_sanitization_required")

    quarantine_root = _safe_root(settings.VAULT_RESTORE_ROOT)
    runtime_root = _safe_root(settings.RUNTIME_GENERATIONS_ROOT)
    workspace = _workspace_for(
        job, generation, verified, quarantine_root
    )
    quarantine = Path(workspace.quarantine_path)
    try:
        capacity = _capacity(
            quarantine_root,
            max(verified.byte_count, 1),
        )
        workspace.capacity_plan = capacity
        workspace.save(update_fields=["capacity_plan", "updated_at"])
        if workspace.state in {
            RestoreWorkspace.State.PLANNED,
            RestoreWorkspace.State.DOWNLOAD_PAUSED,
        }:
            workspace = transition_workspace(
                workspace,
                RestoreWorkspace.State.DOWNLOADING,
                correlation_id=job.correlation_id,
            )
        quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        step, _ = VaultJobStep.objects.get_or_create(
            job=job,
            phase="restore_download",
            defaults={
                "status": VaultJobStep.Status.RUNNING,
                "started_at": timezone.now(),
            },
        )
        checkpoints = dict(step.checkpoint.get("objects", {}))
        completed_bytes = 0
        for index, entry in enumerate(verified.manifest["files"], start=1):
            if cancellation_check():
                workspace = transition_workspace(
                    workspace,
                    RestoreWorkspace.State.DOWNLOAD_PAUSED,
                    correlation_id=job.correlation_id,
                    reason_code="restore_cancelled",
                )
                raise RestoreCancelled()
            target = _target_path(quarantine, entry["path"])
            disposition = _download_object(vault, entry, target)
            checkpoints[entry["sha256"]] = {
                "path": entry["path"],
                "bytes": entry["bytes"],
                "disposition": disposition,
            }
            completed_bytes += entry["bytes"]
            step.status = VaultJobStep.Status.RUNNING
            step.checkpoint = {"objects": checkpoints}
            step.completed_objects = index
            step.completed_bytes = completed_bytes
            step.save(
                update_fields=[
                    "status",
                    "checkpoint",
                    "completed_objects",
                    "completed_bytes",
                    "updated_at",
                ]
            )
            workspace.downloaded_objects = index
            workspace.downloaded_bytes = completed_bytes
            workspace.save(
                update_fields=[
                    "downloaded_objects",
                    "downloaded_bytes",
                    "updated_at",
                ]
            )
            heartbeat(
                phase="restore_downloading",
                progress={
                    "objects_completed": index,
                    "objects_total": verified.file_count,
                    "bytes_completed": completed_bytes,
                },
            )
        step.status = VaultJobStep.Status.COMPLETED
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "finished_at", "updated_at"])
        workspace = transition_workspace(
            workspace,
            RestoreWorkspace.State.DOWNLOADED,
            correlation_id=job.correlation_id,
        )
        workspace = transition_workspace(
            workspace,
            RestoreWorkspace.State.VALIDATING,
            correlation_id=job.correlation_id,
        )
        validation_evidence = _validate_download(quarantine, verified)
        validation_evidence["compatibility_checks"] = compatibility.checks
        validation_evidence["unavailable_documents"] = verified.manifest[
            "unavailable_documents"
        ]
        ArtifactValidation.objects.create(
            generation=generation,
            validation_type="restore_preparation",
            status=ArtifactValidation.Status.PASSED,
            manifest_digest=verified.manifest_digest,
            validator_version="vaultops-restore/v1",
            evidence=validation_evidence,
            validated_by_id=job.requested_by_id,
            validated_by_name=job.requested_by_name,
        )
        workspace.validation_evidence = validation_evidence
        workspace.save(
            update_fields=["validation_evidence", "updated_at"]
        )
        _make_read_only(quarantine)
        generation.local_presence = ArtifactGeneration.LocalPresence.QUARANTINED
        generation.save(update_fields=["local_presence", "updated_at"])

        _capacity(runtime_root, max(verified.byte_count, 1))
        runtime_name = (
            f"{verified.generation_id}-{verified.manifest_digest[:12]}"
        )
        incomplete = runtime_root / f".{runtime_name}.incomplete"
        final_runtime = runtime_root / runtime_name
        if final_runtime.exists():
            raise RestoreError("runtime_workspace_exists")
        _copy_quarantine(quarantine, incomplete)
        prepared_database = incomplete / "db.sqlite3"
        if _requires_sanitization(verified):
            workspace = transition_workspace(
                workspace,
                RestoreWorkspace.State.SANITIZING,
                correlation_id=job.correlation_id,
            )
            sanitized_database = incomplete / ".db.sanitized.sqlite3"
            sanitization = sanitize_database(
                prepared_database,
                sanitized_database,
                dataset_id=verified.manifest["dataset_id"],
            )
            os.replace(sanitized_database, prepared_database)
            recovery_username = (
                settings.ACTIVATION_RECOVERY_SUPERADMIN_USERNAME
            )
            recovery_password = (
                settings.ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD
            )
            if recovery_username or recovery_password:
                try:
                    recovery_evidence = provision_recovery_superadmin(
                        prepared_database,
                        username=recovery_username,
                        password=recovery_password,
                    )
                except ValueError as exc:
                    raise RestoreError(str(exc)) from exc
                sanitization["stats"][
                    "recovery_superadmin_provisioned"
                ] = bool(recovery_evidence["provisioned"])
            issues = validate_sanitization(
                prepared_database,
                recovery_username=recovery_username,
            )
            if issues:
                raise RestoreError("restore_sanitization_validation_failed")
            workspace.sanitization_evidence = {
                "policy_version": sanitization["policy_version"],
                "validated": True,
                "stats": sanitization["stats"],
            }
            workspace.save(
                update_fields=["sanitization_evidence", "updated_at"]
            )
        if run_rehearsal:
            workspace = transition_workspace(
                workspace,
                RestoreWorkspace.State.MIGRATION_REHEARSAL,
                correlation_id=job.correlation_id,
            )
            rehearsal_root = quarantine.parent / "rehearsal-workspace"
            rehearsal = rehearse_migrations(
                prepared_database,
                workspace_path=rehearsal_root,
                promote_to=prepared_database,
            )
            workspace.rehearsal_evidence = rehearsal
            workspace.save(
                update_fields=["rehearsal_evidence", "updated_at"]
            )
        for directory_name in (
            "media",
            "pdf_cache",
            "faiss_indexes",
            "chroma_db",
        ):
            (incomplete / directory_name).mkdir(
                parents=True, exist_ok=True, mode=0o700
            )
        evidence = {
            "generation_id": verified.generation_id,
            "manifest_digest": verified.manifest_digest,
            "profile_fingerprint": job.profile_fingerprint,
            "sanitized": bool(workspace.sanitization_evidence),
            "static_assets_posture": "custody_only",
        }
        evidence_path = incomplete / "runtime-evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(evidence_path, 0o440)
        _make_read_only(incomplete)
        os.replace(incomplete, final_runtime)
        workspace.runtime_path = str(final_runtime)
        workspace.prepared_at = timezone.now()
        workspace.save(
            update_fields=["runtime_path", "prepared_at", "updated_at"]
        )
        workspace = transition_workspace(
            workspace,
            RestoreWorkspace.State.ACTIVATION_READY,
            correlation_id=job.correlation_id,
        )
        generation.local_presence = ArtifactGeneration.LocalPresence.PREPARED
        generation.save(update_fields=["local_presence", "updated_at"])
        append_event(
            action="restore_prepared",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            after_state={
                "workspace_state": workspace.state,
                "local_presence": generation.local_presence,
            },
            evidence={
                "workspace_id": str(workspace.public_id),
                "manifest_digest": verified.manifest_digest,
            },
        )
        return workspace
    except RestoreCancelled:
        raise
    except RehearsalError as exc:
        shutil.rmtree(incomplete, ignore_errors=True)
        _fail_workspace(
            workspace,
            exc.reason_code,
            correlation_id=job.correlation_id,
        )
        raise RestoreError(exc.reason_code) from exc
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", "restore_failed")
        if getattr(exc, "retryable", False) and workspace.state == (
            RestoreWorkspace.State.DOWNLOADING
        ):
            transition_workspace(
                workspace,
                RestoreWorkspace.State.DOWNLOAD_PAUSED,
                correlation_id=job.correlation_id,
                reason_code=reason_code,
            )
        else:
            if "incomplete" in locals():
                shutil.rmtree(incomplete, ignore_errors=True)
            _fail_workspace(
                workspace,
                reason_code,
                correlation_id=job.correlation_id,
            )
        raise
