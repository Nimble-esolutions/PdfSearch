import hashlib
import json
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.views import superadmin_required
from core.maintenance_plans import (
    MaintenancePlanError,
    create_plan as create_maintenance_plan,
    queue_plan as queue_maintenance_plan,
    workbench_maintenance_state,
)
from core.models import MaintenanceAuditEvent, MaintenanceJob, MaintenancePlan
from core.emergency_recovery import create_set as create_recovery_set
from vaultops.models import (
    ArtifactGeneration,
    GarbageCollectionPlan,
    RestoreWorkspace,
    RetentionHold,
    VaultConnectionProfile,
    VaultJob,
)
from vaultops.services.activation import schedule_activation
from vaultops.services.audit import append_event
from vaultops.services.confirmations import (
    consume_confirmation,
    issue_confirmation,
)
from vaultops.services.jobs import request_cancellation, requeue_job
from vaultops.services.inventory import project_verified_generation, verify_generation
from vaultops.services.profiles import (
    probe_restore_profile,
    upsert_restore_profile,
    vault_for_profile,
)
from vaultops.services.read_model import (
    build_workbench_state,
    generation_state_digest,
    workspace_state_digest,
)
from vaultops.services.restore import queue_restore_job
from vaultops.services.retention import (
    create_gc_plan,
    create_retention_hold,
    execute_gc_plan,
    generation_protection_reasons,
    release_retention_hold,
    retire_generation as retire_projected_generation,
    unretire_generation as unretire_projected_generation,
)
from vaultops.services.sync import queue_sync_job


SECTIONS = {
    "overview",
    "sync",
    "generations",
    "restore",
    "jobs",
    "retention",
    "configuration",
    "maintenance",
}
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
MUTATION_RATE_LIMIT = 30
MUTATION_RATE_WINDOW_SECONDS = 60


class WorkbenchRequestError(RuntimeError):
    reason_code = "request_invalid"
    status_code = 400

    def __init__(self, reason_code=None, *, status_code=None):
        self.reason_code = reason_code or self.reason_code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.reason_code)


def _request_data(request):
    if hasattr(request, "_vaultops_request_data"):
        return request._vaultops_request_data
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise WorkbenchRequestError("request_json_invalid") from exc
        if not isinstance(data, dict):
            raise WorkbenchRequestError("request_json_invalid")
    else:
        data = request.POST
    request._vaultops_request_data = data
    return data


def _request_value(request, key, default=""):
    return _request_data(request).get(key, default)


def _enforce_mutation_rate_limit(request):
    actor = request.user.pk or 0
    client_ip = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(
        f"{actor}:{client_ip}:{request.path}".encode("utf-8")
    ).hexdigest()
    cache_key = f"vaultops:rate:{digest}"
    try:
        if cache.add(
            cache_key, 1, timeout=MUTATION_RATE_WINDOW_SECONDS
        ):
            return
        attempts = cache.incr(cache_key)
    except Exception as exc:
        raise WorkbenchRequestError(
            "control_plane_unavailable", status_code=503
        ) from exc
    if attempts > MUTATION_RATE_LIMIT:
        raise WorkbenchRequestError("rate_limited", status_code=429)


def _request_idempotency_key(request, *, require_admin_gate=True):
    if require_admin_gate and not settings.VAULT_ADMIN_MUTATIONS_ENABLED:
        raise WorkbenchRequestError(
            "vault_admin_mutations_disabled", status_code=409
        )
    _enforce_mutation_rate_limit(request)
    value = (
        request.headers.get("Idempotency-Key")
        or _request_value(request, "idempotency_key", "")
    ).strip()
    if not IDEMPOTENCY_RE.fullmatch(value):
        raise WorkbenchRequestError("idempotency_key_required")
    return value


