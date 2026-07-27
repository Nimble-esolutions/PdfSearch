"""Preview-and-confirm contract for local document and index maintenance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .emergency_recovery import create_set, list_sets, plan_prune
from .artifact_cleanup import cleanup_plan, inventory_local_artifacts
from .maintenance import queue_job
from .models import Folder, MaintenanceJob, MaintenancePlan, PDFFile

LOCAL_OPERATIONS = {
    "validate",
    "repair_indexes",
    "reindex_needed",
    "reindex_selected",
}
FORCE_CONFIRMATION = "REINDEX SELECTED"


class MaintenancePlanError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


def capability_reasons() -> dict[str, str]:
    common = ""
    if getattr(settings, "ACTIVE_RUNTIME", None) is not None:
        common = "runtime_read_only"
    elif not getattr(settings, "LOCAL_INDEX_MAINTENANCE_ENABLED", False):
        common = "bulk_reindex_disabled"
    else:
        try:
            from vaultops.models import VaultJob

            if VaultJob.objects.using("control").filter(
                status__in=("queued", "running", "retrying"),
                operation="sync_publish",
            ).exists():
                common = "snapshot_in_progress"
        except Exception:
            pass

    reasons = {
        "validate": common,
        "repair_indexes": common,
        "reindex_needed": common,
        "reindex_selected": common,
    }
    if not common and not getattr(settings, "FORCE_REINDEX_ENABLED", False):
        reasons["reindex_selected"] = "bulk_reindex_disabled"
    elif (
        not common
        and not getattr(settings, "EXTERNAL_EMBEDDINGS_ENABLED", False)
    ):
        reasons["reindex_selected"] = "external_embeddings_disabled"
        reasons["reindex_needed"] = "external_embeddings_disabled"
    return reasons


def _values(data, name: str) -> list[str]:
    if hasattr(data, "getlist"):
        values = data.getlist(name)
    else:
        value = data.get(name, [])
        values = value if isinstance(value, list) else [value]
    return [str(value).strip() for value in values if str(value).strip()]


def normalize_selection(data) -> dict:
    folder_ids = sorted({int(value) for value in _values(data, "folder_ids") if value.isdigit()})
    pdf_ids = sorted({int(value) for value in _values(data, "pdf_ids") if value.isdigit()})
    indexed = str(data.get("filter_indexed", "")).strip().lower()
    if indexed not in {"", "true", "false"}:
        raise MaintenancePlanError("malformed_filters", "filter_indexed")
    result = {
        "folder_ids": folder_ids,
        "pdf_ids": pdf_ids,
        "filters": {
            "indexed": indexed,
            "category": str(data.get("filter_category", "")).strip()[:50],
            "subject": str(data.get("filter_subject", "")).strip()[:50],
            "keywords": sorted({
                word.strip().casefold()
                for word in str(data.get("filter_keywords", "")).split(",")
                if word.strip()
            }),
            "uploaded_after": str(data.get("filter_uploaded_after", "")).strip(),
            "uploaded_before": str(data.get("filter_uploaded_before", "")).strip(),
        },
    }
    for key in ("uploaded_after", "uploaded_before"):
        value = result["filters"][key]
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise MaintenancePlanError("malformed_filters", key) from exc
    if not folder_ids and not pdf_ids:
        raise MaintenancePlanError("empty_scope")
    return result


def _selection_query(selection: dict):
    query = PDFFile.objects.all()
    folder_ids = selection["folder_ids"]
    pdf_ids = selection["pdf_ids"]
    if folder_ids and pdf_ids:
        query = query.filter(Q(folder_id__in=folder_ids) | Q(pk__in=pdf_ids))
    elif folder_ids:
        query = query.filter(folder_id__in=folder_ids)
    else:
        query = query.filter(pk__in=pdf_ids)
    filters = selection["filters"]
    if filters["indexed"]:
        query = query.filter(indexed=filters["indexed"] == "true")
    if filters["category"]:
        query = query.filter(category=filters["category"])
    if filters["subject"]:
        query = query.filter(subject=filters["subject"])
    if filters["keywords"]:
        keywords = Q()
        for keyword in filters["keywords"]:
            keywords |= Q(keywords__icontains=keyword)
        query = query.filter(keywords)
    if filters["uploaded_after"]:
        query = query.filter(uploaded_at__date__gte=filters["uploaded_after"])
    if filters["uploaded_before"]:
        query = query.filter(uploaded_at__date__lte=filters["uploaded_before"])
    return query.order_by("pk")


def _source_digest(query) -> str:
    summary = query.aggregate(latest_upload=Max("uploaded_at"))
    payload = {
        "pdf_ids": list(query.values_list("pk", flat=True)),
        "latest_upload": str(summary["latest_upload"] or ""),
        "runtime_generation": getattr(settings, "RUNTIME_GENERATION_ID", ""),
        "runtime_manifest": getattr(settings, "RUNTIME_MANIFEST_DIGEST", ""),
    }
    try:
        from vaultops.models import SourceMutationState

        mutation = SourceMutationState.objects.using("control").filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id
        ).first()
        payload["mutation_epoch"] = mutation.current_epoch if mutation else 0
    except Exception:
        payload["mutation_epoch"] = 0
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def calculate_preview(operation: str, selection: dict) -> dict:
    if operation not in LOCAL_OPERATIONS:
        raise MaintenancePlanError("unknown_operation")
    query = _selection_query(selection)
    if operation == "reindex_needed":
        query = query.filter(indexed=False)
    pdf_ids = list(query.values_list("pk", flat=True))
    folder_rows = list(
        query.values("folder_id", "folder__name").order_by("folder_id").distinct()
    )
    folder_ids = [row["folder_id"] for row in folder_rows if row["folder_id"]]
    if operation == "repair_indexes":
        folder_ids = sorted(set(folder_ids) | set(selection["folder_ids"]))
    indexed_count = query.filter(indexed=True).count()
    return {
        "pdf_ids": pdf_ids,
        "folder_ids": folder_ids,
        "pdf_count": len(pdf_ids),
        "folder_count": len(folder_ids),
        "indexed_count": indexed_count,
        "needs_index_count": len(pdf_ids) - indexed_count,
        "affected_folders": [
            {"id": row["folder_id"], "name": row["folder__name"]}
            for row in folder_rows if row["folder_id"]
        ],
        "estimated_work_units": len(pdf_ids) + len(folder_ids),
    }


def create_plan(*, operation: str, data, actor, idempotency_key: str) -> MaintenancePlan:
    reason = capability_reasons().get(operation, "unknown_operation")
    if reason:
        raise MaintenancePlanError(reason)
    if not idempotency_key or len(idempotency_key) > 128:
        raise MaintenancePlanError("invalid_idempotency_key")
    existing = MaintenancePlan.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.created_by_id != actor.pk:
            raise MaintenancePlanError("idempotency_conflict")
        return existing
    selection = normalize_selection(data)
    preview = calculate_preview(operation, selection)
    if not preview["folder_ids"] and not preview["pdf_ids"]:
        raise MaintenancePlanError("empty_scope")
    source = _source_digest(_selection_query(selection))
    state_payload = {
        "operation": operation,
        "selection": selection,
        "preview": preview,
        "source_digest": source,
    }
    state_version = hashlib.sha256(
        json.dumps(state_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return MaintenancePlan.objects.create(
        operation=operation,
        selection=selection,
        preview=preview,
        source_digest=source,
        state_version=state_version,
        idempotency_key=idempotency_key,
        external_embeddings_required=operation in {
            "reindex_needed", "reindex_selected"
        },
        created_by=actor,
        expires_at=timezone.now() + timedelta(minutes=15),
    )


def queue_plan(*, plan: MaintenancePlan, actor, confirmation: str = "") -> MaintenanceJob:
    plan.refresh_from_db()
    if plan.created_by_id != actor.pk:
        raise MaintenancePlanError("plan_owner_mismatch")
    if plan.job_id:
        return plan.job
    if plan.state != "previewed" or plan.expires_at <= timezone.now():
        plan.state = "expired"
        plan.save(update_fields=["state", "updated_at"])
        raise MaintenancePlanError("plan_expired")
    reason = capability_reasons().get(plan.operation, "unknown_operation")
    if reason:
        raise MaintenancePlanError(reason)
    fresh_preview = calculate_preview(plan.operation, plan.selection)
    fresh_source = _source_digest(_selection_query(plan.selection))
    if fresh_preview != plan.preview or fresh_source != plan.source_digest:
        plan.state = "rejected"
        plan.save(update_fields=["state", "updated_at"])
        raise MaintenancePlanError("stale_plan")
    if (
        plan.operation == "reindex_selected"
        and confirmation.strip() != FORCE_CONFIRMATION
    ):
        raise MaintenancePlanError("typed_confirmation_required")

    with transaction.atomic():
        plan = MaintenancePlan.objects.select_for_update().get(pk=plan.pk)
        if plan.created_by_id != actor.pk:
            raise MaintenancePlanError("plan_owner_mismatch")
        if plan.job_id:
            return plan.job
        recovery = None
        if plan.operation != "validate":
            recovery = create_set("pre-bulk-maintenance")
        pdfs = PDFFile.objects.filter(pk__in=plan.preview["pdf_ids"]).order_by("pk")
        folders = Folder.objects.filter(pk__in=plan.preview["folder_ids"]).order_by("pk")
        if plan.operation == "repair_indexes":
            job = queue_job(
                kind=plan.operation,
                requested_by=actor,
                folders=folders,
                scope={"maintenance_plan": str(plan.public_id), **plan.selection},
                options={
                    "recovery_set_id": recovery["set_id"] if recovery else "",
                    "candidate_required": True,
                    "external_embeddings_required": False,
                },
            )
        else:
            job = queue_job(
                kind=plan.operation,
                requested_by=actor,
                pdfs=pdfs,
                scope={"maintenance_plan": str(plan.public_id), **plan.selection},
                options={
                    "recovery_set_id": recovery["set_id"] if recovery else "",
                    "candidate_required": plan.operation != "validate",
                    "external_embeddings_required": plan.external_embeddings_required,
                },
            )
        plan.job = job
        plan.state = "queued"
        plan.save(update_fields=["job", "state", "updated_at"])
        return job


def workbench_maintenance_state() -> dict:
    reasons = capability_reasons()
    recovery_sets = list_sets()
    cleanup = plan_prune()
    local_cleanup = cleanup_plan()
    local_inventory = inventory_local_artifacts()
    maintenance_workspaces = [
        item for item in local_inventory
        if item["category"] == "maintenance_workspace"
    ]
    return {
        "capabilities": {
            operation: {"enabled": not reason, "reason_code": reason}
            for operation, reason in reasons.items()
        },
        "folders": list(
            Folder.objects.annotate(pdf_count=Count("files")).values(
                "id", "name", "pdf_count"
            )
        ),
        "plans": list(
            MaintenancePlan.objects.select_related("job").values(
                "public_id", "operation", "state", "preview", "state_version",
                "expires_at",
                "job__public_id", "job__status",
            )[:20]
        ),
        "jobs": list(
            MaintenanceJob.objects.filter(kind__in=LOCAL_OPERATIONS).values(
                "public_id", "kind", "status", "total_items", "completed_items",
                "failed_items", "error_summary", "created_at",
            )[:20]
        ),
        "confirmation_phrase": FORCE_CONFIRMATION,
        "health": {
            "local_recovery_sets": {
                "state": "degraded" if cleanup["blocked"] else "healthy",
                "verified_count": sum(
                    item.get("verification_state") == "verified"
                    for item in recovery_sets
                ),
                "bytes": sum(
                    sum(
                        db.get("bytes", 0)
                        for db in item.get("databases", {}).values()
                    )
                    for item in recovery_sets
                ),
                "held_bytes": sum(
                    sum(
                        db.get("bytes", 0)
                        for db in item.get("databases", {}).values()
                    )
                    for item in recovery_sets
                    if item.get("retention", {}).get("incident_hold")
                ),
                "prunable_bytes": cleanup["candidate_bytes"],
            },
            "maintenance_workspaces": {
                "state": (
                    "disabled"
                    if reasons.get("reindex_needed") == "runtime_read_only"
                    else "available"
                ),
                "active_count": len(maintenance_workspaces),
                "activation_ready_count": sum(
                    item["state"] == "activation_ready"
                    for item in maintenance_workspaces
                ),
            },
            "cleanup": {
                "state": (
                    "blocked"
                    if cleanup["blocked"] or not local_cleanup["apply_allowed"]
                    else "planned"
                ),
                "plan_id": local_cleanup["plan_id"],
                "prunable_bytes": local_cleanup["candidate_bytes"],
                "protected_bytes": local_cleanup["protected_bytes"],
            },
            "last_restore_drill": {"state": "unknown"},
        },
    }
