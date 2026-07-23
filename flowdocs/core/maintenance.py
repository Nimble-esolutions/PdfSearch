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
from .models import ArtifactGeneration, ArtifactValidation, Folder, MaintenanceAuditEvent, MaintenanceJob, MaintenanceJobItem, PDFFile
from .utils import SearchDataIntegrityError, build_or_load_faiss_index_for_folder, precompute_pdf_embeddings
from .namespace import KeyBuilder
from .registration import RegistrationError


def _audit(job=None, *, event_type, actor=None, payload=None):
    """Record an append-only audit event for a job or system event."""
    MaintenanceAuditEvent.objects.create(
        job=job,
        event_type=event_type,
        actor=actor,
        payload=payload or {},
    )


def _record_validation(generation, *, validation_type, status, details=None, validated_by=None):
    """Record a structured validation result for a generation."""
    return ArtifactValidation.objects.create(
        generation=generation,
        validation_type=validation_type,
        status=status,
        details=details or {},
        validated_by=validated_by,
    )


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
    _audit(event_type="promoted", actor=requested_by, payload={"generation_id": generation.generation_id, "superseded": prior.generation_id if prior else None})
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
    result = promote_active_generation(generation_id, requested_by=requested_by)
    _audit(event_type="rolled_back", actor=requested_by, payload={"generation_id": generation_id})
    return result


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
    for pid in purged_ids:
        _audit(event_type="purged", actor=requested_by, payload={"generation_id": pid})
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
    _audit(event_type="purged", actor=requested_by, payload={"generation_id": generation_id})
    return generation


def deprecate_pdf(pdf, *, requested_by=None):
    """Mark a PDF as deprecated — hides it from search but preserves the row and file."""
    if pdf.lifecycle == "archived":
        raise SearchDataIntegrityError("Cannot deprecate an archived document — restore it first")
    pdf.lifecycle = "deprecated"
    pdf.indexed = False
    pdf.save(update_fields=["lifecycle", "indexed"])
    return pdf


def archive_pdf(pdf, *, requested_by=None):
    """Mark a PDF as archived — hides it from search and dashboard lists."""
    pdf.lifecycle = "archived"
    pdf.indexed = False
    pdf.save(update_fields=["lifecycle", "indexed"])
    return pdf


def restore_pdf(pdf, *, requested_by=None):
    """Restore a deprecated or archived PDF to uploaded state, requeuing reindex."""
    pdf.lifecycle = "uploaded"
    pdf.indexed = False
    pdf.save(update_fields=["lifecycle", "indexed"])
    return pdf


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
    _audit(job=job, event_type="queued", actor=requested_by, payload={"kind": kind, "total_items": job.total_items})
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
        job = MaintenanceJob.objects.get(pk=job.pk)
        _audit(job=job, event_type="claimed")
        return job


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
            generation = sync_active_generation(requested_by=job.requested_by, job=job)
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
                PDFFile.objects.filter(pk=item.pdf.pk).update(lifecycle="processing")
                item.pdf.refresh_from_db()
                precompute_pdf_embeddings(item.pdf)
                PDFFile.objects.filter(pk=item.pdf.pk).update(lifecycle="ready")
                item.pdf.refresh_from_db()
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
            _audit(job=job, event_type="item_failed", payload={"item_id": item.pk, "error_code": item.error_code, "error_message": item.error_message[:200]})
        else:
            item.status = "completed"
            item.finished_at = timezone.now()
            item.save(update_fields=["status", "finished_at"])
            job.completed_items += 1
            _audit(job=job, event_type="item_completed", payload={"item_id": item.pk, "pdf_id": item.pdf_id, "folder_id": item.folder_id})

        job.save(update_fields=["completed_items", "failed_items", "updated_at"])

    job.refresh_from_db()
    job.status = "failed" if job.failed_items else "completed"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])
    _audit(job=job, event_type=job.status, payload={"completed_items": job.completed_items, "failed_items": job.failed_items})
    return job


