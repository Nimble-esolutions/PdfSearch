"""Small server-rendered Data Operations surface.

The page is deliberately useful before the full worker migration lands: it
shows fresh control-plane observations and queues only bounded, idempotent
operations. Existing maintenance services remain the execution authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.maintenance import queue_job
from core.maintenance_plans import workbench_maintenance_state
from core.models import MaintenanceJob, PDFFile
from core.operator_presentation import decorate_operator_state
from vaultops.services.read_model import build_workbench_state

from .config import resolve_profiles, resolve_selectors, resolve_setting, validate_profiles
from .models import BackupJob, DataConnection, DataOperation, DataProfile, DataOpsAuditEvent, MirrorDeletionPreview, RecoveryPoint
from .job_scheduler import ScheduleConfigurationError, parse_schedule
from .profile_service import ProfileMutationError, probe_profile, save_profile, save_selectors
from .pipeline import (
    DataOpsPipelineError,
    clone_destination_generation_id,
    preflight_operation,
    resolve_operation_route,
)
from .readiness import readiness_payload
from .storage import StorageConfigurationError
from .v3_config import V3ConfigurationError
from .v3_connection import (
    V3ConnectionError,
    ensure_connection_readable,
    ensure_owned_connection_ready,
)
from .v3_planning import action_status, compile_requested_plan, materialize_primary_connection, runtime_config


_FORBIDDEN_V3_REQUEST_FIELDS = frozenset(
    {"access_key", "credential", "credentials", "password", "secret", "secret_key", "token"}
)


def _contains_forbidden_v3_field(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_V3_REQUEST_FIELDS or any(
                normalized.endswith(f"_{part}")
                for part in _FORBIDDEN_V3_REQUEST_FIELDS
            ):
                return True
            if _contains_forbidden_v3_field(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_v3_field(item) for item in value)
    return False


def _v3_request_payload(request):
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("request_json_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("request_object_required")
        return payload
    return request.POST.dict()


def _v3_bool(value, *, field):
    if value in (True, "1", "true", "yes", "on"):
        return True
    if value in (False, None, "", "0", "false", "no", "off"):
        return False
    raise ValueError(f"{field}_invalid")


def _v3_recovery_point(value):
    recovery_point_id = str(value or "").strip()
    if not recovery_point_id:
        return None
    try:
        public_id = uuid.UUID(recovery_point_id)
    except ValueError:
        public_id = None
    point = None
    if public_id:
        point = (
            RecoveryPoint.objects.using("control")
            .filter(public_id=public_id)
            .first()
        )
    if point is None:
        point = (
            RecoveryPoint.objects.using("control")
            .filter(release_id=recovery_point_id)
            .first()
        )
    if point is None:
        raise ValueError("recovery_point_not_found")
    return point


@login_required
@require_GET
def v3_status(request):
    """Return one secret-free DataOps readiness and configuration summary."""
    if not _can_act(request):
        return JsonResponse({"error": {"code": "superadmin_required"}}, status=403)
    try:
        config = runtime_config()
    except V3ConfigurationError as exc:
        return JsonResponse(
            {
                "contract_version": 3,
                "status": "action_required",
                "issue": {"code": exc.code},
            },
            status=409,
        )
    return JsonResponse(
        {
            "contract_version": 3,
            "status": "ready" if config.enabled else "disabled",
            "environment": config.environment,
            "deployment_id": config.deployment_id,
            "dataset_id": config.dataset_id,
            "configuration_digest": config.digest,
            "connection": config.connection.redacted() if config.connection else None,
            "actions": action_status(config),
        }
    )


@login_required
@require_POST
def v3_operation_preview(request):
    """Compile the exact safe route without queuing or mutating anything."""
    if not _can_act(request):
        return JsonResponse({"error": {"code": "superadmin_required"}}, status=403)
    try:
        payload = _v3_request_payload(request)
        if _contains_forbidden_v3_field(payload):
            raise ValueError("secret_fields_forbidden")
        point = _v3_recovery_point(payload.get("recovery_point_id"))
        config, plan = compile_requested_plan(
            action=str(payload.get("action") or ""),
            activate=_v3_bool(payload.get("activate"), field="activate"),
            confirmation_present=bool(
                str(payload.get("confirmation") or "").strip()
            ),
            point=point,
            source_kind=str(payload.get("source_kind") or "").strip().lower(),
        )
    except (ValueError, V3ConfigurationError) as exc:
        code = getattr(exc, "code", str(exc))
        return JsonResponse({"error": {"code": code}}, status=400)
    return JsonResponse(
        {
            "configuration_digest": config.digest,
            "plan": plan.as_dict(),
            "active_data_unchanged_until_activation": True,
        },
        status=200 if plan.allowed else 409,
    )


@login_required
@require_POST
def v3_operation_start(request):
    """Persist an approved v3 plan and queue its resumable executor."""
    if not _can_act(request):
        return JsonResponse({"error": {"code": "superadmin_required"}}, status=403)
    try:
        payload = _v3_request_payload(request)
        if _contains_forbidden_v3_field(payload):
            raise ValueError("secret_fields_forbidden")
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"backup", "restore", "test_recovery"}:
            raise ValueError("executor_not_available")
        activate = _v3_bool(payload.get("activate"), field="activate")
        if activate:
            raise ValueError("signed_activation_executor_not_available")
        point = _v3_recovery_point(payload.get("recovery_point_id"))
        initial_config = runtime_config()
        connection = materialize_primary_connection(initial_config)
        if action == "backup" or (
            point is not None and point.dataset_id != initial_config.dataset_id
        ):
            ensure_owned_connection_ready(
                connection,
                deployment_id=initial_config.deployment_id,
            )
        if point is not None and point.connection_id:
            ensure_connection_readable(point.connection)
        config, plan = compile_requested_plan(
            action=action,
            activate=activate,
            confirmation_present=bool(
                str(payload.get("confirmation") or "").strip()
            ),
            point=point,
            source_kind=str(payload.get("source_kind") or "").strip().lower(),
        )
        if not plan.allowed:
            return JsonResponse(
                {"configuration_digest": config.digest, "plan": plan.as_dict()},
                status=409,
            )
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            idempotency_key = secrets.token_urlsafe(18)
        operation_kind = {
            "backup": DataOperation.Kind.BACKUP,
            "restore": DataOperation.Kind.RESTORE,
            "test_recovery": DataOperation.Kind.TEST_RECOVERY,
        }[action]
        checkpoint = {"configuration_digest": config.digest}
        if point is not None:
            checkpoint["recovery_point_id"] = str(point.public_id)
        operation, created = DataOperation.objects.using("control").get_or_create(
            kind=operation_kind,
            idempotency_key=idempotency_key,
            defaults={
                "state": DataOperation.State.QUEUED,
                "connection": connection,
                "release_id": str(payload.get("recovery_point_name") or "").strip(),
                "pipeline_stage": "planned",
                "lifecycle_route": plan.route,
                "lifecycle_plan": plan.as_dict(),
                "lifecycle_plan_digest": plan.plan_digest,
                "checkpoint": checkpoint,
            },
        )
        if not created and operation.lifecycle_plan_digest != plan.plan_digest:
            raise ValueError("idempotency_key_conflict")
        if created:
            DataOpsAuditEvent.objects.using("control").create(
                actor_id=request.user.pk,
                actor_name=request.user.get_username(),
                action=action,
                operation_id=operation.public_id,
                outcome="queued",
                evidence={
                    "plan_digest": plan.plan_digest,
                    "configuration_digest": config.digest,
                },
            )
    except (ValueError, V3ConfigurationError, V3ConnectionError) as exc:
        code = getattr(exc, "code", str(exc))
        return JsonResponse({"error": {"code": code}}, status=400)
    return JsonResponse(
        {
            "operation_id": str(operation.public_id),
            "state": operation.state,
            "plan_digest": operation.lifecycle_plan_digest,
            "created": created,
        },
        status=202 if created else 200,
    )


def _can_act(request):
    return bool(getattr(request.user, "is_superuser", False))


def _navigation(active):
    return {"active": active}


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


def _state(request, *, include_legacy_advanced=False):
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
    state = {
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
        "active_maintenance_jobs": MaintenanceJob.objects.filter(status__in=("queued", "running", "retrying")).count(),
        "profile_cards": profile_cards,
        "history": {"count": len(recent), "items": [{"label": item.get_kind_display(), "status_label": item.get_state_display(), "started_at": item.created_at, "stage": item.pipeline_stage, "error_code": item.error_code, "receipt_id": str(item.public_id), "timeline": (item.result or {}).get("stages", [])} for item in recent]},
        "profiles": profiles,
        "permissions": {"can_refresh": _can_act(request), "can_backup": _can_act(request), "can_restore": _can_act(request), "can_configure": _can_act(request), "can_export_env": _can_act(request)},
    }
    if include_legacy_advanced:
        try:
            maintenance = workbench_maintenance_state(selected_plan_id=request.GET.get("plan", ""), selected_job_id=request.GET.get("job", ""))
        except Exception:
            maintenance = {"state_version": "", "capabilities": {}, "folders": [], "plans": [], "jobs": [], "selected_plan": None, "selected_job": None}
        decorate_operator_state(maintenance)
        state["maintenance"] = maintenance
        state["state_version"] = maintenance.get("state_version", "")
    return state


@login_required
def workbench(request):
    legacy_advanced = bool(request.GET.get("section"))
    state = _state(request, include_legacy_advanced=legacy_advanced)
    return render(request, "dataops/workbench.html", {"state": state, "state_url": reverse("dataops:state"), "idempotency_key": secrets.token_urlsafe(18), "dataops_nav": _navigation("overview"), "legacy_advanced": legacy_advanced})


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
@require_POST
def clone_rebind(request):
    """Queue the explicit cross-dataset clone behind Advanced Controls."""
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    if not getattr(settings, "DATAOPS_CLONE_REBIND_ENABLED", False):
        return HttpResponse("Clone/rebind is disabled by deployment policy", status=409)
    source_key = request.POST.get("source_profile", "").strip().lower()
    destination_key = request.POST.get("destination_profile", "").strip().lower()
    source_generation_id = request.POST.get("source_generation_id", "").strip()
    destination_generation_id = clone_destination_generation_id(
        source_generation_id,
        request.POST.get("destination_generation_id", "").strip(),
    )
    clone_reason = request.POST.get("clone_reason", "stage-rehearsal").strip() or "stage-rehearsal"
    confirmation = request.POST.get("confirmation", "")
    try:
        resolved, selectors, _issues, error = _resolved_profile_state()
        if error:
            return HttpResponse("Clone/rebind blocked by profile configuration", status=409)
        route = resolve_operation_route(
            "clone_rebind",
            resolved,
            selectors=selectors,
            source_profile=source_key,
            destination_profile=destination_key,
            local_dataset_id=str(getattr(settings, "DATASET_ID", "")),
        )
        preview = preflight_operation(
            "clone_rebind",
            route.source,
            route.destination,
            release_id=destination_generation_id,
            source_generation_id=source_generation_id,
            destination_generation_id=destination_generation_id,
            confirmation=confirmation,
            clone_reason=clone_reason,
            local_dataset_id=str(getattr(settings, "DATASET_ID", "")),
        )
    except (DataOpsPipelineError, StorageConfigurationError) as exc:
        return HttpResponse(f"Clone/rebind blocked: {getattr(exc, 'code', str(exc))}", status=409)
    if not preview.ok:
        return HttpResponse("Clone/rebind confirmation or profile validation failed", status=409)
    idempotency_key = request.POST.get("idempotency_key", "").strip() or secrets.token_urlsafe(18)
    operation, created = DataOperation.objects.using("control").get_or_create(
        kind=DataOperation.Kind.CLONE_REBIND,
        idempotency_key=idempotency_key,
        defaults={
            "state": DataOperation.State.QUEUED,
            "profile_key": destination_key,
            "source_profile_key": source_key,
            "destination_profile_key": destination_key,
            "release_id": destination_generation_id,
            "checkpoint": {
                "pipeline": "preflight",
                "source_generation_id": source_generation_id,
                "destination_generation_id": destination_generation_id,
                "clone_reason": clone_reason,
                "confirmation_sha256": hashlib.sha256(confirmation.encode("utf-8")).hexdigest(),
            },
        },
    )
    if created:
        DataOpsAuditEvent.objects.using("control").create(
            actor_id=request.user.pk,
            actor_name=request.user.get_username(),
            action="clone_rebind",
            operation_id=operation.public_id,
            profile_key=destination_key,
            outcome="queued",
            evidence={
                "source_profile": source_key,
                "destination_profile": destination_key,
                "source_generation_id": source_generation_id,
                "destination_generation_id": destination_generation_id,
                "clone_reason": clone_reason,
            },
        )
    return redirect("dataops:advanced")


@login_required
@require_GET
def preflight(request):
    operation = request.GET.get("operation", "restore").strip().lower()
    source_key = request.GET.get("source_profile", "").strip().lower()
    destination_key = request.GET.get("destination_profile", "").strip().lower()
    release_id = request.GET.get("release_id", "").strip()
    source_generation_id = request.GET.get("source_generation_id", "").strip()
    destination_generation_id = request.GET.get("destination_generation_id", "").strip()
    clone_reason = request.GET.get("clone_reason", "stage-rehearsal").strip() or "stage-rehearsal"
    confirmation = request.GET.get("confirmation", "")
    try:
        if operation in {"clone_rebind", "clone/rebind"} and not getattr(settings, "DATAOPS_CLONE_REBIND_ENABLED", False):
            return JsonResponse(
                {"ok": False, "issues": [{"code": "clone_rebind_disabled", "message": "Clone/rebind is disabled by deployment policy"}]},
                status=409,
            )
        resolved, selectors, issues, error = _resolved_profile_state()
        if error:
            return JsonResponse({"ok": False, "issues": [{"code": "dataops_config_invalid", "message": error}]}, status=409)
        route = resolve_operation_route(operation, resolved, selectors=selectors, source_profile=source_key, destination_profile=destination_key, local_dataset_id=str(getattr(settings, "DATASET_ID", "")))
        result = preflight_operation(
            operation,
            route.source,
            route.destination,
            release_id=release_id,
            source_generation_id=source_generation_id,
            destination_generation_id=destination_generation_id,
            confirmation=confirmation,
            clone_reason=clone_reason,
            local_dataset_id=str(getattr(settings, "DATASET_ID", "")),
            active_root=getattr(settings, "RUNTIME_GENERATIONS_ROOT", None),
        )
        return JsonResponse(result.redacted(), status=200 if result.ok else 409)
    except (DataOpsPipelineError, StorageConfigurationError) as exc:
        return JsonResponse({"ok": False, "issues": [{"code": getattr(exc, "code", "storage_configuration_invalid"), "message": str(exc)}]}, status=409)


@login_required
def configuration(request):
    if request.method == "POST":
        if not _can_act(request) or not getattr(settings, "DATAOPS_UI_CONFIG_ENABLED", False):
            return HttpResponse("Database profile configuration is disabled", status=403)
        action = request.POST.get("action", "save_profile")
        if action == "save_selectors":
            save_selectors(request.POST, actor=request.user)
            return redirect("dataops:configuration")
        key = request.POST.get("key", "").strip().lower()
        try:
            env_profiles = {profile.key for profile in resolve_profiles(environ=dict(os.environ), include_stored=False)}
        except Exception:
            env_profiles = set()
        if key in env_profiles:
            return HttpResponse("Environment profile values are locked", status=409)
        if not key:
            return HttpResponse("Profile name is required", status=400)
        try:
            save_profile(request.POST, actor=request.user)
        except (ProfileMutationError, ValueError) as exc:
            return HttpResponse(str(exc), status=400)
        return redirect("dataops:configuration")
    resolved, selectors, issues, error = _resolved_profile_state()
    stored = {profile.key: profile for profile in DataProfile.objects.using("control").all()}
    cards = []
    for profile in resolved:
        row = stored.get(profile.key)
        cards.append({**profile.redacted(), "last_observed_at": getattr(row, "last_observed_at", None), "observation": getattr(row, "observation", {}), "credential_configured": bool(getattr(getattr(row, "credential", None), "enabled", False)) if row else False})
    return render(request, "dataops/configuration.html", {"profiles": cards, "selectors": selectors, "issues": issues, "error": error, "provider_choices": DataProfile.Provider.choices, "dataops_nav": _navigation("profiles")})


@login_required
@require_POST
def profile_probe(request, profile_key):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    row = DataProfile.objects.using("control").filter(key=profile_key).first()
    resolved = {item.key: item for item in resolve_profiles()}.get(profile_key)
    if row is None or resolved is None:
        return HttpResponse("Profile not found", status=404)
    try:
        probe_profile(row, resolved, actor=request.user)
    except Exception as exc:
        return HttpResponse(f"Profile probe failed: {getattr(exc, 'code', str(exc))}", status=409)
    return redirect("dataops:configuration")


@login_required
def jobs(request):
    if request.method == "POST":
        if not _can_act(request):
            return HttpResponse("Superadmin approval required", status=403)
        mode = request.POST.get("mode", "incremental")
        if mode not in {"incremental", "archive", "mirror"}:
            return HttpResponse("Invalid transfer mode", status=400)
        schedule = request.POST.get("schedule", "").strip()
        timezone_name = request.POST.get("timezone", "UTC").strip()
        try:
            if schedule:
                parse_schedule(schedule)
            ZoneInfo(timezone_name)
        except (ScheduleConfigurationError, ZoneInfoNotFoundError) as exc:
            return HttpResponse(f"Invalid schedule: {exc}", status=400)
        try:
            delete_max_objects = min(10000, max(1, int(request.POST.get("mirror_delete_max_objects", "100"))))
            delete_max_percent = min(100, max(1, int(request.POST.get("mirror_delete_max_percent", "10"))))
        except ValueError:
            return HttpResponse("Invalid mirror deletion budget", status=400)
        BackupJob.objects.using("control").update_or_create(
            slug=request.POST.get("slug", "").strip().lower(),
            defaults={
                "name": request.POST.get("name", "").strip(),
                "source_profile_key": request.POST.get("source_profile", "").strip().lower(),
                "target_profile_key": request.POST.get("target_profile", "").strip().lower(),
                "source_prefix": request.POST.get("source_prefix", "").strip().strip("/"),
                "target_prefix": request.POST.get("target_prefix", "").strip().strip("/"),
                "mode": mode,
                "schedule": schedule,
                "timezone": timezone_name,
                "enabled": request.POST.get("enabled", "1") in {"1", "on", "true"},
                "delete_orphans": mode == "mirror" and request.POST.get("delete_orphans") in {"1", "on", "true"},
                "mirror_delete_max_objects": delete_max_objects,
                "mirror_delete_max_percent": delete_max_percent,
            },
        )
        return redirect("dataops:jobs")
    profiles = [profile.redacted() for profile in resolve_profiles() if profile.enabled]
    jobs = list(BackupJob.objects.using("control").all())
    for job in jobs:
        job.latest_deletion_preview = job.deletion_previews.filter(state=MirrorDeletionPreview.State.READY).order_by("-created_at").first()
    from .quarantine import quarantine_inventory
    return render(request, "dataops/jobs.html", {"jobs": jobs, "profiles": profiles, "quarantines": quarantine_inventory(), "dataops_nav": _navigation("jobs")})


@login_required
@require_POST
def run_job(request, job_slug):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    job = BackupJob.objects.using("control").filter(slug=job_slug, enabled=True).first()
    if job is None:
        return HttpResponse("Backup job not found or disabled", status=404)
    if job.delete_orphans:
        return HttpResponse("Mirror deletion requires an independently verified preview", status=409)
    operation = DataOperation.objects.using("control").create(
        kind=DataOperation.Kind.SYNC,
        state=DataOperation.State.QUEUED,
        profile_key=job.target_profile_key,
        source_profile_key=job.source_profile_key,
        destination_profile_key=job.target_profile_key,
        idempotency_key=f"job:{job.slug}:{secrets.token_urlsafe(12)}",
        checkpoint={"job_slug": job.slug, "trigger": "manual"},
    )
    BackupJob.objects.using("control").filter(pk=job.pk).update(last_run_status="queued")
    DataOpsAuditEvent.objects.using("control").create(
        actor_id=request.user.pk,
        actor_name=request.user.get_username(),
        action="backup_job_queued",
        operation_id=operation.public_id,
        profile_key=job.target_profile_key,
        outcome="queued",
        evidence={"job_slug": job.slug, "mode": job.mode, "source_profile": job.source_profile_key},
    )
    return redirect("dataops:jobs")


@login_required
@require_POST
def preview_job_deletions(request, job_slug):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    job = BackupJob.objects.using("control").filter(slug=job_slug, enabled=True).first()
    if job is None:
        return HttpResponse("Backup job not found or disabled", status=404)
    try:
        from .mirror import create_deletion_preview

        create_deletion_preview(job)
    except DataOpsPipelineError as exc:
        return HttpResponse(f"Deletion preview blocked: {exc.code}", status=409)
    return redirect("dataops:jobs")


@login_required
@require_POST
def confirm_job_deletions(request, job_slug, preview_id):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    preview = MirrorDeletionPreview.objects.using("control").filter(public_id=preview_id, job__slug=job_slug, state=MirrorDeletionPreview.State.READY, confirmed_operation__isnull=True).first()
    if preview is None:
        return HttpResponse("Deletion preview not found", status=404)
    operation = DataOperation.objects.using("control").create(
        kind=DataOperation.Kind.SYNC, state=DataOperation.State.QUEUED,
        profile_key=preview.job.target_profile_key, source_profile_key=preview.job.source_profile_key,
        destination_profile_key=preview.job.target_profile_key,
        idempotency_key=f"mirror:{preview.public_id}",
        checkpoint={"job_slug": preview.job.slug, "trigger": "confirmed_mirror", "mirror_preview_id": str(preview.public_id)},
    )
    preview.confirmed_operation = operation
    preview.save(update_fields=["confirmed_operation", "updated_at"])
    return redirect("dataops:jobs")


@login_required
@require_POST
def recover_job_quarantine(request, operation_id):
    if not _can_act(request):
        return HttpResponse("Superadmin approval required", status=403)
    operation = DataOperation.objects.using("control").filter(public_id=operation_id, kind=DataOperation.Kind.SYNC, state=DataOperation.State.SUCCEEDED).first()
    if operation is None:
        return HttpResponse("Quarantine operation not found", status=404)
    try:
        from .quarantine import recover_mirror_quarantine
        recover_mirror_quarantine(operation, actor=request.user)
    except (ValueError, StorageConfigurationError) as exc:
        return HttpResponse(f"Quarantine recovery blocked: {exc}", status=409)
    except Exception:
        return HttpResponse("Quarantine recovery blocked: storage operation failed", status=409)
    return redirect("dataops:jobs")


@login_required
def advanced(request):
    try:
        maintenance = workbench_maintenance_state(selected_plan_id=request.GET.get("plan", ""), selected_job_id=request.GET.get("job", ""))
    except Exception:
        maintenance = {"state_version": "", "capabilities": {}, "folders": [], "plans": [], "jobs": [], "selected_plan": None, "selected_job": None}
    decorate_operator_state(maintenance)
    state = build_workbench_state()
    state["maintenance"] = maintenance
    try:
        resolved = resolve_profiles()
    except Exception:
        resolved = ()
    clone_profiles = [
        {
            **profile.redacted(),
            "can_clone_source": profile.can_restore,
            "can_clone_destination": profile.can_backup,
        }
        for profile in resolved
        if profile.enabled
    ]
    clone_controls = {
        "enabled": bool(getattr(settings, "DATAOPS_CLONE_REBIND_ENABLED", False)),
        "profiles": clone_profiles,
        "confirmation_prefix": "CLONE <source-profile>:<source-generation> -> <destination-profile>:<destination-generation> / <reason>",
    }
    return render(request, "dataops/advanced.html", {"state": state, "clone_controls": clone_controls, "idempotency_key": secrets.token_urlsafe(18), "dataops_nav": _navigation("advanced")})


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
