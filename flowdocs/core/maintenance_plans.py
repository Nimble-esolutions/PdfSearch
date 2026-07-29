"""Preview-and-confirm contract for local document and index maintenance."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .emergency_recovery import create_set, list_sets, plan_prune
from .artifact_cleanup import (
    CleanupError,
    capacity_report,
    cleanup_plan,
    inventory_local_artifacts,
)
from .maintenance import queue_job
from .search_artifact_health import classify_search_artifacts
from .models import (
    Folder,
    MaintenanceAuditEvent,
    MaintenanceJob,
    MaintenancePlan,
    PDFFile,
)
from .worker_readiness import maintenance_worker_capability

LOCAL_OPERATIONS = {
    "validate",
    "repair_indexes",
    "reindex_needed",
    "reindex_selected",
}
FORCE_CONFIRMATION = "REINDEX SELECTED"
MAX_SELECTION_IDS = 5000
# A plan is persisted in the control database and later used to construct the
# queue scope.  Keep that persisted payload deliberately bounded.  Repairing
# stored indexes is folder-scoped and does not need document ids, so it may
# report a larger document count without serialising every id.
MAX_PREVIEW_PDFS = 5000


class MaintenancePlanError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        # Keep the stable typed code at the front of the message while still
        # exposing a safe field-level detail for operator diagnostics.
        super().__init__(
            f"{reason_code}: {detail}" if detail else reason_code
        )


def _vault_health() -> tuple[dict, dict]:
    from vaultops.models import (
        ArtifactGeneration,
        ArtifactValidation,
        RestoreWorkspace,
    )

    now = timezone.now()
    verified_generation_ids = set(
        ArtifactValidation.objects.filter(
            status=ArtifactValidation.Status.PASSED,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values_list("generation_id", flat=True)
    )
    generations = ArtifactGeneration.objects.filter(
        pk__in=verified_generation_ids,
    ).exclude(
        vault_state__in=[
            ArtifactGeneration.VaultState.INVALID,
            ArtifactGeneration.VaultState.UNKNOWN,
            ArtifactGeneration.VaultState.LEGACY_READ_ONLY,
        ]
    )
    latest_drill = (
        RestoreWorkspace.objects.exclude(rehearsal_evidence={})
        .order_by("-updated_at")
        .first()
    )
    drill = {"state": "unknown", "observed_at": None, "workspace_id": ""}
    if latest_drill:
        drill = {
            "state": (
                "passed"
                if latest_drill.rehearsal_evidence.get("success")
                else "failed"
            ),
            "observed_at": latest_drill.updated_at,
            "workspace_id": str(latest_drill.public_id),
        }
    return (
        {
            "state": "healthy" if generations.exists() else "unknown",
            "verified_count": generations.count(),
            "authoritative_count": generations.filter(
                vault_state=ArtifactGeneration.VaultState.AUTHORITATIVE
            ).count(),
        },
        drill,
    )


def capability_reasons() -> dict[str, str]:
    """Return the server-authoritative gate reason for every local operation.

    Validation and stored-index repair only need local maintenance readiness;
    document reindexing additionally needs embeddings, while force-reindexing
    has its own explicit authorization flag.  Keep these prerequisites
    independent so one disabled flag cannot accidentally mask another.
    """
    common = ""
    if getattr(settings, "ACTIVE_RUNTIME", None) is not None:
        common = "runtime_read_only"
    elif not getattr(settings, "LOCAL_INDEX_MAINTENANCE_ENABLED", False):
        common = "bulk_reindex_disabled"
    elif (
        getattr(settings, "MAINTENANCE_WORKER_READINESS_REQUIRED", True)
        and not maintenance_worker_capability()["available"]
    ):
        common = "maintenance_worker_unavailable"
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

    reasons = {operation: common for operation in LOCAL_OPERATIONS}
    if common:
        return reasons

    # ``reindex_needed`` calls the embedding provider for missing artifacts.
    # Force-reindexing has both this prerequisite and a separate operator gate.
    if not getattr(settings, "EXTERNAL_EMBEDDINGS_ENABLED", False):
        reasons["reindex_needed"] = "external_embeddings_disabled"
        reasons["reindex_selected"] = "external_embeddings_disabled"
    if not getattr(settings, "FORCE_REINDEX_ENABLED", False):
        # Prefer the force gate when both selected-reindex prerequisites are
        # unavailable: it is the first explicit authorization the operator
        # must grant, while ``reindex_needed`` remains independently gated.
        reasons["reindex_selected"] = "bulk_reindex_disabled"
    return reasons


def _values(data, name: str) -> list[str]:
    if hasattr(data, "getlist"):
        values = data.getlist(name)
    else:
        value = data.get(name, [])
        values = value if isinstance(value, list) else [value]
    return [str(value).strip() for value in values if str(value).strip()]


def normalize_selection(data) -> dict:
    raw_folder_ids = _values(data, "folder_ids")
    raw_pdf_ids = _values(data, "pdf_ids")
    if (
        len(raw_folder_ids) > MAX_SELECTION_IDS
        or len(raw_pdf_ids) > MAX_SELECTION_IDS
    ):
        raise MaintenancePlanError("selection_too_large")
    folder_ids = sorted({int(value) for value in raw_folder_ids if value.isdigit()})
    pdf_ids = sorted({int(value) for value in raw_pdf_ids if value.isdigit()})
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
    parsed_dates = {}
    for key in ("uploaded_after", "uploaded_before"):
        value = result["filters"][key]
        if value:
            try:
                parsed_dates[key] = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise MaintenancePlanError("malformed_filters", key) from exc
    if (
        parsed_dates.get("uploaded_after")
        and parsed_dates.get("uploaded_before")
        and parsed_dates["uploaded_after"] > parsed_dates["uploaded_before"]
    ):
        raise MaintenancePlanError("malformed_filters", "uploaded_date_range")
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
    digest = hashlib.sha256()
    # Stream secret-free hashes of stored artifact state. Only the aggregate
    # digest is persisted; document text and embedding values never leave the
    # database or enter previews, logs, or audit records.
    fields = (
        "pk", "folder_id", "file", "file_path", "lifecycle", "indexed",
        "uploaded_at", "category", "subject", "keywords", "extracted_text",
        "text_content", "page_chunks", "chunk_embeddings",
    )
    for row in query.values_list(*fields).iterator(chunk_size=250):
        artifact = dict(zip(fields, row))
        for key in (
            "keywords", "extracted_text", "text_content",
            "page_chunks", "chunk_embeddings",
        ):
            artifact[key] = hashlib.sha256(
                json.dumps(
                    artifact[key],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        digest.update(
            json.dumps(
                artifact, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        )
        digest.update(b"\n")
    payload = {
        "selection_digest": digest.hexdigest(),
        "match_count": query.count(),
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
    artifact_reason_counts: dict[str, int] = {}
    blocked_count = 0
    repair_only_count = 0
    if operation == "reindex_needed":
        needed_ids: list[int] = []
        candidates = query.filter(
            lifecycle__in=("uploaded", "processing", "ready")
        ).only(
            "pk", "file", "lifecycle", "indexed",
            "page_chunks", "chunk_embeddings",
        )
        for scanned, pdf in enumerate(
            candidates.iterator(chunk_size=250), start=1
        ):
            if scanned > MAX_PREVIEW_PDFS:
                raise MaintenancePlanError("selection_too_large")
            try:
                media_exists = bool(
                    pdf.file
                    and pdf.file.name
                    and pdf.file.storage.exists(pdf.file.name)
                )
            except (OSError, ValueError):
                media_exists = False
            health = classify_search_artifacts(
                lifecycle=pdf.lifecycle,
                indexed=pdf.indexed,
                media_exists=media_exists,
                page_chunks=pdf.page_chunks,
                chunk_embeddings=pdf.chunk_embeddings,
            )
            for reason_code in health.reason_codes:
                artifact_reason_counts[reason_code] = (
                    artifact_reason_counts.get(reason_code, 0) + 1
                )
            blocked_count += int(health.blocking)
            repair_only_count += int(health.repair_required)
            if health.reindex_required:
                needed_ids.append(pdf.pk)
        query = query.filter(pk__in=needed_ids)
    pdf_count = query.count()
    # Repair workers consume folders and rebuild one index per folder; they do
    # not need a document-id payload. Other operations queue individual PDFs,
    # therefore fail deterministically before serialising an oversized scope.
    if operation != "repair_indexes" and pdf_count > MAX_PREVIEW_PDFS:
        raise MaintenancePlanError("selection_too_large")
    pdf_ids = (
        []
        if operation == "repair_indexes"
        else list(query.values_list("pk", flat=True))
    )
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
        "pdf_count": pdf_count,
        "folder_count": len(folder_ids),
        "indexed_count": indexed_count,
        "needs_index_count": pdf_count - indexed_count,
        "affected_folders": [
            {"id": row["folder_id"], "name": row["folder__name"]}
            for row in folder_rows if row["folder_id"]
        ],
        "estimated_work_units": len(pdf_ids) + len(folder_ids),
        "blocked_count": blocked_count,
        "repair_only_count": repair_only_count,
        "artifact_reason_counts": artifact_reason_counts,
    }


def _preview_source_query(selection: dict, preview: dict):
    query = _selection_query(selection)
    pdf_ids = preview.get("pdf_ids", [])
    if pdf_ids:
        return query.filter(pk__in=pdf_ids)
    if preview.get("folder_ids"):
        return query.filter(folder_id__in=preview["folder_ids"])
    return query.none()


def create_plan(*, operation: str, data, actor, idempotency_key: str) -> MaintenancePlan:
    reason = capability_reasons().get(operation, "unknown_operation")
    if reason:
        raise MaintenancePlanError(reason)
    if not idempotency_key or len(idempotency_key) > 128:
        raise MaintenancePlanError("invalid_idempotency_key")
    selection = normalize_selection(data)
    existing = MaintenancePlan.objects.filter(
        idempotency_key=idempotency_key, operation=operation
    ).first()
    if existing:
        if existing.created_by_id != actor.pk or existing.selection != selection:
            raise MaintenancePlanError("idempotency_conflict")
        return existing
    preview = calculate_preview(operation, selection)
    if not preview["folder_ids"] and not preview["pdf_ids"]:
        raise MaintenancePlanError("empty_scope")
    source = _source_digest(_preview_source_query(selection, preview))
    state_payload = {
        "operation": operation,
        "selection": selection,
        "preview": preview,
        "source_digest": source,
    }
    state_version = hashlib.sha256(
        json.dumps(state_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
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
    except IntegrityError:
        collision = MaintenancePlan.objects.filter(
            idempotency_key=idempotency_key,
            operation=operation,
        ).first()
        if collision is None:
            raise
        if collision.created_by_id != actor.pk or collision.selection != selection:
            raise MaintenancePlanError("idempotency_conflict")
        return collision


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
    fresh_source = _source_digest(
        _preview_source_query(plan.selection, fresh_preview)
    )
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


def _job_state_version(job: dict) -> str:
    payload = {
        "public_id": str(job["public_id"]),
        "status": job["status"],
        "completed_items": job["completed_items"],
        "failed_items": job["failed_items"],
        "updated_at": job["updated_at"].isoformat(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _serialize_local_job_payload(
    job: MaintenanceJob | dict,
    *,
    prepared_workspace_by_job=None,
    include_audit=False,
) -> dict:
    prepared_workspace_by_job = prepared_workspace_by_job or {}
    payload = {
        "public_id": str(job.public_id if hasattr(job, "public_id") else job["public_id"]),
        "kind": str(job.kind if hasattr(job, "kind") else job["kind"]),
        "status": str(job.status if hasattr(job, "status") else job["status"]),
        "total_items": int(job.total_items if hasattr(job, "total_items") else job["total_items"]),
        "completed_items": int(job.completed_items if hasattr(job, "completed_items") else job["completed_items"]),
        "failed_items": int(job.failed_items if hasattr(job, "failed_items") else job["failed_items"]),
        # Keep raw error_summary server-side for the existing audit/support
        # contract. Browser read models expose only a bounded stable code.
        "safe_error_code": (
            "maintenance_job_failed"
            if str(
                job.error_summary
                if hasattr(job, "error_summary")
                else job["error_summary"]
            )
            else ""
        ),
        "updated_at": (
            job.updated_at.isoformat() if hasattr(job, "updated_at")
            else job["updated_at"].isoformat()
        ),
        "options": job.options if hasattr(job, "options") else job.get("options", {}),
    }
    payload["state_version"] = _job_state_version(
        {
            "public_id": payload["public_id"],
            "status": payload["status"],
            "completed_items": payload["completed_items"],
            "failed_items": payload["failed_items"],
            "updated_at": (job.updated_at if hasattr(job, "updated_at") else job["updated_at"]),
        }
    )
    payload["allowed_actions"] = {
        "cancel": payload["status"] in {"queued", "running"},
        "retry": payload["status"] == "failed",
    }
    candidate_reason = ""
    prepared_workspace_id = ""
    if payload["status"] != "completed":
        candidate_reason = "job_not_completed"
    elif payload["options"].get("candidate_state") != "activation_ready":
        candidate_reason = "candidate_not_ready"
    else:
        prepared_workspace_id = str(
            prepared_workspace_by_job.get(payload["public_id"]) or ""
        )
        if not prepared_workspace_id and not (
            settings.MAINTENANCE_CANDIDATE_PREPARATION_ENABLED
        ):
            candidate_reason = "candidate_preparation_disabled"
        elif not prepared_workspace_id and settings.ENV_IDENTITY.is_production:
            candidate_reason = "runtime_read_only"
    payload["allowed_actions"]["prepare_activation"] = (
        not candidate_reason and not prepared_workspace_id
    )
    payload["allowed_actions"]["review_activation"] = bool(
        prepared_workspace_id
    )
    payload["allowed_actions"]["candidate_reason"] = candidate_reason
    payload["prepared_workspace_id"] = prepared_workspace_id
    if include_audit and hasattr(job, "id"):
        payload["audit_events"] = [
            {
                "event_type": event.event_type,
                "created_at": event.created_at.isoformat(),
                "safe_error_code": str(event.payload.get("error_code") or ""),
                "evidence_id": str(
                    event.payload.get("item_id")
                    or event.payload.get("folder_id")
                    or event.payload.get("pdf_id")
                    or ""
                ),
                "actor": event.actor.username if event.actor else None,
            }
            for event in MaintenanceAuditEvent.objects.filter(
                job=job
            ).order_by("created_at")
        ]
    else:
        payload["audit_events"] = []
    return payload


def _prepared_workspace_ids(job_records) -> dict[str, str]:
    from vaultops.models import ArtifactGeneration, RestoreWorkspace

    eligible_job_ids = {
        str(job.public_id)
        for job in job_records
        if (
            job.status == "completed"
            and job.options.get("candidate_state") == "activation_ready"
        )
    }
    if not eligible_job_ids:
        return {}

    prepared_by_job = {}
    workspaces = (
        RestoreWorkspace.objects.filter(
            generation__origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE,
            generation__lineage_job_public_id__in=eligible_job_ids,
            state=RestoreWorkspace.State.ACTIVATION_READY,
        )
        .order_by(
            "generation__lineage_job_public_id",
            "-prepared_at",
            "-pk",
        )
        .values_list(
            "generation__lineage_job_public_id",
            "public_id",
        )
    )
    for job_id, workspace_id in workspaces:
        prepared_by_job.setdefault(str(job_id), str(workspace_id))
    return prepared_by_job


def workbench_maintenance_state(
    *, selected_plan_id="", selected_job_id=""
) -> dict:
    reasons = capability_reasons()
    worker = maintenance_worker_capability()
    recovery_sets = list_sets()
    cleanup = plan_prune()
    try:
        local_cleanup = cleanup_plan()
        local_inventory = inventory_local_artifacts()
        cleanup_inventory_reason = ""
    except (CleanupError, OSError):
        local_cleanup = {
            "plan_id": "",
            "candidate_bytes": 0,
            "protected_bytes": 0,
            "apply_allowed": False,
        }
        local_inventory = []
        cleanup_inventory_reason = "cleanup_inventory_unavailable"
    maintenance_workspaces = [
        item for item in local_inventory
        if item["category"] == "maintenance_workspace"
    ]
    verified_vault_generations, last_restore_drill = _vault_health()
    try:
        free_space = capacity_report(source_bytes=0, operation="health")
    except (FileNotFoundError, OSError):
        free_space = {
            "byte_capacity_ok": False,
            "inode_capacity_ok": False,
            "free_bytes": 0,
            "required_bytes": 0,
            "free_inodes": 0,
            "inode_reserve": 0,
        }
    job_records = list(
        MaintenanceJob.objects.filter(kind__in=LOCAL_OPERATIONS).order_by(
            "-created_at", "-pk"
        )[:20]
    )
    selected_job_id = str(selected_job_id).strip()
    selected_job_record = next(
        (
            job for job in job_records
            if selected_job_id and str(job.public_id) == selected_job_id
        ),
        None,
    )
    if selected_job_id and selected_job_record is None:
        selected_job_record = MaintenanceJob.objects.filter(
            public_id=selected_job_id,
            kind__in=LOCAL_OPERATIONS,
        ).first()
    workspace_job_records = list(job_records)
    if (
        selected_job_record is not None
        and selected_job_record not in workspace_job_records
    ):
        workspace_job_records.append(selected_job_record)
    prepared_workspace_by_job = _prepared_workspace_ids(workspace_job_records)
    jobs = [
        _serialize_local_job_payload(
            job,
            prepared_workspace_by_job=prepared_workspace_by_job,
        )
        for job in job_records
    ]
    plans = list(
        MaintenancePlan.objects.select_related("job").values(
            "public_id", "operation", "state", "preview", "state_version",
            "expires_at", "external_embeddings_required",
            "job__public_id", "job__status",
        )[:20]
    )
    selected_plan = next(
        (
            plan for plan in plans
            if selected_plan_id and str(plan["public_id"]) == str(selected_plan_id)
        ),
        None,
    )
    selected_job = None
    if selected_job_id:
        selected_job = next(
            (
                job
                for job in jobs
                if str(job["public_id"]) == selected_job_id
            ),
            None,
        )
        if selected_job is None and selected_job_record is not None:
            selected_job = _serialize_local_job_payload(
                selected_job_record,
                prepared_workspace_by_job=prepared_workspace_by_job,
                include_audit=True,
            )
        if selected_job and not selected_job["audit_events"]:
            selected_job["audit_events"] = [
                {
                    "event_type": event["event_type"],
                    "created_at": event["created_at"].isoformat(),
                    "safe_error_code": str(
                        event["payload"].get("error_code") or ""
                    ),
                    "evidence_id": str(
                        event["payload"].get("item_id")
                        or event["payload"].get("folder_id")
                        or event["payload"].get("pdf_id")
                        or ""
                    ),
                    "actor": event["actor__username"],
                }
                for event in MaintenanceAuditEvent.objects.filter(
                    job__public_id=selected_job_id
                )
                .order_by("created_at")
                .values("event_type", "created_at", "payload", "actor__username")
            ]
    version_payload = {
        "plans": [
            (str(plan["public_id"]), plan["state"], plan["state_version"])
            for plan in plans
        ],
        "jobs": [
            (str(job["public_id"]), job["state_version"])
            for job in jobs
        ],
        "capabilities": reasons,
    }
    state_version = hashlib.sha256(
        json.dumps(version_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "state_version": state_version,
        "capabilities": {
            operation: {"enabled": not reason, "reason_code": reason}
            for operation, reason in reasons.items()
        },
        "worker": worker,
        "folders": list(
            Folder.objects.annotate(pdf_count=Count("files")).values(
                "id", "name", "pdf_count"
            )
        ),
        "plans": plans,
        "selected_plan": selected_plan,
        "jobs": jobs,
        "selected_job": selected_job,
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
            "verified_vault_generations": verified_vault_generations,
            "free_space_reserve": {
                "state": (
                    "healthy"
                    if (
                        free_space["byte_capacity_ok"]
                        and free_space["inode_capacity_ok"]
                    )
                    else "degraded"
                ),
                "free_bytes": free_space["free_bytes"],
                "required_bytes": free_space["required_bytes"],
                "free_inodes": free_space["free_inodes"],
                "required_inodes": free_space["inode_reserve"],
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
                "reason_code": cleanup_inventory_reason,
            },
            "last_restore_drill": last_restore_drill,
        },
    }
