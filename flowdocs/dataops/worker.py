"""Maintenance-worker dispatch and receipt reconciliation for Data Operations."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import MaintenanceJob

from .models import DataOperation, DataOpsAuditEvent
from .pipeline import DataOpsPipelineError, execute_operation_record
from .config import resolve_selectors


def _lease_deadline():
    from django.conf import settings

    try:
        seconds = int(getattr(settings, "DATAOPS_OPERATION_LEASE_SECONDS", 3600))
    except (TypeError, ValueError):
        seconds = 3600
    return timezone.now() + timedelta(seconds=max(60, seconds))


def _renew_operation_lease(operation_id, lease_token):
    """Extend a lease only when this worker still owns the operation row."""
    if not lease_token:
        return {"lease_id": uuid.uuid4().hex}
    renewed = DataOperation.objects.using("control").filter(
        pk=operation_id,
        state=DataOperation.State.RUNNING,
        lease_token=lease_token,
    ).update(lease_expires_at=_lease_deadline(), updated_at=timezone.now())
    if renewed != 1:
        raise DataOpsPipelineError("operation_lease_lost", stage="lease", retryable=True)
    return {"lease_id": lease_token}


def _claim_pipeline_operation(operation_id):
    """Claim a queued operation or reclaim an expired preflight lease."""
    now = timezone.now()
    token = uuid.uuid4().hex
    with transaction.atomic(using="control"):
        current = DataOperation.objects.using("control").select_for_update().get(pk=operation_id)
        queued = current.state == DataOperation.State.QUEUED
        expired = (
            current.state == DataOperation.State.RUNNING
            and (current.lease_expires_at is None or current.lease_expires_at <= now)
        )
        if not queued and not expired:
            return None
        current.state = DataOperation.State.RUNNING
        current.pipeline_stage = "preflight"
        current.started_at = current.started_at or now
        current.lease_token = token
        current.lease_expires_at = _lease_deadline()
        current.save(update_fields=["state", "pipeline_stage", "started_at", "lease_token", "lease_expires_at", "updated_at"])
        return current, token


def _finish_pipeline_operation(operation, *, lease_token=""):
    """Run one backup/restore outside the row-lock and persist a safe result."""
    operation.attempt = int(operation.attempt or 0) + 1
    operation.state = DataOperation.State.RUNNING
    operation.pipeline_stage = "preflight"
    operation.started_at = operation.started_at or timezone.now()
    operation.lease_token = lease_token or operation.lease_token
    operation.lease_expires_at = _lease_deadline() if operation.lease_token else operation.lease_expires_at
    operation.save(update_fields=["attempt", "state", "pipeline_stage", "started_at", "lease_token", "lease_expires_at", "updated_at"])

    lease = (lambda: _renew_operation_lease(operation.pk, operation.lease_token)) if operation.lease_token else None
    try:
        if operation.kind == DataOperation.Kind.SYNC:
            from .job_executor import execute_backup_job

            result = execute_backup_job(operation, lease=lease)
        else:
            result = execute_operation_record(operation, lease=lease)
    except DataOpsPipelineError as exc:
        if operation.lease_token and not DataOperation.objects.using("control").filter(
            pk=operation.pk, state=DataOperation.State.RUNNING, lease_token=operation.lease_token
        ).exists():
            return operation
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
        operation.lease_token = ""
        operation.lease_expires_at = None
        operation.save(update_fields=["state", "pipeline_stage", "error_code", "error_detail", "finished_at", "lease_token", "lease_expires_at", "updated_at"])
        DataOpsAuditEvent.objects.using("control").create(
            operation_id=operation.public_id,
            profile_key=operation.destination_profile_key or operation.source_profile_key or operation.profile_key,
            action="pipeline_failed",
            outcome=operation.state,
            evidence={"code": operation.error_code, "stage": operation.pipeline_stage, "attempt": operation.attempt},
        )
        return operation
    except Exception:
        if operation.lease_token and not DataOperation.objects.using("control").filter(
            pk=operation.pk, state=DataOperation.State.RUNNING, lease_token=operation.lease_token
        ).exists():
            return operation
        operation.state = DataOperation.State.FAILED
        operation.pipeline_stage = "unknown"
        operation.error_code = "operation_executor_failed"
        operation.error_detail = "The executor returned an unexpected failure."
        operation.finished_at = timezone.now()
        operation.lease_token = ""
        operation.lease_expires_at = None
        operation.save(update_fields=["state", "pipeline_stage", "error_code", "error_detail", "finished_at", "lease_token", "lease_expires_at", "updated_at"])
        return operation
    if operation.lease_token:
        try:
            _renew_operation_lease(operation.pk, operation.lease_token)
        except DataOpsPipelineError:
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
    operation.lease_token = ""
    operation.lease_expires_at = None
    operation.save(update_fields=["state", "pipeline_stage", "result", "release_id", "source_profile_key", "destination_profile_key", "error_code", "error_detail", "finished_at", "lease_token", "lease_expires_at", "updated_at"])
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
    for operation in DataOperation.objects.using("control").filter(
        state=DataOperation.State.QUEUED,
    ).exclude(kind__in={DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE, DataOperation.Kind.SYNC}).order_by("created_at")[:limit]:
        with transaction.atomic(using="control"):
            current = DataOperation.objects.using("control").select_for_update().get(pk=operation.pk)
            if current.state != DataOperation.State.QUEUED:
                continue
            if current.request_id:
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
    # Transfer pipelines are executed after all short database transactions.
    claims = []
    queued_ids = DataOperation.objects.using("control").filter(
        state=DataOperation.State.QUEUED,
        kind__in={DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE, DataOperation.Kind.SYNC},
    ).order_by("created_at").values_list("pk", flat=True)[:limit]
    for operation_id in queued_ids:
        claim = _claim_pipeline_operation(operation_id)
        if claim:
            claims.append(claim)
    remaining = max(0, limit - len(claims))
    if remaining:
        stale_ids = DataOperation.objects.using("control").filter(
            state=DataOperation.State.RUNNING,
            kind__in={DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE, DataOperation.Kind.SYNC},
        ).filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=timezone.now())).order_by("created_at").values_list("pk", flat=True)[:remaining]
        for operation_id in stale_ids:
            claim = _claim_pipeline_operation(operation_id)
            if claim:
                claims.append(claim)
    for operation, lease_token in claims:
        _finish_pipeline_operation(operation, lease_token=lease_token)
    changed += len(claims)
    return changed
