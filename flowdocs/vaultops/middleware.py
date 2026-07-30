from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.http import JsonResponse
from django.urls import Resolver404, resolve

from vaultops.services.mutations import (
    SnapshotBarrierActive,
    mark_current_scope_changed,
    mutation_scope,
    tracking_enabled,
)


MUTATING_VIEW_NAMES = {
    "dashboard",
    "dashboard_folder",
    "save_settings",
    "register",
    "edit_user",
    "toggle_user_status",
    "delete_user",
    "create_folder",
    "rename_folder",
    "delete_folder",
    "update_folder_keywords",
    "add_subcategory",
    "rename_pdf",
    "assign_pdf_owner",
    "delete_pdf",
    "deprecate_pdf",
    "archive_pdf",
    "mark_pdf_unavailable",
    "bind_pdf_recovery_evidence",
    "restore_pdf",
}


def _mark_committed_core_change(sender, using=None, **_kwargs):
    """Observe committed source-model writes inside a lazy web scope."""
    model_meta = getattr(sender, "_meta", None)
    if model_meta is None or model_meta.app_label != "core":
        return
    transaction.on_commit(mark_current_scope_changed, using=using)


def _connect_change_signals():
    post_save.connect(
        _mark_committed_core_change,
        dispatch_uid="vaultops.core_source_post_save",
        weak=False,
    )
    post_delete.connect(
        _mark_committed_core_change,
        dispatch_uid="vaultops.core_source_post_delete",
        weak=False,
    )


class SourceMutationBarrierMiddleware:
    """Fence relevant web mutations while a consistent snapshot finalizes."""

    def __init__(self, get_response):
        self.get_response = get_response
        _connect_change_signals()

    def __call__(self, request):
        if not tracking_enabled() or request.method not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            return self.get_response(request)
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)
        if match.url_name not in MUTATING_VIEW_NAMES:
            return self.get_response(request)
        try:
            with mutation_scope(
                category="web",
                relative_path=match.url_name or request.path_info,
                operation=request.method.lower(),
                record_on_change=True,
            ) as outcome:
                try:
                    response = self.get_response(request)
                except Exception:
                    if not outcome.changed:
                        outcome.discard()
                    raise
                if response.status_code >= 400 and not outcome.changed:
                    outcome.discard()
                return response
        except SnapshotBarrierActive:
            payload = {
                "status": "blocked",
                "reason_code": "snapshot_barrier_active",
                "severity": "warning",
                "recommended_action": "Retry after snapshot finalization.",
            }
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse(payload, status=409)
            return JsonResponse(payload, status=409)
