"""Maintenance-worker dispatch and receipt reconciliation for Data Operations."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import MaintenanceJob

from .models import DataOperation, DataOpsAuditEvent
from .pipeline import DataOpsPipelineError, execute_operation_record
from .config import resolve_selectors


def _finish_pipeline_operation(operation):
    """Run one backup/restore outside the row-lock and persist a safe result."""
    operation.attempt = int(operation.attempt or 0) + 1
    operation.state = DataOperation.State.RUNNING
    operation.pipeline_stage = "preflight"
    operation.started_at = operation.started_at or timezone.now()
    operation.save(update_fields=["attempt", "state", "pipeline_stage", "started_at", "updated_at"])
    try:
        result = execute_operation_record(operation)
    except DataOpsPipelineError as exc:
        operation.error_code = exc.code
        operation.error_detail = json_safe_detail(exc.details)
        operation.pipeline_stage = exc.stage or "unknown"
        max_retries = 3
        try:
            from django.conf import settings

            max_retries = int(getattr(settings, "DATAOPS_AUTO_HEAL_MAX_RETRIES", 3))
        except Exception:
            pass
        if exc.retryable and operation.attempt <= max_retries:
            operation.state = DataOperation.State.QUEUED
            operation.finished_at = None
        else:
            operation.state = DataOperation.State.FAILED
            operation.finished_at = timezone.now()
        operation.save(update_fields=["state", "pipeline_stage", "error_code", "error_detail", "finished_at", "updated_at"])
        DataOpsAuditEvent.objects.using("control").create(
            operation_id=operation.public_id,
            profile_key=operation.destination_profile_key or operation.source_profile_key or operation.profile_key,
            action="pipeline_failed",
            outcome=operation.state,
            evidence={"code": operation.error_code, "stage": operation.pipeline_stage, "attempt": operation.attempt},
        )
        return operation
    except Exception:
        operation.state = DataOperation.State.FAILED
        operation.pipeline_stage = "unknown"
        operation.error_code = "operation_executor_failed"
        operation.error_detail = "The executor returned an unexpected failure."
        operation.finished_at = timezone.now()
        operation.save(update_fields=["state", "pipeline_stage", "error_code", "error_detail", "finished_at", "updated_at"])
        return operation
    operation.state = DataOperation.State.SUCCEEDED
    operation.pipeline_stage = "publish_receipt"
    operation.result = result
    operation.release_id = str(result.get("release_id", operation.release_id) or operation.release_id)
    operation.source_profile_key = str(result.get("source_profile", operation.source_profile_key) or operation.source_profile_key)
    operation.destination_profile_key = str(result.get("destination_profile", operation.destination_profile_key) or operation.destination_profile_key)
    operation.error_code = ""
    operation.error_detail = ""
    operation.finished_at = timezone.now()
    operation.save(update_fields=["state", "pipeline_stage", "result", "release_id", "source_profile_key", "destination_profile_key", "error_code", "error_detail", "finished_at", "updated_at"])
    if operation.kind == DataOperation.Kind.BACKUP:
        try:
            from core.backup_policy import clear_dirty_flag

            clear_dirty_flag()
        except Exception:
            pass
    DataOpsAuditEvent.objects.using("control").create(
        operation_id=operation.public_id,
        profile_key=operation.destination_profile_key or operation.source_profile_key or operation.profile_key,
        action="pipeline_receipt_published",
        outcome=operation.state,
        evidence={
            "manifest_digest": result.get("manifest_digest", ""),
            "active_generation": result.get("activation", {}).get("active_generation", ""),
            "source_profile": result.get("source_profile", ""),
            "destination_profile": result.get("destination_profile", ""),
        },
    )
    return operation


def json_safe_detail(value):
    """Keep operation error details bounded and JSON-safe."""
    if not isinstance(value, dict):
        return ""
    try:
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":"))[:4000]
    except (TypeError, ValueError):
        return ""


def queue_backup_if_due(*, trigger: str = "scheduled", force: bool = False, requested_by_id=None, requested_by_name: str = ""):
    """Coalesce scheduled/change-triggered backup requests into one operation."""
    from django.conf import settings

    if not getattr(settings, "DATAOPS_ENABLED", False):
        return None
    mode = str(getattr(settings, "DATAOPS_BACKUP_MODE", "manual")).strip().lower()
    if mode == "manual" and not force:
        return None
    if mode == "changes" and not force:
        from django.core.cache import cache
        from core.backup_policy import DIRTY_STATE_KEY

        dirty_at = cache.get(DIRTY_STATE_KEY)
        if dirty_at is None:
            return None
        try:
            quiet_period = int(getattr(settings, "DATAOPS_BACKUP_QUIET_PERIOD_SECONDS", 120))
            if (timezone.now().timestamp() - float(dirty_at)) < quiet_period:
                return None
        except (TypeError, ValueError):
            return None
    selectors = resolve_selectors()
    destination_key = selectors.get("backup_destination") or selectors.get("backup")
    source_key = selectors.get("backup_source") or ""
    if not destination_key:
        return None
    now = timezone.now()
    interval = int(getattr(settings, "DATAOPS_BACKUP_INTERVAL_SECONDS", 900))
    latest = DataOperation.objects.using("control").filter(
        kind=DataOperation.Kind.BACKUP,
        state=DataOperation.State.SUCCEEDED,
    ).first()
    if not force and latest and latest.finished_at and (now - latest.finished_at).total_seconds() < interval:
        return None
    bucket = int(now.timestamp() // max(1, interval))
    idempotency = f"dataops-backup:{destination_key}:{mode}:{bucket}"
    operation, created = DataOperation.objects.using("control").get_or_create(
        kind=DataOperation.Kind.BACKUP,
        idempotency_key=idempotency,
        defaults={
            "state": DataOperation.State.QUEUED,
            "profile_key": destination_key,
            "source_profile_key": source_key,
            "destination_profile_key": destination_key,
            "request_id": str(requested_by_id or ""),
            "checkpoint": {"trigger": trigger},
        },
    )
    if created:
        DataOpsAuditEvent.objects.using("control").create(
            actor_id=requested_by_id,
            actor_name=requested_by_name,
            action="backup_queued_automatically",
            operation_id=operation.public_id,
            profile_key=destination_key,
            outcome="queued",
            evidence={"trigger": trigger, "mode": mode},
        )
    return operation if created else None


def reconcile_receipts(*, limit: int = 50) -> int:
    """Claim and execute queued operations, then reconcile legacy job receipts."""
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    changed = 0
    for operation in DataOperation.objects.using("control").filter(state=DataOperation.State.QUEUED).order_by("created_at")[:limit]:
        with transaction.atomic(using="control"):
            current = DataOperation.objects.using("control").select_for_update().get(pk=operation.pk)
            if current.state != DataOperation.State.QUEUED:
                continue
            if current.kind in {DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE}:
                # Release the short claim transaction before network I/O.  The
                # executor owns its own checkpoints and retries.
                current.state = DataOperation.State.RUNNING
                current.pipeline_stage = "preflight"
                current.started_at = current.started_at or timezone.now()
                current.save(update_fields=["state", "pipeline_stage", "started_at", "updated_at"])
                claimed = current
                changed += 1
                # Network/storage work must never run while the control row is
                # locked, so execute after the atomic block below.
                continue
            elif current.request_id:
                try:
                    job = MaintenanceJob.objects.get(public_id=current.request_id)
                except (MaintenanceJob.DoesNotExist, ValueError):
                    current.state = DataOperation.State.FAILED
                    current.error_code = "maintenance_receipt_missing"
                    current.finished_at = timezone.now()
                else:
                    if job.status in {"completed", "succeeded"}:
                        current.state = DataOperation.State.SUCCEEDED
                        current.result = {"maintenance_job_id": str(job.public_id), "completed_items": job.completed_items, "failed_items": job.failed_items}
                        current.finished_at = timezone.now()
                    elif job.status in {"failed", "cancelled"}:
                        current.state = DataOperation.State.FAILED
                        current.error_code = "maintenance_job_failed"
                        current.finished_at = timezone.now()
                    else:
                        current.state = DataOperation.State.RUNNING
                        current.started_at = current.started_at or timezone.now()
            else:
                current.state = DataOperation.State.FAILED
                current.error_code = "operation_executor_missing"
                current.finished_at = timezone.now()
            current.save(update_fields=["state", "error_code", "result", "started_at", "finished_at", "updated_at"])
            DataOpsAuditEvent.objects.using("control").create(operation_id=current.public_id, action="receipt_reconciled", outcome=current.state, evidence={"error_code": current.error_code})
            changed += 1
    # Backup/restore claims are executed after all short database transactions.
    for operation in DataOperation.objects.using("control").filter(
        state=DataOperation.State.RUNNING,
        pipeline_stage="preflight",
        kind__in={DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE},
    ).order_by("created_at")[:limit]:
        _finish_pipeline_operation(operation)
    return changed