@superadmin_required
@require_POST
def profile_configure(request):
    try:
        _request_idempotency_key(request, require_admin_gate=False)
        supplied_secret_fields = {
            name
            for name in (
                "access_key",
                "secret_key",
                "password",
                "token",
            )
            if _request_value(request, name, "")
        }
        if supplied_secret_fields:
            raise WorkbenchRequestError("browser_secret_entry_rejected")
        profile = upsert_restore_profile(
            key=_request_value(request, "key", "").strip(),
            display_name=_request_value(request, "display_name", "").strip(),
            endpoint_origin=_request_value(request, "endpoint_origin", "").strip(),
            bucket=_request_value(request, "bucket", "").strip(),
            region=_request_value(request, "region", "").strip(),
            dataset_id=_request_value(request, "dataset_id", "").strip(),
            production_source_id=_request_value(
                request, "production_source_id", ""
            ).strip(),
            credential_alias=_request_value(
                request, "credential_alias", ""
            ).strip(),
        )
        return _mutation_success(
            request,
            section="configuration",
            reason_code="profile_configured",
            message=f"Read-only profile {profile.key} configured.",
            data={"profile_key": profile.key},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="configuration")


def _actor(request):
    return request.user.pk, request.user.get_username()


def _state_version_guard(request):
    supplied = _request_value(request, "state_version", "")
    if not supplied:
        raise WorkbenchRequestError("state_version_required")
    current = build_workbench_state()
    if supplied != current["state_version"]:
        raise WorkbenchRequestError("stale_state", status_code=409)
    return current


def _api_response(
    *,
    status,
    reason_code="",
    severity="info",
    recommended_action="",
    state_version="",
    data=None,
    correlation_id=None,
    http_status=200,
):
    return JsonResponse(
        {
            "status": status,
            "reason_code": reason_code,
            "severity": severity,
            "recommended_action": recommended_action,
            "observed_at": timezone.now(),
            "correlation_id": str(correlation_id or uuid.uuid4()),
            "state_version": state_version,
            "data": data if data is not None else {},
        },
        status=http_status,
    )


def _wants_json(request):
    accept = request.headers.get("Accept", "")
    return (
        request.content_type == "application/json"
        or (
            "application/json" in accept
            and "text/html" not in accept
        )
    )


def _form_redirect(section):
    response = HttpResponseRedirect(
        f"{reverse('operations_panel')}?section={section}"
    )
    response.status_code = 303
    return response


def _mutation_success(
    request,
    *,
    section,
    reason_code,
    message,
    data=None,
    state_version="",
    correlation_id=None,
):
    if _wants_json(request):
        return _api_response(
            status="accepted",
            reason_code=reason_code,
            recommended_action=message,
            state_version=state_version,
            correlation_id=correlation_id,
            data=data,
            http_status=202,
        )
    messages.success(request, message)
    return _form_redirect(section)


def _mutation_error(request, exc, *, section="overview"):
    reason_code = getattr(exc, "reason_code", "operation_failed")
    http_status = getattr(exc, "status_code", 409)
    if _wants_json(request):
        return _api_response(
            status="blocked",
            reason_code=reason_code,
            severity="warning",
            recommended_action="Refresh state and review the blocking reason.",
            http_status=http_status,
        )
    messages.error(request, reason_code.replace("_", " "))
    return _form_redirect(section)


@superadmin_required
@require_GET
def workbench(request):
    section = request.GET.get("section", "overview")
    if section not in SECTIONS:
        section = "overview"
    state = build_workbench_state(
        profile_key=request.GET.get("profile") or None
    )
    state["maintenance"] = workbench_maintenance_state(
        selected_plan_id=request.GET.get("plan", ""),
    )
    state["vault_state_version"] = state["state_version"]
    state["maintenance_state_version"] = state["maintenance"]["state_version"]
    state["combined_state_version"] = hashlib.sha256(
        (
            state["vault_state_version"]
            + ":"
            + state["maintenance_state_version"]
        ).encode("utf-8")
    ).hexdigest()
    return render(
        request,
        "vaultops/workbench.html",
        {
            "title": "Vault Operations Workbench",
            "section": section,
            "state": state,
            "idempotency_key": str(uuid.uuid4()),
            "breadcrumb_items": [
                {"label": "Dashboard", "url": reverse("dashboard")},
                {"label": "Operations", "url": None},
            ],
        },
    )


