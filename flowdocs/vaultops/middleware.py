from django.http import JsonResponse
from django.urls import Resolver404, resolve

from vaultops.services.mutations import (
    SnapshotBarrierActive,
    mutation_scope,
    tracking_enabled,
)


MUTATING_VIEW_NAMES = {
    "dashboard",
    "dashboard_folder",
    "bulk_maintenance",
    "maintenance_job_action",
    "maintenance_candidate_prepare",
    "save_settings",
    "register",
    "edit_user",
    "toggle_user_status",
    "delete_user",
    "create_folder",
    "rename_folder",
    "delete_folder",
    "update_folder_keywords",
    "folder_operations",
    "add_subcategory",
    "rename_pdf",
    "assign_pdf_owner",
    "delete_pdf",
    "deprecate_pdf",
    "archive_pdf",
    "restore_pdf",
}


class SourceMutationBarrierMiddleware:
    """Fence relevant web mutations while a consistent snapshot finalizes."""

    def __init__(self, get_response):
        self.get_response = get_response

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
            ):
                return self.get_response(request)
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
