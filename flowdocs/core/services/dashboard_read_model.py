from dataclasses import dataclass

from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from core.models import (
    CustomUser,
    Folder,
    MaintenanceJob,
    PDFFile,
    SEARCHABLE_PDF_LIFECYCLES,
)
from core.maintenance_plans import LOCAL_OPERATIONS as LOCAL_MAINTENANCE_JOB_KINDS
from core.operator_presentation import decorate_dashboard_state


CATEGORY_PAGE_SIZE = 24
ATTENTION_LIMIT = 6
ACTIVE_JOB_LIMIT = 5
RECENT_INTAKE_LIMIT = 5


@dataclass(frozen=True)
class DashboardFilters:
    query: str = ""
    readiness: str = ""
    provenance: str = ""
    occupancy: str = ""
    ordering: str = "name"
    page: int = 1


def _visible_folders(user):
    from core.views import searchable_folders

    return searchable_folders(user)


def _visible_pdfs(user):
    from core.views import visible_pdfs

    return visible_pdfs(user)


def normalize_dashboard_filters(data):
    query = (data.get("category_q") or "").strip()[:100]
    readiness = data.get("readiness", "")
    provenance = data.get("provenance", "")
    occupancy = data.get("occupancy", "")
    ordering = data.get("ordering", "name")
    if readiness not in {"", "ready", "needs_index", "unavailable"}:
        readiness = ""
    if provenance not in {"", "complete", "unknown"}:
        provenance = ""
    if occupancy not in {"", "with_documents", "empty"}:
        occupancy = ""
    if ordering not in {"name", "-latest_upload", "-pdf_count", "-index_debt"}:
        ordering = "name"
    try:
        page = max(int(data.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    return DashboardFilters(
        query=query,
        readiness=readiness,
        provenance=provenance,
        occupancy=occupancy,
        ordering=ordering,
        page=page,
    )


def _category_queryset(user, filters):
    folders = _visible_folders(user).annotate(
        pdf_count=Count("files", distinct=True),
        searchable_count=Count(
            "files",
            filter=Q(files__lifecycle__in=SEARCHABLE_PDF_LIFECYCLES),
            distinct=True,
        ),
        unavailable_count=Count(
            "files",
            filter=Q(files__lifecycle="unavailable"),
            distinct=True,
        ),
        indexed_count=Count(
            "files",
            filter=Q(
                files__indexed=True,
                files__lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            ),
            distinct=True,
        ),
        unknown_uploader_count=Count(
            "files",
            filter=Q(files__uploaded_by__isnull=True),
            distinct=True,
        ),
        latest_upload=Max("files__uploaded_at"),
    ).annotate(
        index_debt=Count(
            "files",
            filter=Q(
                files__indexed=False,
                files__lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            ),
            distinct=True,
        )
    )
    if filters.query:
        folders = folders.filter(name__icontains=filters.query)
    if filters.readiness == "ready":
        folders = folders.filter(searchable_count__gt=0, index_debt=0)
    elif filters.readiness == "needs_index":
        folders = folders.filter(index_debt__gt=0)
    elif filters.readiness == "unavailable":
        folders = folders.filter(files__lifecycle="unavailable").distinct()
    if filters.provenance == "complete":
        folders = folders.filter(unknown_uploader_count=0)
    elif filters.provenance == "unknown":
        folders = folders.filter(unknown_uploader_count__gt=0)
    if filters.occupancy == "with_documents":
        folders = folders.filter(pdf_count__gt=0)
    elif filters.occupancy == "empty":
        folders = folders.filter(pdf_count=0)
    ordering = {
        "name": ("name", "pk"),
        "-latest_upload": ("-latest_upload", "name", "pk"),
        "-pdf_count": ("-pdf_count", "name", "pk"),
        "-index_debt": ("-index_debt", "name", "pk"),
    }[filters.ordering]
    return folders.order_by(*ordering)


def _attention_items(
    *,
    needs_index,
    unavailable_media,
    unknown_uploaders,
    empty_folders,
    jobs,
    failed_job_count,
):
    items = []
    if failed_job_count:
        items.append({
            "severity": "danger",
            "reason_code": "maintenance_job_failed",
            "count": failed_job_count,
            "title": gettext("Maintenance work needs review"),
            "detail": gettext("A local maintenance job failed."),
            "action_label": gettext("Review jobs"),
            "action_url": f"{reverse('operations_panel')}?section=jobs",
        })
    if needs_index:
        items.append({
            "severity": "warning",
            "reason_code": "index_debt_present",
            "count": needs_index,
            "title": gettext("Documents need index review"),
            "detail": gettext(
                "Preview a bounded maintenance plan before changing search artifacts."
            ),
            "action_label": gettext("Maintain Documents & Indexes"),
            "action_url": f"{reverse('operations_panel')}?section=maintenance",
        })
    if unavailable_media:
        items.append({
            "severity": "warning",
            "reason_code": "document_media_unavailable",
            "count": unavailable_media,
            "title": gettext("Document files are unavailable"),
            "detail": gettext(
                "These preserved records are excluded from search and index work."
            ),
            "action_label": gettext("Review unavailable documents"),
            "action_url": f"{reverse('dashboard')}?readiness=unavailable",
        })
    if unknown_uploaders:
        items.append({
            "severity": "warning",
            "reason_code": "provenance_review_required",
            "count": unknown_uploaders,
            "title": gettext("Document provenance needs review"),
            "detail": gettext("Assign an accountable uploader where it is missing."),
            "action_label": gettext("Review categories"),
            "action_url": f"{reverse('dashboard')}?provenance=unknown",
        })
    if jobs:
        items.append({
            "severity": "info",
            "reason_code": "maintenance_in_progress",
            "count": len(jobs),
            "title": gettext("Maintenance is in progress"),
            "detail": gettext("The active runtime remains unchanged while work runs."),
            "action_label": gettext("View active work"),
            "action_url": f"{reverse('operations_panel')}?section=maintenance",
        })
    if empty_folders:
        items.append({
            "severity": "muted",
            "reason_code": "empty_categories_present",
            "count": empty_folders,
            "title": gettext("Categories are awaiting intake"),
            "detail": gettext("These categories contain no documents."),
            "action_label": gettext("Show empty categories"),
            "action_url": f"{reverse('dashboard')}?occupancy=empty",
        })
    return items[:ATTENTION_LIMIT]


def build_dashboard_state(*, user, data):
    filters = normalize_dashboard_filters(data)
    categories = _category_queryset(user, filters)
    paginator = Paginator(categories, CATEGORY_PAGE_SIZE)
    page = paginator.get_page(filters.page)

    pdfs = _visible_pdfs(user).select_related("folder", "uploaded_by")
    totals = pdfs.aggregate(
        total=Count("pk"),
        searchable=Count(
            "pk",
            filter=Q(lifecycle__in=SEARCHABLE_PDF_LIFECYCLES),
        ),
        indexed=Count(
            "pk",
            filter=Q(
                indexed=True,
                lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            ),
        ),
        unavailable=Count("pk", filter=Q(lifecycle="unavailable")),
        unknown_uploaders=Count("pk", filter=Q(uploaded_by__isnull=True)),
    )
    total_pdfs = totals["total"] or 0
    searchable_pdfs = totals["searchable"] or 0
    indexed_pdfs = totals["indexed"] or 0
    unavailable_pdfs = totals["unavailable"] or 0
    unknown_uploaders = totals["unknown_uploaders"] or 0

    all_folders = _visible_folders(user)
    folder_summary = all_folders.aggregate(
        total=Count("pk", distinct=True),
        with_documents=Count(
            "pk", filter=Q(files__isnull=False), distinct=True
        ),
    )
    folder_count = folder_summary["total"] or 0
    folders_with_documents = folder_summary["with_documents"] or 0
    empty_folders = max(folder_count - folders_with_documents, 0)

    is_superadmin = getattr(user, "role", None) == "superadmin"
    if is_superadmin:
        from vaultops.services.read_model import build_dashboard_authority_summary

        authority_summary = build_dashboard_authority_summary()
        search_available = authority_summary["search_available"]
        jobs = list(
            MaintenanceJob.objects.select_related("requested_by")
            .filter(
                kind__in=LOCAL_MAINTENANCE_JOB_KINDS,
                status__in={"queued", "running", "cancel_requested"},
            )
            .order_by("-updated_at", "-pk")[:ACTIVE_JOB_LIMIT]
        )
        failed_job_count = MaintenanceJob.objects.filter(
            kind__in=LOCAL_MAINTENANCE_JOB_KINDS,
            status="failed",
        ).count()
        vault_posture = authority_summary
    else:
        search_available = False
        jobs = []
        failed_job_count = 0
        vault_posture = None
    local_index_debt = max(searchable_pdfs - indexed_pdfs, 0)
    needs_index = local_index_debt if search_available else 0
    attention = _attention_items(
        needs_index=needs_index,
        unavailable_media=unavailable_pdfs,
        unknown_uploaders=unknown_uploaders,
        empty_folders=empty_folders,
        jobs=jobs,
        failed_job_count=failed_job_count,
    )
    if not search_available:
        recommended_action = (
            {
                "action_label": gettext("Review search authority"),
                "action_url": f"{reverse('operations_panel')}?section=configuration",
            }
            if is_superadmin
            else None
        )
        posture = {
            "severity": "warning",
            "reason_code": "search_authority_unavailable",
            "message": gettext("Search readiness is unavailable"),
            "recommended_action": recommended_action,
        }
    elif attention:
        posture = {
            "severity": attention[0]["severity"],
            "reason_code": attention[0]["reason_code"],
            "message": attention[0]["title"],
            "recommended_action": attention[0],
        }
    else:
        posture = {
            "severity": "good",
            "reason_code": "operations_ready",
            "message": gettext("No immediate document operations need attention."),
            "recommended_action": None,
        }

    return decorate_dashboard_state({
        "observed_at": timezone.now(),
        "posture": posture,
        "folder_count": folder_count,
        "folders_with_pdfs": folders_with_documents,
        "empty_folders": empty_folders,
        "total_pdfs": total_pdfs,
        "searchable_pdfs": searchable_pdfs,
        "indexed_pdfs": indexed_pdfs,
        "unavailable_pdfs": unavailable_pdfs,
        "needs_index_pdfs": needs_index,
        "search_available": search_available,
        "unknown_uploaders": unknown_uploaders,
        "recent_pdfs": list(pdfs.order_by("-uploaded_at", "-pk")[:RECENT_INTAKE_LIMIT]),
        "maintenance_jobs": jobs,
        "vault_posture": vault_posture,
        "attention_items": attention,
        "category_page": page,
        "category_result_count": paginator.count,
        "filters": filters,
        "has_active_filters": any((
            filters.query,
            filters.readiness,
            filters.provenance,
            filters.occupancy,
            filters.ordering != "name",
        )),
        "user_count": CustomUser.objects.count() if user.role in {"admin", "superadmin"} else None,
        "active_user_count": (
            CustomUser.objects.filter(is_active=True).count()
            if user.role in {"admin", "superadmin"}
            else None
        ),
    })