@superadmin_required
@require_GET
def state_api(request):
    state = build_workbench_state(
        profile_key=request.GET.get("profile") or None
    )
    state["maintenance"] = workbench_maintenance_state(
        selected_plan_id=request.GET.get("plan", ""),
    )
    state["vault_state_version"] = state["state_version"]
    state["maintenance_state_version"] = state["maintenance"]["state_version"]
    state["combined_state_version"] = hashlib.sha256(
        (
            state["vault_state_version"]
            + ":"
            + state["maintenance_state_version"]
        ).encode("utf-8")
    ).hexdigest()
    return _api_response(
        status=state["status"],
        reason_code=state["reason_code"],
        severity=state["severity"],
        recommended_action=state["recommended_action"],
        state_version=state["state_version"],
        data=state,
    )


@superadmin_required
@require_POST
def maintenance_plan_create(request):
    try:
        idempotency_key = _request_idempotency_key(
            request, require_admin_gate=False
        )
        plan = create_maintenance_plan(
            operation=_request_value(request, "operation", "").strip(),
            data=_request_data(request),
            actor=request.user,
            idempotency_key=idempotency_key,
        )
        if _wants_json(request):
            return _api_response(
                status="previewed",
                reason_code="maintenance_plan_created",
                state_version=plan.state_version,
                data={
                    "plan_id": str(plan.public_id),
                    "operation": plan.operation,
                    "preview": plan.preview,
                    "expires_at": plan.expires_at,
                    "external_embeddings_required": (
                        plan.external_embeddings_required
                    ),
                },
            )
        messages.success(
            request,
            f"Preview ready: {plan.preview['pdf_count']} document(s), "
            f"{plan.preview['folder_count']} folder(s).",
        )
        return HttpResponseRedirect(
            f"{reverse('operations_panel')}?section=maintenance"
            f"&plan={plan.public_id}"
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="maintenance")


@superadmin_required
@require_POST
def maintenance_plan_queue(request, plan_id):
    try:
        _enforce_mutation_rate_limit(request)
        plan = get_object_or_404(MaintenancePlan, public_id=plan_id)
        supplied_version = _request_value(request, "state_version", "")
        if supplied_version != plan.state_version:
            raise MaintenancePlanError("stale_state_version")
        job = queue_maintenance_plan(
            plan=plan,
            actor=request.user,
            confirmation=_request_value(request, "typed_confirmation", ""),
        )
        return _mutation_success(
            request,
            section="maintenance",
            reason_code="maintenance_job_queued",
            message=f"Local maintenance job {job.public_id} queued.",
            data={"job_id": str(job.public_id), "plan_id": str(plan.public_id)},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="maintenance")


