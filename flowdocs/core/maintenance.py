"""Durable maintenance queue shared by the admin UI and management command."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .artifact_vault import ArtifactVault
from .management.commands.inventory_artifacts import build_manifest
from .models import ArtifactGeneration, Folder, MaintenanceJob, MaintenanceJobItem, PDFFile
from .utils import SearchDataIntegrityError, build_or_load_faiss_index_for_folder, precompute_pdf_embeddings


def _apply_retention_expiry(generation):
    """Stamp expires_at based on retention_policy and retention_value."""
    now = timezone.now()
    if generation.retention_policy == "age_based" and generation.retention_value:
        from datetime import timedelta
        generation.expires_at = now + timedelta(days=generation.retention_value)
    elif generation.retention_policy == "keep_last_n":
        generation.expires_at = None
    else:
        generation.expires_at = None
    generation.save(update_fields=["expires_at"])


def promote_active_generation(generation_id, *, requested_by=None):
    """Atomically promote a validated generation to active, superseding the prior active."""
    from django.db import transaction
    try:
        generation = ArtifactGeneration.objects.get(generation_id=generation_id)
    except ArtifactGeneration.DoesNotExist:
        raise SearchDataIntegrityError(f"Generation {generation_id} does not exist")
    if generation.status not in ("validated", "active"):
        raise SearchDataIntegrityError(
            f"Generation {generation_id} must be validated before promotion (current: {generation.status})"
        )
    with transaction.atomic():
        prior = ArtifactGeneration.objects.filter(status="active").exclude(pk=generation.pk).first()
        if prior is not None:
            prior.status = "superseded"
            prior.superseded_by = generation
            prior.save(update_fields=["status", "superseded_by", "updated_at"] if hasattr(prior, "updated_at") else ["status", "superseded_by"])
        generation.status = "active"
        generation.promoted_at = timezone.now()
        _apply_retention_expiry(generation)
        generation.save(update_fields=["status", "promoted_at", "expires_at"])
    return generation


def rollback_to_generation(generation_id, *, requested_by=None):
    """Re-stage a prior generation and promote it as the new active."""
    try:
        generation = ArtifactGeneration.objects.get(generation_id=generation_id)
    except ArtifactGeneration.DoesNotExist:
        raise SearchDataIntegrityError(f"Generation {generation_id} does not exist")
    if generation.status not in ("superseded", "validated", "active", "staged"):
        raise SearchDataIntegrityError(
            f"Generation {generation_id} cannot be rolled back to (current: {generation.status})"
        )
    if generation.status != "validated":
        generation.status = "validated"
        generation.validated_at = timezone.now()
        generation.save(update_fields=["status", "validated_at"])
    return promote_active_generation(generation_id, requested_by=requested_by)


def purge_expired_generations(*, requested_by=None):
    """Delete generations past their retention window that are not active or the immediate predecessor of active."""
    now = timezone.now()
    active = ArtifactGeneration.objects.filter(status="active").first()
    immediate_predecessor_id = active.superseded_by_id if active else None
    expired = ArtifactGeneration.objects.filter(
        expires_at__isnull=False,
        expires_at__lt=now,
    ).exclude(status="active")
    if immediate_predecessor_id:
        expired = expired.exclude(pk=immediate_predecessor_id)
    purged_ids = list(expired.values_list("generation_id", flat=True))
    expired.update(status="purged")
    return purged_ids


def purge_generation(generation_id, *, requested_by=None):
    """Manually purge a single non-active generation."""
    try:
        generation = ArtifactGeneration.objects.get(generation_id=generation_id)
    except ArtifactGeneration.DoesNotExist:
        raise SearchDataIntegrityError(f"Generation {generation_id} does not exist")
    if generation.status == "active":
        raise SearchDataIntegrityError("Cannot purge the active generation")
    if generation.status == "purged":
        return generation
    generation.status = "purged"
    generation.save(update_fields=["status"])
    return generation


def queue_job(*, kind: str, requested_by, pdfs=None, folders=None, scope=None, options=None):
    """Create a resumable job and materialize its initial item set."""
    pdfs = list(pdfs or [])
    folders = list(folders or [])
    with transaction.atomic():
        job = MaintenanceJob.objects.create(
            kind=kind,
            requested_by=requested_by,
            scope=scope or {},
            options=options or {},
            total_items=len(pdfs) or len(folders) or (1 if kind == "sync_generation" else 0),
        )
        if pdfs:
            MaintenanceJobItem.objects.bulk_create([
                MaintenanceJobItem(job=job, pdf=pdf, folder_id=pdf.folder_id)
                for pdf in pdfs
            ])
        elif folders:
            MaintenanceJobItem.objects.bulk_create([
                MaintenanceJobItem(job=job, folder=folder)
                for folder in folders
            ])
    return job


def claim_next_job():
    """Claim one queued job; the conditional update prevents duplicate workers."""
    with transaction.atomic():
        job = MaintenanceJob.objects.filter(status="queued").order_by("created_at").first()
        if job is None:
            return None
        changed = MaintenanceJob.objects.filter(pk=job.pk, status="queued").update(
            status="running", started_at=timezone.now()
        )
        if not changed:
            return None
        return MaintenanceJob.objects.get(pk=job.pk)


def run_job(job: MaintenanceJob) -> MaintenanceJob:
    """Run a claimed job and preserve partial results for retry/reporting."""
    if job.status == "queued":
        job.status = "running"
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
    if job.status != "running":
        return job

    if job.kind == "sync_generation":
        try:
            generation = sync_active_generation(requested_by=job.requested_by)
        except Exception as exc:
            job.status = "failed"
            job.failed_items = 1
            job.error_summary = str(exc)[:2000]
        else:
            job.status = "completed"
            job.completed_items = 1
            job.options = {**job.options, "generation_id": generation.generation_id}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "failed_items", "completed_items", "error_summary", "options", "finished_at", "updated_at"])
        return job

    if job.kind == "restore_generation":
        try:
            generation_id = job.options.get("generation_id", "")
            generation = stage_generation(generation_id, requested_by=job.requested_by)
        except Exception as exc:
            job.status = "failed"
            job.failed_items = 1
            job.error_summary = str(exc)[:2000]
        else:
            job.status = "completed"
            job.completed_items = 1
            job.options = {**job.options, "generation_id": generation.generation_id, "staged": True}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "failed_items", "completed_items", "error_summary", "options", "finished_at", "updated_at"])
        return job

    if job.kind == "promote_generation":
        try:
            generation_id = job.options.get("generation_id", "")
            generation = promote_active_generation(generation_id, requested_by=job.requested_by)
        except Exception as exc:
            job.status = "failed"
            job.failed_items = 1
            job.error_summary = str(exc)[:2000]
        else:
            job.status = "completed"
            job.completed_items = 1
            job.options = {**job.options, "generation_id": generation.generation_id, "promoted": True}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "failed_items", "completed_items", "error_summary", "options", "finished_at", "updated_at"])
        return job

    if job.kind == "rollback_generation":
        try:
            generation_id = job.options.get("generation_id", "")
            generation = rollback_to_generation(generation_id, requested_by=job.requested_by)
        except Exception as exc:
            job.status = "failed"
            job.failed_items = 1
            job.error_summary = str(exc)[:2000]
        else:
            job.status = "completed"
            job.completed_items = 1
            job.options = {**job.options, "generation_id": generation.generation_id, "rolled_back": True}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "failed_items", "completed_items", "error_summary", "options", "finished_at", "updated_at"])
        return job

    if job.kind == "purge_generation":
        try:
            generation_id = job.options.get("generation_id", "")
            if generation_id:
                purge_generation(generation_id, requested_by=job.requested_by)
                purged = [generation_id]
            else:
                purged = purge_expired_generations(requested_by=job.requested_by)
        except Exception as exc:
            job.status = "failed"
            job.failed_items = 1
            job.error_summary = str(exc)[:2000]
        else:
            job.status = "completed"
            job.completed_items = len(purged)
            job.options = {**job.options, "purged_ids": purged}
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "failed_items", "completed_items", "error_summary", "options", "finished_at", "updated_at"])
        return job

    for item in job.items.filter(status__in=("queued", "failed")).order_by("pk"):
        job.refresh_from_db(fields=["status"])
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at", "updated_at"])
            return job

        item.status = "running"
        item.attempts += 1
        item.started_at = timezone.now()
        item.error_code = ""
        item.error_message = ""
        item.save(update_fields=["status", "attempts", "started_at", "error_code", "error_message"])
        try:
            if job.kind in {"reindex_needed", "reindex_all"}:
                if item.pdf is None:
                    raise SearchDataIntegrityError("PDF no longer exists")
                precompute_pdf_embeddings(item.pdf)
            elif job.kind == "repair_indexes":
                if item.folder is None:
                    raise SearchDataIntegrityError("Category no longer exists")
                _repair_folder(item.folder)
            elif job.kind == "validate":
                _validate_pdf(item.pdf)
            else:
                raise SearchDataIntegrityError(
                    f"Job kind '{job.kind}' requires the generation worker"
                )
        except Exception as exc:
            if (
                job.kind in {"reindex_needed", "reindex_all"}
                and item.pdf is not None
                and _has_stored_artifacts(item.pdf)
            ):
                try:
                    _repair_folder(item.folder)
                except Exception:
                    pass
                else:
                    item.status = "completed"
                    item.error_code = "stored_artifact_repair"
                    item.error_message = "Rebuilt from stored OCR/search artifacts"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "error_code", "error_message", "finished_at"])
                    job.completed_items += 1
                    job.save(update_fields=["completed_items", "updated_at"])
                    continue
            item.status = "failed"
            item.error_code = _error_code(exc)
            item.error_message = str(exc)[:2000]
            item.finished_at = timezone.now()
            item.save(update_fields=["status", "error_code", "error_message", "finished_at"])
            MaintenanceJob.objects.filter(pk=job.pk).update(
                failed_items=job.failed_items + 1,
                error_summary=item.error_message,
                updated_at=timezone.now(),
            )
            job.failed_items += 1
        else:
            item.status = "completed"
            item.finished_at = timezone.now()
            item.save(update_fields=["status", "finished_at"])
            job.completed_items += 1

        job.save(update_fields=["completed_items", "failed_items", "updated_at"])

    job.refresh_from_db()
    job.status = "failed" if job.failed_items else "completed"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])
    return job


def sync_active_generation(*, requested_by=None):
    """Upload a content-addressed active snapshot and register its manifest."""
    vault = ArtifactVault()
    if not vault.enabled:
        raise SearchDataIntegrityError("Artifact vault is disabled")

    generation_id = timezone.now().strftime("gen-%Y%m%dT%H%M%S-") + secrets.token_hex(4)
    root = Path(settings.DATA_ROOT).resolve()
    database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()
    if not database_path.is_file():
        raise SearchDataIntegrityError("Configured SQLite database is missing")

    # SQLite backup produces a consistent point-in-time copy without stopping
    # the web or worker processes. The temporary file is never registered as
    # live application data and is removed after the immutable upload.
    with tempfile.TemporaryDirectory(prefix="pdfsearch-sync-", dir=settings.BACKUP_DIR) as temporary_dir:
        snapshot_path = Path(temporary_dir) / "db.sqlite3"
        source = sqlite3.connect(database_path)
        snapshot = sqlite3.connect(snapshot_path)
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
            source.close()

        manifest = build_manifest(
            root,
            database_path=snapshot_path,
            media_root=settings.MEDIA_ROOT,
            faiss_root=settings.FAISS_INDEX_DIR,
            chroma_root=settings.CHROMA_DIR,
            static_root=settings.STATIC_ROOT,
        )
        files = [{
            "path": "db.sqlite3",
            "bytes": snapshot_path.stat().st_size,
            "sha256": _file_sha256(snapshot_path),
            "object_key": vault.database_object_key(generation_id),
            "artifact_type": "database",
        }]
        for entry in manifest.get("pdf_storage", {}).get("files", []):
            path = root / entry["path"]
            files.append({**entry, "bytes": entry["size_bytes"], "object_key": vault.pdf_object_key(entry["sha256"]), "artifact_type": "pdf"})
            _upload_file(vault, path, files[-1])
        for entry in manifest.get("faiss", {}).get("files", []):
            path = root / entry["path"]
            folder_id = int(Path(entry["path"]).stem.split("_")[-1])
            record = {**entry, "bytes": entry["size_bytes"], "object_key": vault.faiss_object_key(generation_id, folder_id), "artifact_type": "faiss"}
            files.append(record)
            _upload_file(vault, path, record)
        _upload_file(vault, snapshot_path, files[0])
        payload = {
            "release_id": generation_id,
            "manifest_version": 1,
            "read_only": True,
            "source": "active-data-root",
            "counts": manifest.get("counts", {}),
            "files": files,
        }
        vault.put_manifest(payload, release_id=generation_id)
        return ArtifactGeneration.objects.create(
            generation_id=generation_id,
            status="validated",
            manifest=payload,
            source="s3",
            created_by=requested_by,
            validated_at=timezone.now(),
        )


def stage_generation(generation_id: str, *, requested_by=None):
    """Download and verify one immutable generation into a quarantine directory."""
    vault = ArtifactVault()
    if not vault.enabled:
        raise SearchDataIntegrityError("Artifact vault is disabled")
    if not generation_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in generation_id):
        raise SearchDataIntegrityError("Generation id is missing or unsafe")
    payload = json.loads(vault.get_manifest(generation_id))
    manifest = vault.normalize_manifest(payload, release_id=generation_id)
    staging_root = Path(settings.BACKUP_DIR) / "staged-generations" / generation_id
    artifacts_root = staging_root / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        object_key = entry["object_key"]
        data = vault.get(object_key, expected_sha256=entry["sha256"])
        target = artifacts_root / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (staging_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return ArtifactGeneration.objects.update_or_create(
        generation_id=generation_id,
        defaults={
            "status": "validated",
            "manifest": manifest,
            "source": "s3-staged",
            "created_by": requested_by,
            "validated_at": timezone.now(),
        },
    )[0]


def _upload_file(vault, path: Path, record):
    if not path.is_file():
        raise SearchDataIntegrityError(f"Artifact is missing: {record.get('path')}")
    payload = path.read_bytes()
    if len(payload) != record["bytes"] or _bytes_sha256(payload) != record["sha256"]:
        raise SearchDataIntegrityError(f"Artifact changed during sync: {record.get('path')}")
    vault.put(record["object_key"], payload, expected_sha256=record["sha256"])


def _file_sha256(path: Path):
    return _bytes_sha256(path.read_bytes())


def _bytes_sha256(payload: bytes):
    return hashlib.sha256(payload).hexdigest()


def _repair_folder(folder):
    eligible = [
        pdf.pk for pdf in PDFFile.objects.filter(folder=folder)
        if isinstance(pdf.page_chunks, list)
        and isinstance(pdf.chunk_embeddings, list)
        and bool(pdf.page_chunks)
        and len(pdf.page_chunks) == len(pdf.chunk_embeddings)
    ]
    if not eligible:
        raise SearchDataIntegrityError("No stored chunks or embeddings are available")
    index, _, _ = build_or_load_faiss_index_for_folder(folder, force_rebuild=True)
    if index is None:
        raise SearchDataIntegrityError("No searchable artifacts were available")
    PDFFile.objects.filter(pk__in=eligible).update(indexed=True)


def _has_stored_artifacts(pdf):
    return (
        isinstance(pdf.page_chunks, list)
        and isinstance(pdf.chunk_embeddings, list)
        and bool(pdf.page_chunks)
        and len(pdf.page_chunks) == len(pdf.chunk_embeddings)
    )


def _validate_pdf(pdf):
    if pdf is None or not pdf.file:
        raise SearchDataIntegrityError("PDF file is unavailable")
    if not pdf.file.storage.exists(pdf.file.name):
        raise SearchDataIntegrityError("PDF file is missing from configured storage")


def _error_code(exc: Exception) -> str:
    if isinstance(exc, SearchDataIntegrityError):
        return "integrity"
    return exc.__class__.__name__.lower()[:64]