def sync_active_generation(*, requested_by=None, job=None):
    """Upload a content-addressed active snapshot as an authoritative generation.

    The complete authenticated publish flow:
      1. Validate environment identity (dataset, source, role).
      2. Detect S3 capabilities (conditional ops required).
      3. Validate or register the dataset namespace.
      4. Acquire global writer authority (CAS: If-None-Match or If-Match).
      5. Create consistent SQLite snapshot.
      6. Build manifest, upload immutable artifacts with scoped keys.
      7. Upload candidate manifest.
      8. Revalidate global writer (epoch, token, source, instance).
      9. Update authoritative pointer with exact CAS (If-Match or If-None-Match).
     10. Record local generation as active.
    """
    from django.conf import settings

    vault = ArtifactVault()
    if not vault.enabled:
        raise SearchDataIntegrityError("Artifact vault is disabled")

    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None:
        raise SearchDataIntegrityError(
            "Environment identity is not configured; "
            "generation sync requires explicit APP_ENV, DATASET_ID, "
            "and PRODUCTION_SOURCE_ID."
        )

    if not env_identity.is_authoritative_writer:
        raise SearchDataIntegrityError(
            f"Environment {env_identity.app_env.value} is not an authoritative "
            f"writer for dataset {env_identity.dataset_id}. "
            f"Check APP_ENV, BACKUP_ROLE, DATASET_ID, AUTHORITATIVE_DATASET_ID, "
            f"and PRODUCTION_SOURCE_ID."
        )

    dataset_id = env_identity.dataset_id
    keys = KeyBuilder(dataset_id)

    try:
        from .registration import (
            validate_registration, register_dataset,
            update_authoritative_pointer,
        )
        registration = validate_registration(
            vault, dataset_id,
            app_identifier="pdfsearch",
            production_source_id=env_identity.production_source_id,
        )
    except RegistrationError as exc:
        if "not registered" in str(exc):
            registration = register_dataset(
                vault, dataset_id,
                production_source_id=env_identity.production_source_id,
                instance_id=env_identity.instance_id,
                app_identifier="pdfsearch",
            )
            _audit(job=job, event_type="registered", actor=requested_by,
                   payload={"dataset_id": dataset_id})
        else:
            raise SearchDataIntegrityError(str(exc)) from exc

    lease = None
    writer_record = None
    writer_epoch = 0

    try:
        from .global_writer import acquire_global_writer, validate_writer_for_publication
        from .object_store_capabilities import probe_capabilities

        caps = probe_capabilities(vault, deployment_id=env_identity.deployment_id)
        if not caps.authoritative_publication_allowed:
            raise SearchDataIntegrityError(
                "S3 endpoint does not support conditional operations needed for "
                "safe authoritative publication"
            )

        writer_record = acquire_global_writer(
            vault, dataset_id,
            production_source_id=env_identity.production_source_id,
            instance_id=env_identity.instance_id,
            deployment_id=env_identity.deployment_id,
            replica_id=env_identity.replica_id,
            app_release=env_identity.app_release_version,
            image_digest=env_identity.app_image_digest,
        )
        writer_epoch = writer_record.get("writer_epoch", 0)
    except Exception as exc:
        raise SearchDataIntegrityError(
            f"Cannot acquire global writer for dataset {dataset_id}: {exc}"
        ) from exc

    generation_id = timezone.now().strftime("gen-%Y%m%dT%H%M%S-") + secrets.token_hex(4)

    if job is not None:
        job.refresh_from_db(fields=["status"])
        if job.status == "cancel_requested":
            release_lease_safe(lease)
            raise SearchDataIntegrityError("Backup job was cancelled")

    root = Path(settings.DATA_ROOT).resolve()
    database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()
    if not database_path.is_file():
        release_lease_safe(lease)
        raise SearchDataIntegrityError("Configured SQLite database is missing")

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

        db_sha256 = _file_sha256(snapshot_path)
        files = [{
            "path": "db.sqlite3",
            "bytes": snapshot_path.stat().st_size,
            "sha256": db_sha256,
            "object_key": keys.generation_database(generation_id),
            "artifact_type": "database",
        }]

        for entry in manifest.get("pdf_storage", {}).get("files", []):
            pdf_digest = entry["sha256"]
            pdf_path = root / entry["path"]
            record = {
                **entry,
                "bytes": entry["size_bytes"],
                "sha256": pdf_digest,
                "object_key": keys.blob_pdf(pdf_digest),
                "artifact_type": "pdf",
            }
            files.append(record)
            _upload_file(vault, pdf_path, record)

        for entry in manifest.get("faiss", {}).get("files", []):
            faiss_path = root / entry["path"]
            folder_id = int(Path(entry["path"]).stem.split("_")[-1])
            record = {
                **entry,
                "bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "object_key": keys.generation_faiss(generation_id, folder_id),
                "artifact_type": "faiss",
            }
            files.append(record)
            _upload_file(vault, faiss_path, record)

        _upload_file(vault, snapshot_path, files[0])

        manifest_object_key = keys.generation_manifest(generation_id)
        payload = {
            "release_id": generation_id,
            "manifest_version": 1,
            "read_only": True,
            "source": "active-data-root",
            "dataset_id": dataset_id,
            "writer_epoch": writer_epoch,
            "production_source_id": env_identity.production_source_id,
            "schema": manifest.get("schema", {}),
            "database": manifest.get("database", {}),
            "counts": manifest.get("counts", {}),
            "files": files,
        }
        manifest_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        vault.put(manifest_object_key, manifest_bytes, content_type="application/json")

        if job is not None:
            job.refresh_from_db(fields=["status"])
            if job.status == "cancel_requested":
                _release_writer_safe(vault, dataset_id, writer_record)
                candidate_gen = ArtifactGeneration.objects.create(
                    generation_id=generation_id,
                    status="staged",
                    manifest=payload,
                    source="s3-candidate",
                    created_by=requested_by,
                )
                _audit(job=job, event_type="cancelled", actor=requested_by,
                       payload={"generation_id": generation_id, "stage": "candidate-uploaded"})
                return candidate_gen

        writer_record = validate_writer_for_publication(
            vault, dataset_id,
            writer_record=writer_record,
            production_source_id=env_identity.production_source_id,
            instance_id=env_identity.instance_id,
            expected_epoch=writer_epoch,
        )

        database_schema = manifest.get("database", {}).get("migrations", {}).get("latest", "")
        from .registration import update_authoritative_pointer_cas
        update_authoritative_pointer_cas(
            vault, dataset_id,
            generation_id=generation_id,
            manifest_object_key=manifest_object_key,
            manifest_sha256=manifest_digest,
            writer_record=writer_record,
            production_source_id=env_identity.production_source_id,
            instance_id=env_identity.instance_id,
            app_release=env_identity.app_release_version,
            image_digest=env_identity.app_image_digest,
            database_schema=database_schema,
            previous_generation_id=_previous_active_generation_id(),
        )

        gen = ArtifactGeneration.objects.create(
            generation_id=generation_id,
            status="validated",
            manifest=payload,
            source="s3",
            created_by=requested_by,
            validated_at=timezone.now(),
        )
        _record_validation(gen, validation_type="manifest", status="passed",
                          details={"files": len(files), "writer_epoch": writer_epoch},
                          validated_by=requested_by)
        _record_validation(gen, validation_type="counts", status="passed",
                          details=manifest.get("counts", {}), validated_by=requested_by)
        _audit(job=job, event_type="completed", actor=requested_by,
               payload={"generation_id": generation_id, "writer_epoch": writer_epoch})

        _release_writer_safe(vault, dataset_id, writer_record)
        return gen


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
    gen = ArtifactGeneration.objects.update_or_create(
        generation_id=generation_id,
        defaults={
            "status": "validated",
            "manifest": manifest,
            "source": "s3-staged",
            "created_by": requested_by,
            "validated_at": timezone.now(),
        },
    )[0]
    _record_validation(gen, validation_type="sha256", status="passed", details={"files_verified": len(manifest["files"])}, validated_by=requested_by)
    return gen


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


def _release_writer_safe(vault, dataset_id, writer_record) -> None:
    if writer_record is None:
        return
    try:
        from .global_writer import release_global_writer
        release_global_writer(vault, dataset_id, writer_record=writer_record)
    except Exception:
        pass


def release_lease_safe(lease) -> None:
    """Release a Redis writer lease without propagating errors."""
    if lease is None:
        return
    try:
        from .lease import release_lease as _release_redis_lease
        _release_redis_lease(lease)
    except Exception:
        pass


def _previous_active_generation_id() -> str:
    try:
        prev = ArtifactGeneration.objects.filter(
            status="active"
        ).order_by("-promoted_at").first()
        return prev.generation_id if prev else ""
    except Exception:
        return ""