def _local_job_state_version(job):
    payload = {
        "public_id": str(job.public_id),
        "status": job.status,
        "completed_items": job.completed_items,
        "failed_items": job.failed_items,
        "updated_at": job.updated_at.isoformat(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _local_job_action(request, job_id, *, action):
    try:
        _enforce_mutation_rate_limit(request)
        job = get_object_or_404(
            MaintenanceJob,
            public_id=job_id,
            kind__in={"validate", "repair_indexes", "reindex_needed", "reindex_selected"},
        )
        supplied_version = _request_value(request, "state_version", "")
        if supplied_version != _local_job_state_version(job):
            raise MaintenancePlanError("stale_state_version")
        if action == "cancel" and job.status in {"queued", "running"}:
            job.status = (
                "cancel_requested" if job.status == "running" else "cancelled"
            )
            if job.status == "cancelled":
                job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at", "updated_at"])
            event_type = "cancelled"
            reason_code = "maintenance_job_cancellation_recorded"
        elif action == "retry" and job.status == "failed":
            job.status = "queued"
            job.error_summary = ""
            job.finished_at = None
            job.failed_items = 0
            job.items.filter(status="failed").update(
                status="queued",
                error_code="",
                error_message="",
                finished_at=None,
            )
            job.save(update_fields=[
                "status", "error_summary", "finished_at", "failed_items",
                "updated_at",
            ])
            event_type = "retried"
            reason_code = "maintenance_job_requeued"
        else:
            raise MaintenancePlanError("maintenance_job_action_not_allowed")
        MaintenanceAuditEvent.objects.create(
            job=job,
            actor=request.user,
            event_type=event_type,
            payload={"source": "vault_workbench"},
        )
        return _mutation_success(
            request,
            section="maintenance",
            reason_code=reason_code,
            message=f"Local maintenance job {job.public_id} updated.",
            data={"job_id": str(job.public_id), "status": job.status},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="maintenance")


@superadmin_required
@require_POST
def maintenance_job_cancel(request, job_id):
    return _local_job_action(request, job_id, action="cancel")


@superadmin_required
@require_POST
def maintenance_job_retry(request, job_id):
    return _local_job_action(request, job_id, action="retry")


@superadmin_required
@require_GET
def profiles_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"profiles": state["profiles"]},
    )


@superadmin_required
@require_GET
def generations_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"generations": state["generations"]},
    )


@superadmin_required
@require_GET
def jobs_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"jobs": state["jobs"]},
    )


@superadmin_required
@require_GET
def audit_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"audit": state["audit"]},
    )


@superadmin_required
@require_GET
def diagnostics_api(request):
    state = build_workbench_state()
    diagnostics = {
        "environment": state["environment"],
        "authority": state["authority"],
        "sync": state["sync"],
        "lease": state["lease"],
        "feature_flags": state["feature_flags"],
        "counts": {
            "profiles": len(state["profiles"]),
            "generations": len(state["generations"]),
            "workspaces": len(state["workspaces"]),
            "jobs": len(state["jobs"]),
            "retention_holds": len(state["retention_holds"]),
            "gc_plans": len(state["gc_plans"]),
        },
        "retention": {
            "active_holds": sum(
                1 for hold in state["retention_holds"] if hold["active"]
            ),
            "retired_generations": sum(
                1
                for generation in state["generations"]
                if generation["vault_state"] == "retired"
            ),
            "gc_execution_enabled": bool(
                state["feature_flags"].get("gc_enabled")
            ),
        },
    }
    return _api_response(
        status=state["status"],
        reason_code=state["reason_code"],
        severity=state["severity"],
        recommended_action=state["recommended_action"],
        state_version=state["state_version"],
        data=diagnostics,
    )


@superadmin_required
@require_GET
def retention_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"retention_holds": state["retention_holds"]},
    )


@superadmin_required
@require_GET
def gc_plans_api(request):
    state = build_workbench_state()
    return _api_response(
        status="ok",
        state_version=state["state_version"],
        data={"gc_plans": state["gc_plans"]},
    )


@superadmin_required
@require_POST
def sync_run(request):
    try:
        _request_idempotency_key(request)
        _state_version_guard(request)
        actor_id, actor_name = _actor(request)
        job = queue_sync_job(
            trigger="manual",
            requested_by_id=actor_id,
            requested_by_name=actor_name,
        )
        if job is None:
            return _mutation_success(
                request,
                section="sync",
                reason_code="no_source_changes",
                message="No source changes require publication.",
            )
        else:
            return _mutation_success(
                request,
                section="sync",
                reason_code="sync_queued",
                message=(
                    f"Sync job {job.public_id} queued as "
                    "publish-only candidate."
                ),
                data={"job_id": str(job.public_id)},
                correlation_id=job.correlation_id,
            )
    except Exception as exc:
        return _mutation_error(request, exc, section="sync")


