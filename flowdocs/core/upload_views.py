"""JSON endpoints for the durable, bounded PDF intake manifest."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Folder, UploadBatch, UploadBatchItem
from .services.upload_intake import (
    UploadBatchLimitError,
    UploadBatchStateError,
    UploadIntakeError,
    UploadIntakePermissionError,
    cleanup_expired_upload_batches,
    create_upload_batch,
    discard_upload_batch,
    finalize_upload_batch,
    receive_upload,
    remove_upload_item,
)


def _operator_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (
            getattr(request.user, "is_superuser", False)
            or getattr(request.user, "role", "") in {"admin", "superadmin"}
        ):
            return JsonResponse(
                {
                    "error": "operator_permission_required",
                    "detail": "Administrator permission is required.",
                },
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapped


def _owned_batch(request, batch_id):
    queryset = UploadBatch.objects.select_related(
        "folder", "uploader", "maintenance_job"
    )
    if not (
        getattr(request.user, "is_superuser", False)
        or getattr(request.user, "role", "") == "superadmin"
    ):
        queryset = queryset.filter(uploader=request.user)
    return get_object_or_404(queryset, public_id=batch_id)


def _display_state(item, batch):
    if item.state != "received" or item.pdf_file is None:
        return item.state
    if batch.status == "draft":
        return "received"
    pdf = item.pdf_file
    if pdf.processing_status == "failed":
        return "needs_attention"
    if pdf.processing_status == "running":
        return "processing"
    if pdf.processing_status == "ready":
        return "searchable" if pdf.indexed else "needs_attention"
    return "queued"


def _serialize_item(item, batch):
    pdf = item.pdf_file
    return {
        "id": item.pk,
        "idempotency_key": item.idempotency_key,
        "original_filename": item.original_filename,
        "title": item.title,
        "state": item.state,
        "display_state": _display_state(item, batch),
        "checksum_sha256": item.checksum_sha256,
        "error_code": item.error_code or (pdf.processing_error_code if pdf else ""),
        "error_message": item.error_message or (pdf.processing_error_message if pdf else ""),
        "pdf_id": item.pdf_file_id,
        "remove_url": reverse(
            "upload_batch_item_remove",
            kwargs={"batch_id": batch.public_id, "item_id": item.pk},
        ),
    }


def _serialize_batch(batch):
    items = list(batch.items.select_related("pdf_file").order_by("created_at", "pk"))
    received = sum(item.state == "received" for item in items)
    rejected = sum(item.state == "rejected" for item in items)
    active = sum(item.state != "removed" for item in items)
    job = batch.maintenance_job
    return {
        "id": str(batch.public_id),
        "status": batch.status,
        "folder_id": batch.folder_id,
        "expires_at": batch.expires_at.isoformat(),
        "finalized_at": batch.finalized_at.isoformat() if batch.finalized_at else None,
        "manifest_sha256": batch.manifest_sha256,
        "counts": {
            "active": active,
            "received": received,
            "rejected": rejected,
            "limit": UploadBatch.MAX_FILES,
        },
        "can_finalize": batch.status == "draft" and received > 0,
        "items": [_serialize_item(item, batch) for item in items],
        "job": (
            {
                "id": str(job.public_id),
                "status": job.status,
                "total_items": job.total_items,
                "completed_items": job.completed_items,
                "failed_items": job.failed_items,
            }
            if job
            else None
        ),
        "endpoints": {
            "detail": reverse("upload_batch_detail", kwargs={"batch_id": batch.public_id}),
            "item": reverse("upload_batch_item", kwargs={"batch_id": batch.public_id}),
            "finalize": reverse("upload_batch_finalize", kwargs={"batch_id": batch.public_id}),
            "discard": reverse("upload_batch_discard", kwargs={"batch_id": batch.public_id}),
        },
    }


def _expected_error(exc):
    if isinstance(exc, UploadIntakePermissionError):
        return JsonResponse({"error": "permission_denied", "detail": str(exc)}, status=403)
    if isinstance(exc, UploadBatchLimitError):
        return JsonResponse({"error": "batch_limit", "detail": str(exc)}, status=409)
    if isinstance(exc, UploadBatchStateError):
        return JsonResponse({"error": "batch_state", "detail": str(exc)}, status=409)
    return JsonResponse({"error": "invalid_intake", "detail": str(exc)}, status=400)


@_operator_required
@require_POST
def create_batch(request, folder_id):
    folder = get_object_or_404(Folder, pk=folder_id)
    cleanup_expired_upload_batches(limit=5)
    batch = (
        UploadBatch.objects.filter(
            folder=folder,
            uploader=request.user,
            status="draft",
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    created = batch is None
    if batch is None:
        try:
            batch = create_upload_batch(folder=folder, uploader=request.user)
        except UploadIntakeError as exc:
            return _expected_error(exc)
    return JsonResponse({"batch": _serialize_batch(batch)}, status=201 if created else 200)


@_operator_required
@require_GET
def batch_detail(request, batch_id):
    batch = _owned_batch(request, batch_id)
    return JsonResponse({"batch": _serialize_batch(batch)})


@_operator_required
@require_POST
def receive_batch_item(request, batch_id):
    batch = _owned_batch(request, batch_id)
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return JsonResponse(
            {"error": "file_required", "detail": "Choose a PDF to add."},
            status=400,
        )
    try:
        item = receive_upload(
            batch=batch,
            actor=request.user,
            idempotency_key=request.POST.get("idempotency_key", ""),
            uploaded_file=uploaded_file,
            title=request.POST.get("title"),
        )
    except UploadIntakeError as exc:
        return _expected_error(exc)
    batch.refresh_from_db()
    payload = {"item": _serialize_item(item, batch), "batch": _serialize_batch(batch)}
    return JsonResponse(payload, status=201 if item.state == "received" else 422)


@_operator_required
@require_POST
def remove_batch_item(request, batch_id, item_id):
    batch = _owned_batch(request, batch_id)
    try:
        item = get_object_or_404(UploadBatchItem, batch=batch, pk=item_id)
        remove_upload_item(batch=batch, item=item, actor=request.user)
    except UploadIntakeError as exc:
        return _expected_error(exc)
    batch.refresh_from_db()
    return JsonResponse({"batch": _serialize_batch(batch)})


@_operator_required
@require_POST
def finalize_batch(request, batch_id):
    batch = _owned_batch(request, batch_id)
    try:
        finalize_upload_batch(batch=batch, actor=request.user)
    except UploadIntakeError as exc:
        return _expected_error(exc)
    batch.refresh_from_db()
    return JsonResponse({"batch": _serialize_batch(batch)})


@_operator_required
@require_POST
def discard_batch(request, batch_id):
    batch = _owned_batch(request, batch_id)
    try:
        discard_upload_batch(batch=batch, actor=request.user)
    except UploadIntakeError as exc:
        return _expected_error(exc)
    batch.refresh_from_db()
    return JsonResponse({"batch": _serialize_batch(batch)})
