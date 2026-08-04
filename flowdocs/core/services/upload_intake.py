"""Durable, retry-safe PDF intake before maintenance processing is queued."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.forms import UploadForm
from core.maintenance import queue_job
from core.models import PDFFile, UploadBatch, UploadBatchItem


DEFAULT_BATCH_LIFETIME = timedelta(hours=24)


class UploadIntakeError(Exception):
    """Base class for expected upload-intake refusals."""


class UploadIntakePermissionError(UploadIntakeError):
    """Raised when an actor cannot manage the requested intake."""


class UploadBatchStateError(UploadIntakeError):
    """Raised when an operation is not valid for the batch state."""


class UploadBatchLimitError(UploadIntakeError):
    """Raised when a batch would exceed its hard file cap."""


def _is_superadmin(actor) -> bool:
    return bool(
        getattr(actor, "is_superuser", False)
        or getattr(actor, "role", "") == "superadmin"
    )


def _assert_active_operator(actor) -> None:
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or not getattr(actor, "is_active", False)
        or (
            not _is_superadmin(actor)
            and getattr(actor, "role", "") != "admin"
        )
    ):
        raise UploadIntakePermissionError("An active document operator is required.")


def _assert_folder_access(*, folder, actor) -> None:
    """Match the dashboard contract: active admins may manage any folder."""
    _assert_active_operator(actor)


def _assert_batch_access(*, batch: UploadBatch, actor) -> None:
    _assert_active_operator(actor)
    if not _is_superadmin(actor) and batch.uploader_id != actor.pk:
        raise UploadIntakePermissionError(
            "This operator cannot manage the selected upload batch."
        )


def _lock_batch(*, batch: UploadBatch, actor) -> UploadBatch:
    """Acquire a real write lock on SQLite and a row lock where supported."""
    _assert_active_operator(actor)
    authorized: QuerySet[UploadBatch] = UploadBatch.objects.filter(pk=batch.pk)
    if not _is_superadmin(actor):
        authorized = authorized.filter(uploader_id=actor.pk)
    if authorized.update(updated_at=timezone.now()) != 1:
        if UploadBatch.objects.filter(pk=batch.pk).exists():
            raise UploadIntakePermissionError(
                "This operator cannot manage the selected upload batch."
            )
        raise UploadBatch.DoesNotExist
    locked = UploadBatch.objects.select_for_update().get(pk=batch.pk)
    _assert_batch_access(batch=locked, actor=actor)
    return locked


def _original_filename(uploaded_file) -> str:
    raw_name = str(getattr(uploaded_file, "name", "") or "")
    return raw_name.replace("\\", "/").rsplit("/", 1)[-1][:255]


def _default_title(original_filename: str) -> str:
    stem = original_filename.rsplit(".", 1)[0] if "." in original_filename else original_filename
    return stem.strip()


def _validation_failure(form: UploadForm) -> tuple[str, str]:
    errors = form.errors.get_json_data()
    field = "file" if errors.get("file") else "title"
    detail = errors[field][0]
    message = detail["message"][:500]
    lowered = message.casefold()

    if field == "title":
        code = "invalid_title"
    elif "no larger than" in lowered:
        code = "file_too_large"
    elif "only pdf files" in lowered:
        code = "invalid_file_extension"
    elif "must be a pdf" in lowered:
        code = "invalid_content_type"
    elif "valid pdf" in lowered:
        code = "invalid_pdf_header"
    else:
        code = "invalid_pdf"
    return code, message


def _checksum(uploaded_file) -> str:
    digest = hashlib.sha256()
    uploaded_file.seek(0)
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def _delete_stored_files(stored_files) -> None:
    for storage, name in stored_files:
        if name:
            storage.delete(name)


def _manifest_digest(batch: UploadBatch, items: list[UploadBatchItem]) -> str:
    payload = {
        "schema": "upload-intake-v1",
        "batch_public_id": str(batch.public_id),
        "folder_id": batch.folder_id,
        "uploader_id": batch.uploader_id,
        "items": [
            {
                "idempotency_key": item.idempotency_key,
                "original_filename": item.original_filename,
                "title": item.title,
                "checksum_sha256": item.checksum_sha256,
                "pdf_id": item.pdf_file_id,
            }
            for item in sorted(items, key=lambda candidate: candidate.idempotency_key)
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def create_upload_batch(*, folder, uploader, expires_at=None) -> UploadBatch:
    """Create a draft manifest after applying the existing folder ownership rule."""
    _assert_folder_access(folder=folder, actor=uploader)
    expires_at = expires_at or (timezone.now() + DEFAULT_BATCH_LIFETIME)
    if expires_at <= timezone.now():
        raise UploadBatchStateError("Upload batch expiry must be in the future.")
    return UploadBatch.objects.create(
        folder=folder,
        uploader=uploader,
        expires_at=expires_at,
    )


def receive_upload(
    *,
    batch: UploadBatch,
    actor,
    idempotency_key: str,
    uploaded_file,
    title: str | None = None,
) -> UploadBatchItem:
    """Validate and persist one PDF, returning the same receipt on retries."""
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key or len(idempotency_key) > 128:
        raise UploadIntakeError("A valid idempotency key is required.")

    stored_file = None
    expired = False
    result = None
    try:
        with transaction.atomic():
            locked = _lock_batch(batch=batch, actor=actor)

            existing = locked.items.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                return existing

            if locked.status != "draft":
                raise UploadBatchStateError(
                    f"Uploads cannot be received by a {locked.status} batch."
                )
            if locked.expires_at <= timezone.now():
                locked.status = "expired"
                locked.save(update_fields=["status", "updated_at"])
                expired = True
            else:
                active_items = locked.items.exclude(state="removed").count()
                if active_items >= UploadBatch.MAX_FILES:
                    raise UploadBatchLimitError(
                        f"An upload batch cannot contain more than {UploadBatch.MAX_FILES} files."
                    )

                original_filename = _original_filename(uploaded_file)
                requested_title = (
                    _default_title(original_filename) if title is None else str(title).strip()
                )
                form = UploadForm(
                    data={"title": requested_title},
                    files={"file": uploaded_file},
                )
                if not form.is_valid():
                    error_code, error_message = _validation_failure(form)
                    result = UploadBatchItem.objects.create(
                        batch=locked,
                        idempotency_key=idempotency_key,
                        original_filename=original_filename,
                        title=requested_title[:200],
                        state="rejected",
                        error_code=error_code,
                        error_message=error_message,
                    )
                else:
                    validated_file = form.cleaned_data["file"]
                    checksum_sha256 = _checksum(validated_file)
                    duplicate = locked.items.filter(
                        state="received",
                        checksum_sha256=checksum_sha256,
                    ).first()
                    if duplicate is not None:
                        result = UploadBatchItem.objects.create(
                            batch=locked,
                            idempotency_key=idempotency_key,
                            original_filename=original_filename,
                            title=form.cleaned_data["title"],
                            state="rejected",
                            checksum_sha256=checksum_sha256,
                            error_code="duplicate_in_batch",
                            error_message=(
                                "This PDF is already present in the current intake manifest."
                            ),
                        )
                    else:
                        pdf = PDFFile(
                            title=form.cleaned_data["title"],
                            uploaded_by=locked.uploader,
                            folder=locked.folder,
                            indexed=False,
                            lifecycle="intake",
                            processing_status="queued",
                        )
                        pdf.file.save(validated_file.name, validated_file, save=False)
                        stored_file = (pdf.file.storage, pdf.file.name)
                        pdf.save()
                        result = UploadBatchItem.objects.create(
                            batch=locked,
                            idempotency_key=idempotency_key,
                            original_filename=original_filename,
                            title=pdf.title,
                            state="received",
                            checksum_sha256=checksum_sha256,
                            pdf_file=pdf,
                        )
    except Exception:
        if stored_file is not None:
            _delete_stored_files([stored_file])
        raise

    if expired:
        raise UploadBatchStateError("The upload batch has expired.")
    return result


def finalize_upload_batch(*, batch: UploadBatch, actor):
    """Seal a manifest and queue exactly one multi-item process_pdf job."""
    expired = False
    job = None
    with transaction.atomic():
        locked = _lock_batch(batch=batch, actor=actor)

        if locked.status == "finalized":
            if locked.maintenance_job_id is None:
                raise UploadBatchStateError(
                    "The finalized batch is missing its maintenance job."
                )
            return locked.maintenance_job
        if locked.status != "draft":
            raise UploadBatchStateError(
                f"A {locked.status} upload batch cannot be finalized."
            )
        if locked.expires_at <= timezone.now():
            locked.status = "expired"
            locked.save(update_fields=["status", "updated_at"])
            expired = True
        else:
            items = list(
                locked.items.filter(state="received", pdf_file__isnull=False)
                .select_related("pdf_file")
                .order_by("idempotency_key")
            )
            if not items:
                raise UploadBatchStateError(
                    "At least one received PDF is required before finalization."
                )

            manifest_sha256 = _manifest_digest(locked, items)
            pdfs = [item.pdf_file for item in items]
            job = queue_job(
                kind="process_pdf",
                requested_by=locked.uploader,
                pdfs=pdfs,
                scope={
                    "upload_batch_id": str(locked.public_id),
                    "folder_id": locked.folder_id,
                },
                options={"manifest_sha256": manifest_sha256},
            )
            locked.status = "finalized"
            locked.finalized_at = timezone.now()
            locked.maintenance_job = job
            locked.manifest_sha256 = manifest_sha256
            locked.save(
                update_fields=[
                    "status",
                    "finalized_at",
                    "maintenance_job",
                    "manifest_sha256",
                    "updated_at",
                ]
            )

    if expired:
        raise UploadBatchStateError("The upload batch has expired.")
    return job


def discard_upload_batch(*, batch: UploadBatch, actor) -> UploadBatch:
    """Discard an unfinalized manifest and remove only the PDFs it owns."""
    stored_files = []
    with transaction.atomic():
        locked = _lock_batch(batch=batch, actor=actor)

        if locked.status == "discarded":
            return locked
        if locked.status == "finalized" or locked.maintenance_job_id is not None:
            raise UploadBatchStateError("A finalized upload batch cannot be discarded.")

        items = list(
            locked.items.filter(state="received", pdf_file__isnull=False)
            .select_related("pdf_file")
        )
        pdf_ids = [item.pdf_file_id for item in items]
        if pdf_ids and UploadBatchItem.objects.filter(pdf_file_id__in=pdf_ids).exclude(
            batch=locked
        ).exists():
            raise UploadBatchStateError(
                "The batch contains a PDF owned by another intake manifest."
            )

        for item in items:
            stored_files.append((item.pdf_file.file.storage, item.pdf_file.file.name))

        locked.items.update(state="removed", pdf_file=None, updated_at=timezone.now())
        if pdf_ids:
            PDFFile.objects.filter(pk__in=pdf_ids).delete()
        locked.status = "discarded"
        locked.save(update_fields=["status", "updated_at"])
        transaction.on_commit(lambda: _delete_stored_files(stored_files))

    return locked


def remove_upload_item(*, batch: UploadBatch, item: UploadBatchItem, actor) -> UploadBatchItem:
    """Remove one draft receipt without affecting unrelated or finalized PDFs."""
    stored_file = None
    with transaction.atomic():
        locked = _lock_batch(batch=batch, actor=actor)
        if locked.status != "draft":
            raise UploadBatchStateError(
                f"Items cannot be removed from a {locked.status} batch."
            )
        current = locked.items.select_for_update().select_related("pdf_file").get(
            pk=item.pk
        )
        if current.state == "removed":
            return current
        pdf_id = current.pdf_file_id
        if current.pdf_file is not None:
            stored_file = (
                current.pdf_file.file.storage,
                current.pdf_file.file.name,
            )
        current.state = "removed"
        current.pdf_file = None
        current.save(update_fields=["state", "pdf_file", "updated_at"])
        if pdf_id:
            PDFFile.objects.filter(pk=pdf_id).delete()
        if stored_file is not None:
            transaction.on_commit(lambda: _delete_stored_files([stored_file]))
    return current


def cleanup_expired_upload_batches(*, limit: int = 10, now=None) -> int:
    """Boundedly expire abandoned drafts and remove only their intake PDFs."""
    if limit < 1 or limit > 100:
        raise ValueError("Expired batch cleanup limit must be between 1 and 100.")
    now = now or timezone.now()
    batch_ids = list(
        UploadBatch.objects.filter(status="draft", expires_at__lte=now)
        .order_by("expires_at")
        .values_list("pk", flat=True)[:limit]
    )
    cleaned = 0
    for batch_id in batch_ids:
        stored_files = []
        with transaction.atomic():
            locked = UploadBatch.objects.select_for_update().get(pk=batch_id)
            if locked.status != "draft" or locked.expires_at > now:
                continue
            items = list(
                locked.items.filter(state="received", pdf_file__isnull=False)
                .select_related("pdf_file")
            )
            pdf_ids = [item.pdf_file_id for item in items]
            stored_files.extend(
                (item.pdf_file.file.storage, item.pdf_file.file.name)
                for item in items
            )
            locked.items.update(
                state="removed",
                pdf_file=None,
                updated_at=timezone.now(),
            )
            if pdf_ids:
                PDFFile.objects.filter(pk__in=pdf_ids).delete()
            locked.status = "expired"
            locked.save(update_fields=["status", "updated_at"])
            transaction.on_commit(
                lambda files=tuple(stored_files): _delete_stored_files(files)
            )
            cleaned += 1
    return cleaned