@superadmin_required
@require_POST
def restore_start(request):
    try:
        idempotency_key = _request_idempotency_key(request)
        _state_version_guard(request)
        profile = get_object_or_404(
            VaultConnectionProfile,
            key=_request_value(request, "profile_key", ""),
            enabled=True,
        )
        actor_id, actor_name = _actor(request)
        job = queue_restore_job(
            profile=profile,
            generation_id=_request_value(
                request, "generation_id", ""
            ).strip(),
            idempotency_key=idempotency_key,
            requested_by_id=actor_id,
            requested_by_name=actor_name,
        )
        return _mutation_success(
            request,
            section="restore",
            reason_code="restore_queued",
            message=(
                f"Restore job {job.public_id} queued for "
                "quarantine preparation."
            ),
            data={"job_id": str(job.public_id)},
            correlation_id=job.correlation_id,
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="restore")


@superadmin_required
@require_POST
def job_cancel(request, job_id):
    try:
        _request_idempotency_key(request)
        job = get_object_or_404(VaultJob, public_id=job_id)
        if str(job.state_version) != _request_value(
            request, "job_state_version"
        ):
            raise WorkbenchRequestError("stale_state", status_code=409)
        job = request_cancellation(job.public_id)
        return _mutation_success(
            request,
            section="jobs",
            reason_code="job_cancellation_requested",
            message=f"Cancellation requested for job {job.public_id}.",
            data={"job_id": str(job.public_id)},
            state_version=str(job.state_version),
            correlation_id=job.correlation_id,
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="jobs")


@superadmin_required
@require_POST
def job_retry(request, job_id):
    try:
        _request_idempotency_key(request)
        job = get_object_or_404(VaultJob, public_id=job_id)
        if str(job.state_version) != _request_value(
            request, "job_state_version"
        ):
            raise WorkbenchRequestError("stale_state", status_code=409)
        job = requeue_job(job.public_id)
        return _mutation_success(
            request,
            section="jobs",
            reason_code="job_retry_queued",
            message=f"Job {job.public_id} queued for retry.",
            data={"job_id": str(job.public_id)},
            state_version=str(job.state_version),
            correlation_id=job.correlation_id,
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="jobs")


@superadmin_required
@require_POST
def profile_probe(request, profile_key):
    try:
        _request_idempotency_key(request, require_admin_gate=False)
        _state_version_guard(request)
        profile = get_object_or_404(
            VaultConnectionProfile, key=profile_key, enabled=True
        )
        evidence = probe_restore_profile(profile)
        return _mutation_success(
            request,
            section="configuration",
            reason_code="profile_probe_completed",
            message=(
                f"Profile probe completed: "
                f"{'reachable' if evidence['reachable'] else 'unavailable'}."
            ),
            data={"evidence": evidence},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="configuration")


@superadmin_required
@require_POST
def profile_inventory(request, profile_key):
    try:
        _request_idempotency_key(request, require_admin_gate=False)
        profile = get_object_or_404(
            VaultConnectionProfile, key=profile_key, enabled=True
        )
        verified = verify_generation(
            vault_for_profile(profile),
            profile,
            generation_id=_request_value(
                request, "generation_id", ""
            ).strip(),
            verify_objects=True,
        )
        projection, generation = project_verified_generation(profile, verified)
        return _mutation_success(
            request,
            section="configuration",
            reason_code="profile_inventory_verified",
            message=(
                f"Verified generation {generation.generation_id} "
                f"({verified.file_count} objects)."
            ),
            data={
                "profile_key": profile.key,
                "generation_id": generation.generation_id,
                "authoritative": verified.authoritative,
                "file_count": verified.file_count,
                "byte_count": verified.byte_count,
                "inventory_state": projection.inventory_state,
            },
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="configuration")


def _default_generation_queryset():
    identity = settings.ENV_IDENTITY
    return ArtifactGeneration.objects.filter(
        profile__key=settings.VAULT_DEFAULT_PROFILE,
        dataset_id=identity.dataset_id,
    )


