"""Small server-rendered Data Operations surface.

The page is deliberately useful before the full worker migration lands: it
shows fresh control-plane observations and queues only bounded, idempotent
operations. Existing maintenance services remain the execution authority.
"""

from __future__ import annotations

import secrets
import os
import hashlib
from datetime import datetime, timezone

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.maintenance import queue_job
from core.maintenance_plans import workbench_maintenance_state
from core.models import MaintenanceJob, PDFFile

from .config import resolve_profiles, resolve_selectors, resolve_setting, validate_profiles
from .models import DataOperation, DataProfile, DataOpsAuditEvent, RecoveryPoint
from .pipeline import DataOpsPipelineError, preflight_operation, resolve_operation_route
from .readiness import readiness_payload
from .storage import StorageConfigurationError


def _can_act(request):
    return bool(getattr(request.user, "is_superuser", False))


def _resolved_profile_state():
    try:
        resolved = resolve_profiles()
        selectors = resolve_selectors()
        issues = list(validate_profiles(resolved, selectors=selectors, operation="backup", credential_environment=os.environ))
        issues.extend(validate_profiles(resolved, selectors=selectors, operation="restore", credential_environment=os.environ))
    except Exception as exc:  # configuration is rendered as evidence, not a boot crash
        resolved = ()
        selectors = {}
        issues = []
        config_error = str(exc)
    else:
        config_error = ""
    return resolved, selectors, tuple({issue.code: issue for issue in issues}.values()), config_error


def _profile_state():
    resolved, _selectors, issues, error = _resolved_profile_state()
    return [profile.redacted() for profile in resolved], "; ".join(issue.message for issue in issues) if issues else error


