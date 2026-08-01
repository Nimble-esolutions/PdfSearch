"""Control-plane reconciliation for Data Operations receipts."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import MaintenanceJob

from .models import DataOperation, DataOpsAuditEvent


def reconcile_receipts(*, limit: int = 50) -> int:
    """Advance Data Operations receipts from the existing worker evidence.

    This is intentionally a receipt reconciler, not a second executor. Backup
    and restore remain approval-gated until their new executor is cut over.
    """
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    changed = 0
    for operation in DataOperation.objects.using("control").filter(state=DataOperation.State.QUEUED).order_by("created_at")[:limit]:
        with transaction.atomic(using="control"):
            current = DataOperation.objects.using("control").select_for_update().get(pk=operation.pk)
            if current.state != DataOperation.State.QUEUED:
                continue
            if current.kind in {DataOperation.Kind.BACKUP, DataOperation.Kind.RESTORE}:
                current.state = DataOperation.State.WAITING_APPROVAL
                current.error_code = "executor_cutover_required"
                current.finished_at = timezone.now()
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
    return changed