def _confirmation_target(action, target):
    if action in {
        "promote_generation",
        "retire_generation",
        "unretire_generation",
    }:
        generation = get_object_or_404(
            _default_generation_queryset(), generation_id=target
        )
        if (
            action == "promote_generation"
            and generation.vault_state
            != ArtifactGeneration.VaultState.CANDIDATE
        ):
            raise WorkbenchRequestError("generation_not_candidate")
        if action == "retire_generation":
            blocking = [
                reason
                for reason in generation_protection_reasons(generation)
                if reason
                in {
                    "generation_authoritative",
                    "generation_runtime_referenced",
                    "generation_job_in_progress",
                }
            ]
            if blocking:
                raise WorkbenchRequestError(blocking[0])
            if generation.vault_state not in {
                ArtifactGeneration.VaultState.CANDIDATE,
                ArtifactGeneration.VaultState.LEGACY_READ_ONLY,
            }:
                raise WorkbenchRequestError("generation_not_retirable")
        if (
            action == "unretire_generation"
            and generation.vault_state
            != ArtifactGeneration.VaultState.RETIRED
        ):
            raise WorkbenchRequestError("generation_not_retired")
        return generation_state_digest(generation)
    if action == "activate_workspace":
        workspace = get_object_or_404(
            RestoreWorkspace.objects.select_related("generation"),
            public_id=target,
        )
        if workspace.state != RestoreWorkspace.State.ACTIVATION_READY:
            raise WorkbenchRequestError("workspace_not_activation_ready")
        if settings.ENV_IDENTITY.is_production:
            raise WorkbenchRequestError(
                "production_activation_disabled", status_code=409
            )
        return workspace_state_digest(workspace)
    raise WorkbenchRequestError("confirmation_action_invalid")


def _confirmation_submit_url(action, target):
    routes = {
        "promote_generation": (
            "vaultops:promote_generation",
            {"generation_id": target},
        ),
        "retire_generation": (
            "vaultops:retire_generation",
            {"generation_id": target},
        ),
        "unretire_generation": (
            "vaultops:unretire_generation",
            {"generation_id": target},
        ),
        "activate_workspace": (
            "vaultops:schedule_activation",
            {"workspace_id": target},
        ),
    }
    try:
        route, kwargs = routes[action]
    except KeyError as exc:
        raise WorkbenchRequestError("confirmation_action_invalid") from exc
    return reverse(route, kwargs=kwargs)


@superadmin_required
@require_POST
def confirmation_issue(request):
    try:
        _request_idempotency_key(request)
        action = _request_value(request, "action", "")
        target = _request_value(request, "target", "")
        state_digest = _confirmation_target(action, target)
        challenge, phrase = issue_confirmation(
            actor_id=request.user.pk,
            action=action,
            target=target,
            state_digest=state_digest,
        )
        if _wants_json(request):
            return _api_response(
                status="issued",
                reason_code="confirmation_issued",
                state_version=state_digest,
                data={
                    "challenge_id": str(challenge.public_id),
                    "confirmation_phrase": phrase,
                    "expires_at": challenge.expires_at,
                    "action": action,
                    "target": target,
                },
            )
        return render(
            request,
            "vaultops/confirmation.html",
            {
                "challenge": challenge,
                "phrase": phrase,
                "action": action,
                "target": target,
                "state_digest": state_digest,
                "idempotency_key": str(uuid.uuid4()),
                "submit_url": _confirmation_submit_url(action, target),
                "breadcrumb_items": [
                    {"label": "Dashboard", "url": reverse("dashboard")},
                    {
                        "label": "Operations",
                        "url": reverse("operations_panel"),
                    },
                    {"label": "Confirmation", "url": None},
                ],
            },
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="generations")


def _consume(request, *, action, target, state_digest):
    return consume_confirmation(
        challenge_id=_request_value(request, "challenge_id", ""),
        actor_id=request.user.pk,
        action=action,
        target=target,
        state_digest=state_digest,
        phrase=_request_value(request, "confirmation_phrase", ""),
    )