def _state(request):
    resolved, selectors, configuration_issues, config_error = _resolved_profile_state()
    profiles = [profile.redacted() for profile in resolved]
    pending = MaintenanceJob.objects.filter(status__in=("queued", "running", "retrying")).count()
    pending += DataOperation.objects.using("control").filter(state__in=(DataOperation.State.QUEUED, DataOperation.State.RUNNING)).count()
    documents = PDFFile.objects.count()
    latest = RecoveryPoint.objects.using("control").filter(state=RecoveryPoint.State.VERIFIED).first()
    recent = list(DataOperation.objects.using("control").all()[:8])
    issues = []
    if config_error:
        issues.append({"label": "Configuration needs review", "code": "dataops_config_invalid"})
    issues.extend({"label": issue.message, "code": issue.code} for issue in configuration_issues)
    readiness = readiness_payload()
    successful_backup = DataOperation.objects.using("control").filter(kind=DataOperation.Kind.BACKUP, state=DataOperation.State.SUCCEEDED).first()
    successful_restore = DataOperation.objects.using("control").filter(kind=DataOperation.Kind.RESTORE, state=DataOperation.State.SUCCEEDED).first()
    latest_points = RecoveryPoint.objects.using("control").filter(state=RecoveryPoint.State.VERIFIED)[:12]
    try:
        maintenance = workbench_maintenance_state(
            selected_plan_id=request.GET.get("plan", ""),
            selected_job_id=request.GET.get("job", ""),
        )
    except Exception:
        # The advanced local controls are additive. A control-plane observation
        # failure must not hide the profile workflow or turn the page into a
        # 500 response.
        maintenance = {
            "state_version": "",
            "capabilities": {},
            "folders": [],
            "plans": [],
            "jobs": [],
            "selected_plan": None,
            "selected_job": None,
        }
    maintenance_version = maintenance.get("state_version", "")
    profile_cards = []
    for profile in profiles:
        key = profile["key"]
        profile_cards.append({
            **profile,
            "selected_for_backup": key == (selectors.get("backup_destination") or selectors.get("backup")),
            "selected_for_restore": key == (selectors.get("restore_source") or selectors.get("restore")),
            "health": "configured" if not configuration_issues else "attention",
            "last_successful_operation": next(({"id": str(item.public_id), "kind": item.kind, "finished_at": item.finished_at} for item in recent if item.state == DataOperation.State.SUCCEEDED and item.profile_key == key), None),
        })
    return {
        "posture": "ready" if readiness.get("status") in {"ok", "not_configured"} and not issues else "attention",
        "status_label": "Data is ready" if readiness.get("status") in {"ok", "not_configured"} and not issues else "Configuration needs review",
        "condition": "The automatic pipeline is available." if not issues else (config_error or "Review the highlighted profile configuration."),
        "automatic_response": "No automatic action running" if not pending else f"{pending} maintenance item(s) in progress",
        "observed_at": datetime.now(timezone.utc),
        "last_verified_at": datetime.now(timezone.utc),
        "environment": getattr(getattr(getattr(settings, "ENV_IDENTITY", None), "app_env", ""), "value", ""),
        "issues": issues,
        "refresh": {"documents": documents, "index_status_label": "Observed", "pending": pending, "note": "Repairs are limited by the configured run and daily budgets."},
        "backup": {"latest": {"id": (successful_backup.result or {}).get("release_id", successful_backup.release_id) if successful_backup else (latest.release_id if latest else ""), "verified_at": successful_backup.finished_at if successful_backup else (latest.updated_at if latest else None), "objects": ((successful_backup.result or {}).get("transfer", {}) or {}).get("objects", latest.counts.get("objects", "—") if latest else "—")} if (successful_backup or latest) else None, "recovery_points": [{"id": point.release_id, "profile_key": point.profile_key, "label": f"{point.release_id} · {point.profile_key}"} for point in latest_points]},
        "restore": {"candidate": None, "last_successful": readiness.get("last_restore") or (str(successful_restore.public_id) if successful_restore else "")},
        "configuration": {"backup_profile": selectors.get("backup_destination") or selectors.get("backup") or resolve_setting("DATAOPS_BACKUP_PROFILE", default="")[0], "restore_profile": selectors.get("restore_source") or selectors.get("restore") or resolve_setting("DATAOPS_RESTORE_PROFILE", default="")[0], "mode_label": resolve_setting("DATAOPS_BACKUP_MODE", default="manual")[0], "env_locked": any(profile.effective_source == "environment" for profile in resolved), "backup_source": resolve_setting("DATAOPS_BACKUP_PROFILE", default="")[1], "restore_source": resolve_setting("DATAOPS_RESTORE_PROFILE", default="")[1]},
        "readiness": readiness,
        "maintenance": maintenance,
        "maintenance_state_version": maintenance_version,
        "combined_state_version": hashlib.sha256(
            (readiness.get("manifest_digest", "") + ":" + maintenance_version).encode("utf-8")
        ).hexdigest(),
        "profile_cards": profile_cards,
        "history": {"count": len(recent), "items": [{"label": item.get_kind_display(), "status_label": item.get_state_display(), "started_at": item.created_at, "stage": item.pipeline_stage, "error_code": item.error_code, "receipt_id": str(item.public_id), "timeline": (item.result or {}).get("stages", [])} for item in recent]},
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
    try:
        job = queue_job(kind=maintenance_kind, requested_by=request.user)
    except Exception as exc:
        # A disabled worker or safety gate is an operator-visible blocked state,
        # not an unhandled server error.
        return HttpResponse(f"Operation blocked: {getattr(exc, 'reason_code', 'maintenance_unavailable')}", status=409)
    operation, _ = DataOperation.objects.using("control").get_or_create(
        kind=kind,
        idempotency_key=request.POST.get("idempotency_key", ""),
        defaults={"state": DataOperation.State.QUEUED, "request_id": str(job.public_id)},
    )
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
    destination = request.POST.get("destination_profile", "").strip().lower()
    source = request.POST.get("source_profile", "").strip().lower()
    idempotency_key = request.POST.get("idempotency_key", "").strip() or secrets.token_urlsafe(18)
    operation, _ = DataOperation.objects.using("control").get_or_create(
        kind=DataOperation.Kind.BACKUP,
        idempotency_key=idempotency_key,
        defaults={
            "state": DataOperation.State.QUEUED,
            "profile_key": destination,
            "source_profile_key": source,
            "destination_profile_key": destination,
            "release_id": request.POST.get("release_id", "").strip(),
            "checkpoint": {"pipeline": "preflight", "source_override": source, "destination_override": destination},
        },
    )
    DataOpsAuditEvent.objects.using("control").create(actor_id=request.user.pk, actor_name=request.user.get_username(), action="backup", operation_id=operation.public_id, outcome="queued")
    return redirect("dataops:workbench")


@login_required
@require_POST
def restore(request):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    source = request.POST.get("source_profile", "").strip().lower()
    destination = request.POST.get("destination_profile", "").strip().lower()
    release_id = request.POST.get("release_id", "").strip() or request.POST.get("recovery_point_id", "").strip()
    operation, _ = DataOperation.objects.using("control").get_or_create(
        kind=DataOperation.Kind.RESTORE,
        idempotency_key=request.POST.get("idempotency_key", "") or secrets.token_urlsafe(18),
        defaults={
            "state": DataOperation.State.QUEUED,
            "profile_key": source,
            "source_profile_key": source,
            "destination_profile_key": destination,
            "release_id": release_id,
            "checkpoint": {"pipeline": "preflight", "source_override": source, "destination_override": destination},
        },
    )
    DataOpsAuditEvent.objects.using("control").create(actor_id=request.user.pk, actor_name=request.user.get_username(), action="restore", operation_id=operation.public_id, profile_key=source, outcome="queued", evidence={"release_id": release_id, "destination_profile": destination})
    return redirect("dataops:workbench")


@login_required
@require_GET
def preflight(request):
    operation = request.GET.get("operation", "restore").strip().lower()
    source_key = request.GET.get("source_profile", "").strip().lower()
    destination_key = request.GET.get("destination_profile", "").strip().lower()
    release_id = request.GET.get("release_id", "").strip()
    try:
        resolved, selectors, issues, error = _resolved_profile_state()
        if error:
            return JsonResponse({"ok": False, "issues": [{"code": "dataops_config_invalid", "message": error}]}, status=409)
        route = resolve_operation_route(operation, resolved, selectors=selectors, source_profile=source_key, destination_profile=destination_key, local_dataset_id=str(getattr(settings, "DATASET_ID", "")))
        result = preflight_operation(operation, route.source, route.destination, release_id=release_id, local_dataset_id=str(getattr(settings, "DATASET_ID", "")), active_root=getattr(settings, "RUNTIME_GENERATIONS_ROOT", None))
        return JsonResponse(result.redacted(), status=200 if result.ok else 409)
    except (DataOpsPipelineError, StorageConfigurationError) as exc:
        return JsonResponse({"ok": False, "issues": [{"code": getattr(exc, "code", "storage_configuration_invalid"), "message": str(exc)}]}, status=409)


@login_required
def configuration(request):
    if request.method == "POST":
        if not _can_act(request) or not getattr(settings, "DATAOPS_UI_CONFIG_ENABLED", False):
            return HttpResponse("Database profile configuration is disabled", status=403)
        key = request.POST.get("key", "").strip().lower()
        try:
            env_profiles = {profile.key for profile in resolve_profiles(environ=dict(os.environ), include_stored=False)}
        except Exception:
            env_profiles = set()
        if key in env_profiles:
            return HttpResponse("Environment profile values are locked", status=409)
        if not key:
            return HttpResponse("Profile name is required", status=400)
        role = request.POST.get("role", "both").strip().lower()
        if role not in {"backup", "restore", "both"}:
            return HttpResponse("Invalid profile role", status=400)
        DataProfile.objects.using("control").update_or_create(
            key=key,
            defaults={
                "display_name": request.POST.get("display_name", key.replace("_", " ").title()).strip()[:160],
                "role": role,
                "source": DataProfile.Source.STORED,
                "enabled": request.POST.get("enabled", "1") in {"1", "true", "on"},
                "environment_locked": False,
                "endpoint": request.POST.get("endpoint", "").strip(),
                "bucket": request.POST.get("bucket", "").strip(),
                "region": request.POST.get("region", "").strip(),
                "dataset_id": request.POST.get("dataset_id", "").strip(),
                "source_id": request.POST.get("source_id", "").strip(),
                "namespace": request.POST.get("namespace", "").strip(),
                "prefix": request.POST.get("prefix", "").strip(),
                "credential_ref": request.POST.get("credential_ref", "").strip(),
                "credential_prefix": request.POST.get("credential_ref", "").strip(),
            },
        )
        return redirect("dataops:configuration")
    resolved, selectors, issues, error = _resolved_profile_state()
    return render(request, "dataops/configuration.html", {"profiles": [profile.redacted() for profile in resolved], "selectors": selectors, "issues": issues, "error": error})


@login_required
def env_patch(request):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    lines = ["# Generated by Data Operations; review and apply in Dokploy.", "# ENV is authoritative; secrets are intentionally omitted.", "DATAOPS_ENABLED=1"]
    for profile in resolve_profiles():
        token = profile.key.upper()
        lines.extend([f"DATAOPS_PROFILE_{token}_ROLE={profile.role}", f"DATAOPS_PROFILE_{token}_BUCKET={profile.bucket}", f"DATAOPS_PROFILE_{token}_DATASET_ID={profile.dataset_id}", f"DATAOPS_PROFILE_{token}_NAMESPACE={profile.namespace}", f"DATAOPS_PROFILE_{token}_CREDENTIAL_REF={profile.credential_ref}"])
    response = HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="dataops.env.patch"'
    return response
