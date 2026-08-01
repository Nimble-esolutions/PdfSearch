"""Small server-rendered Data Operations surface.

The page is deliberately useful before the full worker migration lands: it
shows fresh control-plane observations and queues only bounded, idempotent
operations. Existing maintenance services remain the execution authority.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.maintenance import queue_job
from core.models import MaintenanceJob, PDFFile

from .config import resolve_profiles, resolve_setting
from .models import DataOperation, DataProfile, DataOpsAuditEvent, RecoveryPoint


def _can_act(request):
    return bool(getattr(request.user, "is_superuser", False))


def _profile_state():
    try:
        profiles = [profile.redacted() for profile in resolve_profiles()]
    except Exception as exc:  # configuration is rendered as evidence, not a boot crash
        profiles = []
        config_error = str(exc)
    else:
        config_error = ""
    return profiles, config_error


def _state(request):
    profiles, config_error = _profile_state()
    pending = MaintenanceJob.objects.filter(status__in=("queued", "running", "retrying")).count()
    documents = PDFFile.objects.count()
    latest = RecoveryPoint.objects.using("control").filter(state=RecoveryPoint.State.VERIFIED).first()
    recent = list(DataOperation.objects.using("control").all()[:8])
    issues = []
    if config_error:
        issues.append({"label": "Configuration needs review", "code": "dataops_config_invalid"})
    return {
        "posture": "attention" if issues else "ready",
        "status_label": "Configuration needs review" if issues else "Data is ready",
        "condition": "Routine repairs are bounded and recorded." if not issues else config_error,
        "automatic_response": "No automatic action running" if not pending else f"{pending} maintenance item(s) in progress",
        "observed_at": datetime.now(timezone.utc),
        "last_verified_at": datetime.now(timezone.utc),
        "environment": getattr(request, "environment", ""),
        "issues": issues,
        "refresh": {"documents": documents, "index_status": "observed", "pending": pending, "note": "Repairs are limited by the configured run and daily budgets."},
        "backup": {"latest": {"id": latest.release_id, "verified_at": latest.updated_at, "objects": latest.counts.get("objects", "—")} if latest else None, "recovery_points": []},
        "restore": {"candidate": None},
        "configuration": {"backup_profile": resolve_setting("DATAOPS_BACKUP_PROFILE", default="")[0], "restore_profile": resolve_setting("DATAOPS_RESTORE_PROFILE", default="")[0], "mode_label": resolve_setting("DATAOPS_BACKUP_MODE", default="manual")[0], "env_locked": bool(profiles)},
        "history": {"count": len(recent), "items": [{"label": item.get_kind_display(), "status_label": item.get_state_display(), "started_at": item.created_at, "receipt_id": str(item.public_id)} for item in recent]},
        "profiles": profiles,
        "permissions": {"can_refresh": _can_act(request), "can_backup": _can_act(request), "can_restore": _can_act(request), "can_configure": _can_act(request), "can_export_env": _can_act(request)},
    }


@login_required
def workbench(request):
    state = _state(request)
    return render(request, "dataops/workbench.html", {"state": state, "state_url": reverse("dataops:state"), "idempotency_key": secrets.token_urlsafe(18)})


@login_required
@require_GET
def state_api(request):
    return JsonResponse(_state(request), safe=True)


def _queue(request, kind, maintenance_kind):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    job = queue_job(kind=maintenance_kind, requested_by=request.user)
    operation = DataOperation.objects.using("control").create(kind=kind, state=DataOperation.State.QUEUED, request_id=str(job.public_id))
    DataOpsAuditEvent.objects.using("control").create(actor_id=request.user.pk, actor_name=request.user.get_username(), action=kind, operation_id=operation.public_id, outcome="queued")
    return redirect("dataops:workbench")


@login_required
@require_POST
def refresh(request):
    return _queue(request, DataOperation.Kind.REINDEX, "repair_indexes")


@login_required
@require_POST
def backup(request):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    operation = DataOperation.objects.using("control").create(kind=DataOperation.Kind.BACKUP, state=DataOperation.State.QUEUED, idempotency_key=request.POST.get("idempotency_key", ""))
    DataOpsAuditEvent.objects.using("control").create(actor_id=request.user.pk, actor_name=request.user.get_username(), action="backup", operation_id=operation.public_id, outcome="queued")
    return redirect("dataops:workbench")


@login_required
@require_POST
def restore(request):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    operation = DataOperation.objects.using("control").create(kind=DataOperation.Kind.RESTORE, state=DataOperation.State.WAITING_APPROVAL, profile_key=request.POST.get("recovery_point_id", ""))
    DataOpsAuditEvent.objects.using("control").create(actor_id=request.user.pk, actor_name=request.user.get_username(), action="restore", operation_id=operation.public_id, outcome="waiting_approval")
    return redirect("dataops:workbench")


@login_required
def configuration(request):
    profiles, error = _profile_state()
    return render(request, "dataops/configuration.html", {"profiles": profiles, "error": error})


@login_required
def env_patch(request):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    lines = ["# Generated by Data Operations; review and apply in Dokploy.", "# ENV is authoritative; secrets are intentionally omitted.", "DATAOPS_ENABLED=1"]
    for profile in resolve_profiles():
        token = profile.key.upper()
        lines.extend([f"DATAOPS_PROFILE_{token}_ROLE={profile.role}", f"DATAOPS_PROFILE_{token}_BUCKET={profile.bucket}", f"DATAOPS_PROFILE_{token}_DATASET_ID={profile.dataset_id}"])
    response = HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="dataops.env.patch"'
    return response