@superadmin_required
@require_POST
def promote_generation(request, generation_id):
    try:
        idempotency_key = _request_idempotency_key(request)
        generation = get_object_or_404(
            _default_generation_queryset().select_related("profile"),
            generation_id=generation_id,
        )
        if generation.vault_state != ArtifactGeneration.VaultState.CANDIDATE:
            raise WorkbenchRequestError("generation_not_candidate")
        with transaction.atomic(using="control"):
            digest = generation_state_digest(generation)
            confirmation_digest = _consume(
                request,
                action="promote_generation",
                target=generation_id,
                state_digest=digest,
            )
            job, created = VaultJob.objects.get_or_create(
                operation="promote_generation",
                idempotency_key=idempotency_key,
                defaults={
                    "profile": generation.profile,
                    "profile_fingerprint": generation.profile.fingerprint,
                    "dataset_id": generation.dataset_id,
                    "generation_id": generation.generation_id,
                    "manifest_digest": generation.manifest_digest,
                    "requested_by_id": request.user.pk,
                    "requested_by_name": request.user.get_username(),
                    "progress": {
                        "confirmation": f"operator:{confirmation_digest}"
                    },
                },
            )
            if created:
                append_event(
                    action="job_queued",
                    result="succeeded",
                    correlation_id=job.correlation_id,
                    actor_id=request.user.pk,
                    actor_name=request.user.get_username(),
                    job_public_id=job.public_id,
                    confirmation_digest=confirmation_digest,
                    after_state={
                        "job_state": job.status,
                        "operation": job.operation,
                        "generation_id": generation.generation_id,
                    },
                )
        return _mutation_success(
            request,
            section="generations",
            reason_code="promotion_queued",
            message=(
                f"Promotion job {job.public_id} queued; "
                "runtime is unchanged."
            ),
            data={"job_id": str(job.public_id)},
            correlation_id=job.correlation_id,
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="generations")


def _confirmed_generation_transition(
    request,
    *,
    generation_id,
    action,
    transition,
):
    _request_idempotency_key(request)
    generation = get_object_or_404(
        _default_generation_queryset().select_related("profile"),
        generation_id=generation_id,
    )
    with transaction.atomic(using="control"):
        digest = generation_state_digest(generation)
        confirmation_digest = _consume(
            request,
            action=action,
            target=generation_id,
            state_digest=digest,
        )
        actor_id, actor_name = _actor(request)
        generation = transition(
            generation,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        append_event(
            action=f"{action}_confirmed",
            result="succeeded",
            correlation_id=uuid.uuid4(),
            actor_id=actor_id,
            actor_name=actor_name,
            confirmation_digest=confirmation_digest,
            after_state={
                "generation_id": generation.generation_id,
                "vault_state": generation.vault_state,
            },
        )
    return generation


@superadmin_required
@require_POST
def retire_generation_view(request, generation_id):
    try:
        generation = _confirmed_generation_transition(
            request,
            generation_id=generation_id,
            action="retire_generation",
            transition=retire_projected_generation,
        )
        return _mutation_success(
            request,
            section="retention",
            reason_code="generation_retired",
            message=(
                f"Generation {generation.generation_id} is retired. "
                "No object was deleted."
            ),
            data={"generation_id": generation.generation_id},
            state_version=generation_state_digest(generation),
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def unretire_generation_view(request, generation_id):
    try:
        generation = _confirmed_generation_transition(
            request,
            generation_id=generation_id,
            action="unretire_generation",
            transition=unretire_projected_generation,
        )
        return _mutation_success(
            request,
            section="retention",
            reason_code="generation_unretired",
            message=f"Generation {generation.generation_id} is a candidate.",
            data={"generation_id": generation.generation_id},
            state_version=generation_state_digest(generation),
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def retention_hold_create(request, generation_id):
    try:
        idempotency_key = _request_idempotency_key(request)
        generation = get_object_or_404(
            _default_generation_queryset(),
            generation_id=generation_id,
        )
        existing = RetentionHold.objects.filter(
            generation=generation,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            return _mutation_success(
                request,
                section="retention",
                reason_code="retention_hold_created",
                message=(
                    f"Retention hold {existing.pk} protects "
                    f"{generation.generation_id}."
                ),
                data={"hold_id": existing.pk},
            )
        _state_version_guard(request)
        expires_at = None
        raw_expiry = _request_value(request, "expires_at", "").strip()
        if raw_expiry:
            expires_at = parse_datetime(raw_expiry)
            if expires_at is None:
                raise WorkbenchRequestError("retention_hold_expiry_invalid")
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at)
        actor_id, actor_name = _actor(request)
        hold = create_retention_hold(
            generation,
            reason_code=_request_value(request, "reason_code", ""),
            owner_reference=_request_value(request, "owner_reference", ""),
            notes=_request_value(request, "notes", ""),
            expires_at=expires_at,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        return _mutation_success(
            request,
            section="retention",
            reason_code="retention_hold_created",
            message=(
                f"Retention hold {hold.pk} protects "
                f"{generation.generation_id}."
            ),
            data={"hold_id": hold.pk},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def retention_hold_release(request, hold_id):
    try:
        _request_idempotency_key(request)
        _state_version_guard(request)
        hold = get_object_or_404(
            RetentionHold.objects.select_related("generation"),
            pk=hold_id,
        )
        actor_id, actor_name = _actor(request)
        hold = release_retention_hold(
            hold,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        return _mutation_success(
            request,
            section="retention",
            reason_code="retention_hold_released",
            message=(
                f"Retention hold {hold.pk} was released. "
                "No object was deleted."
            ),
            data={"hold_id": hold.pk},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def gc_plan_create(request):
    try:
        _request_idempotency_key(request)
        _state_version_guard(request)
        profile = get_object_or_404(
            VaultConnectionProfile,
            key=settings.VAULT_DEFAULT_PROFILE,
            enabled=True,
        )
        actor_id, actor_name = _actor(request)
        plan = create_gc_plan(
            profile,
            dataset_id=profile.dataset_id,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        return _mutation_success(
            request,
            section="retention",
            reason_code="gc_plan_created",
            message=(
                f"GC dry-run {plan.public_id} is ready. "
                "Deletion remains disabled."
            ),
            data={"gc_plan_id": str(plan.public_id)},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def gc_plan_execute(request, plan_id):
    try:
        _request_idempotency_key(request)
        _state_version_guard(request)
        plan = get_object_or_404(GarbageCollectionPlan, public_id=plan_id)
        actor_id, actor_name = _actor(request)
        execute_gc_plan(plan, actor_id=actor_id, actor_name=actor_name)
        raise WorkbenchRequestError("gc_execution_unavailable")
    except Exception as exc:
        return _mutation_error(request, exc, section="retention")


@superadmin_required
@require_POST
def schedule_activation_view(request, workspace_id):
    try:
        _request_idempotency_key(request)
        workspace = get_object_or_404(
            RestoreWorkspace.objects.select_related("generation"),
            public_id=workspace_id,
        )
        recovery_set = create_recovery_set("pre-activation")
        with transaction.atomic(using="control"):
            digest = workspace_state_digest(workspace)
            _consume(
                request,
                action="activate_workspace",
                target=str(workspace.public_id),
                state_digest=digest,
            )
            intent = schedule_activation(
                workspace,
                actor_id=request.user.pk,
                actor_name=request.user.get_username(),
                confirmed=True,
            )
            append_event(
                action="activation_recovery_set_verified",
                result="verified",
                correlation_id=uuid.uuid4(),
                actor_id=request.user.pk,
                actor_name=request.user.get_username(),
                evidence={
                    "workspace_id": str(workspace.public_id),
                    "recovery_set_id": recovery_set["set_id"],
                },
            )
        return _mutation_success(
            request,
            section="restore",
            reason_code="activation_scheduled",
            message=f"Activation intent {intent.public_id} scheduled.",
            data={"activation_intent_id": str(intent.public_id)},
        )
    except Exception as exc:
        return _mutation_error(request, exc, section="restore")
