# views.py (optimized)
import functools
import hashlib
import logging
import os
import re
import uuid
import time
from functools import wraps

logger = logging.getLogger(__name__)

import redis as redis_lib


def rate_limit(max_attempts: int = 5, window_seconds: int = 60):
    """Decorator: rate-limit by client IP using Redis."""
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method != "POST":
                return view_func(request, *args, **kwargs)
            try:
                r = redis_lib.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/1"))
                client_ip = request.META.get("REMOTE_ADDR", "unknown")
                key = f"ratelimit:login:{client_ip}"
                attempts = r.get(key)
                if attempts and int(attempts) >= max_attempts:
                    from django.http import HttpResponse
                    return HttpResponse(
                        "Too many login attempts. Try again later.",
                        status=429,
                    )
                pipe = r.pipeline()
                pipe.incr(key)
                pipe.expire(key, window_seconds)
                pipe.execute()
            except Exception:
                pass
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
from django.shortcuts import render, redirect, get_object_or_404
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Max, Q
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_GET
from django.urls import reverse
from django.utils.translation import gettext
from django.utils import timezone

from .models import ArtifactGeneration, ArtifactValidation, PDFFile, Folder, CustomUser, MaintenanceJob, MaintenanceAuditEvent, SEARCHABLE_PDF_LIFECYCLES, SiteSetting
from .configuration_registry import build_configuration_groups
from .artifact_vault import ArtifactVault, ArtifactVaultError, ArtifactVaultConfigurationError
from .metrics import metrics_view
from .maintenance import (
    MEDIA_QUARANTINE_REASONS,
    archive_pdf,
    deprecate_pdf,
    mark_pdf_unavailable,
    queue_job,
    restore_pdf,
    restore_unavailable_pdf,
)
from .maintenance_plans import (
    LOCAL_OPERATIONS as LOCAL_MAINTENANCE_JOB_KINDS,
    MaintenancePlanError,
    create_plan as create_maintenance_plan,
)
from .forms import UploadForm
from .forms import UserRegisterForm, UserManageForm, DEPARTMENT_CHOICES
from .services.dashboard_read_model import build_dashboard_state
from .operator_presentation import present_reason
from datetime import datetime
from .utils import (
    detect_language,
    precompute_pdf_embeddings,
    build_or_load_faiss_index_for_folder,
    search_pdfs_fast,
    is_general_query,
    detect_folder_by_keywords,
    semantic_folder_search,
    truncate_context,
    detect_folder_by_keywords_multi,
    TOP_K_CHUNKS,
    MAX_CONTEXT_WORDS,
    generate_gpt_answer,
    SearchDataIntegrityError,
)

CACHE_TTL = getattr(settings, "SEARCH_CACHE_TTL", 60 * 10)
ADMIN_ROLES = frozenset(("admin", "superadmin"))


def is_admin_user(user):
    return user.is_authenticated and getattr(user, "role", None) in ADMIN_ROLES


def is_superadmin_user(user):
    return user.is_authenticated and getattr(user, "role", None) == "superadmin"


def admin_required(view_func):
    """Allow only admin roles and return 403 for ordinary users."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not is_admin_user(request.user):
            return HttpResponseForbidden("Administrator permission required.")
        return view_func(request, *args, **kwargs)

    return wrapped


def superadmin_required(view_func):
    """Allow only superadmins to run high-impact maintenance actions."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not is_superadmin_user(request.user):
            return HttpResponseForbidden("Superadmin permission required.")
        return view_func(request, *args, **kwargs)

    return wrapped


def safe_referer_redirect(request, fallback="dashboard"):
    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(fallback)


def can_access_pdf(user, pdf):
    if is_admin_user(user):
        return True
    return pdf.uploaded_by_id == user.pk or (
        pdf.folder_id is not None and pdf.folder.created_by_id == user.pk
    )


def visible_pdfs(user, queryset=None, *, public=False):
    queryset = queryset if queryset is not None else PDFFile.objects.all()
    if public:
        queryset = queryset.filter(lifecycle__in=SEARCHABLE_PDF_LIFECYCLES)
    if public:
        return queryset.filter(
            folder__in=searchable_folders(user, public=True),
        ).distinct()
    if is_admin_user(user):
        return queryset
    return queryset.filter(
        Q(uploaded_by=user) | Q(folder__created_by=user)
    ).distinct()


def searchable_folders(user, *, public=False):
    """Return folders whose PDFs are visible under the current access policy."""
    folders = Folder.objects.all()
    if public:
        if settings.PUBLIC_SEARCH_ALL_FOLDERS:
            return folders
        return folders.filter(pk__in=settings.PUBLIC_SEARCH_FOLDER_IDS)
    if is_admin_user(user):
        return folders
    return folders.filter(
        Q(created_by=user) | Q(files__uploaded_by=user)
    ).distinct()


def admin_cockpit_context(user, category_query=""):
    folders = searchable_folders(user).annotate(
        pdf_count=Count("files", distinct=True),
        searchable_count=Count(
            "files",
            filter=Q(files__lifecycle__in=SEARCHABLE_PDF_LIFECYCLES),
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
    ).order_by("name")
    if category_query:
        folders = folders.filter(name__icontains=category_query)
    pdfs = visible_pdfs(user).select_related("folder", "uploaded_by")
    total_pdfs = pdfs.count()
    searchable_pdfs = pdfs.filter(
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES
    ).count()
    indexed_pdfs = pdfs.filter(
        indexed=True,
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
    ).count()
    unknown_uploaders = pdfs.filter(uploaded_by__isnull=True).count()
    folder_count = folders.count()
    folders_with_pdfs = folders.filter(files__isnull=False).distinct().count()
    recent_pdfs = list(pdfs.order_by("-uploaded_at")[:5])
    index_debt_folders = [
        folder for folder in folders
        if folder.searchable_count
        and folder.indexed_count < folder.searchable_count
    ][:4]
    owner_review_folders = [
        folder for folder in folders
        if folder.unknown_uploader_count
    ][:4]
    empty_folder_lanes = [
        folder for folder in folders
        if folder.pdf_count == 0
    ][:4]
    cockpit = {
        "folder_count": folder_count,
        "folders_with_pdfs": folders_with_pdfs,
        "empty_folders": max(folder_count - folders_with_pdfs, 0),
        "total_pdfs": total_pdfs,
        "searchable_pdfs": searchable_pdfs,
        "indexed_pdfs": indexed_pdfs,
        "needs_index_pdfs": max(searchable_pdfs - indexed_pdfs, 0),
        "unknown_uploaders": unknown_uploaders,
        "recent_pdfs": recent_pdfs,
        "index_debt_folders": index_debt_folders,
        "owner_review_folders": owner_review_folders,
        "empty_folder_lanes": empty_folder_lanes,
        "user_count": CustomUser.objects.count() if is_admin_user(user) else None,
        "active_user_count": CustomUser.objects.filter(is_active=True).count()
        if is_admin_user(user)
        else None,
    }
    return folders, cockpit


def folder_cockpit_context(user, folder):
    pdfs = visible_pdfs(
        user,
        PDFFile.objects.filter(folder=folder),
    ).select_related("uploaded_by", "folder").order_by("-uploaded_at")
    total_pdfs = pdfs.count()
    searchable_pdfs = pdfs.filter(
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES
    ).count()
    indexed_pdfs = pdfs.filter(
        indexed=True,
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
    ).count()
    unknown_uploaders = pdfs.filter(uploaded_by__isnull=True).count()
    stats = {
        "total_pdfs": total_pdfs,
        "searchable_pdfs": searchable_pdfs,
        "indexed_pdfs": indexed_pdfs,
        "needs_index_pdfs": max(searchable_pdfs - indexed_pdfs, 0),
        "unknown_uploaders": unknown_uploaders,
        "latest_upload": pdfs.aggregate(latest=Max("uploaded_at"))["latest"],
    }
    return pdfs, stats


def _pdf_has_stored_search_artifacts(pdf):
    chunks = pdf.page_chunks or []
    embeddings = pdf.chunk_embeddings or []
    return (
        isinstance(chunks, list)
        and isinstance(embeddings, list)
        and bool(chunks)
        and bool(embeddings)
        and len(chunks) == len(embeddings)
    )


def _repair_folder_index_from_stored_artifacts(folder):
    pdfs = list(
        PDFFile.objects.filter(
            folder=folder,
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
        ).order_by("pk")
    )
    eligible_ids = [
        pdf.pk
        for pdf in pdfs
        if _pdf_has_stored_search_artifacts(pdf)
        and pdf.file
        and pdf.file.name
        and pdf.file.storage.exists(pdf.file.name)
    ]
    if not eligible_ids:
        return 0, PDFFile.objects.filter(
            folder=folder,
            indexed=False,
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
        ).count()

    index, _, _ = build_or_load_faiss_index_for_folder(folder, force_rebuild=True)
    if index is None:
        raise SearchDataIntegrityError("No searchable artifacts were available for this category")

    repaired = PDFFile.objects.filter(pk__in=eligible_ids).update(indexed=True)
    remaining = PDFFile.objects.filter(
        folder=folder,
        indexed=False,
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
    ).count()
    return repaired, remaining


def _safe_login_destination(request):
    destination = request.POST.get("next") or request.GET.get("next")
    if destination and url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destination
    return reverse("dashboard")


def _public_search_rate_limited(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    client_address = forwarded_for.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR", "unknown")
    client_hash = hashlib.sha256(client_address.encode("utf-8")).hexdigest()[:16]
    bucket = int(time.time()) // settings.PUBLIC_SEARCH_RATE_WINDOW
    key = f"public-search:{client_hash}:{bucket}"
    try:
        if cache.add(key, 1, timeout=settings.PUBLIC_SEARCH_RATE_WINDOW + 1):
            return False
        return cache.incr(key) > settings.PUBLIC_SEARCH_RATE_LIMIT
    except Exception:
        return True


def _protected_references(references, *, public=False):
    """Expose only authenticated PDF view URLs to the search client."""
    protected = []
    for reference in references or []:
        item = dict(reference)
        pdf_id = item.get("pdf_id")
        item.pop("url", None)
        if pdf_id:
            item["url"] = reverse(
                "public_view_pdf" if public else "view_pdf",
                args=[pdf_id],
            )
        protected.append(item)
    return protected


def livez(request):
    return JsonResponse({"status": "ok"})


def readyz(request):
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    try:
        if getattr(settings, "REDIS_URL", ""):
            cache.set("__flowdocs_readyz", "ok", timeout=10)
            checks["cache"] = "ok" if cache.get("__flowdocs_readyz") == "ok" else "error"
        else:
            checks["cache"] = "not_configured"
    except Exception:
        checks["cache"] = "error"

    try:
        executor = MigrationExecutor(connection)
        checks["migrations"] = "ok" if not executor.migration_plan(executor.loader.graph.leaf_nodes()) else "pending"
    except Exception:
        checks["migrations"] = "error"


    try:
        checks["data"] = _data_readiness_check()
    except Exception:
        checks["data"] = "error"

    try:
        checks["backup"] = _backup_readiness_check()
    except Exception:
        checks["backup"] = "error"

    ready = all(value in ("ok", "not_configured", "empty") for value in checks.values())
    response = {"status": "ready" if ready else "not_ready", "checks": checks}
    response["runtime_generation_id"] = getattr(
        settings, "RUNTIME_GENERATION_ID", ""
    )
    response["runtime_manifest_digest"] = getattr(
        settings, "RUNTIME_MANIFEST_DIGEST", ""
    )

    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is not None:
        response["data_mode"] = env_identity.data_mode.value
    else:
        response["data_mode"] = None

    try:
        active_gen = ArtifactGeneration.objects.filter(status="active").order_by("-promoted_at").first()
        if active_gen:
            age = timezone.now() - active_gen.created_at
            response["generation_age_hours"] = round(age.total_seconds() / 3600, 1)
    except Exception:
        pass

    return JsonResponse(response, status=200 if ready else 503)



def _data_readiness_check():
    from .models import PDFFile
    total = PDFFile.objects.count()
    indexed = PDFFile.objects.filter(
        lifecycle__in=("ready", "processing")
    ).count()
    if total == 0:
        return "empty"
    ratio = indexed / total if total > 0 else 0
    if ratio < 0.5:
        return "degraded"
    if ratio < 0.9:
        return "partial"
    return "ok"


def _backup_readiness_check():
    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None or env_identity.backup_role.value == "disabled":
        return "not_configured"
    last_gen = ArtifactGeneration.objects.order_by("-created_at").first()
    if last_gen is None:
        return "no_generations"
    if last_gen.status == "failed":
        return "degraded"
    return "ok"

@login_required
def view_pdf(request, pdf_id):
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    if not can_access_pdf(request.user, pdf):
        return HttpResponseForbidden("You do not have permission to view this PDF.")
    if pdf.lifecycle == "unavailable":
        raise Http404("PDF file is unavailable")
    if not pdf.file:
        raise Http404("PDF file is unavailable")
    try:
        handle = open(pdf.file.path, "rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("PDF file is unavailable") from exc
    response = FileResponse(handle, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=False,
        filename=os.path.basename(pdf.file.name),
    )
    return response


def public_view_pdf(request, pdf_id):
    if not settings.PUBLIC_SEARCH_ENABLED:
        raise Http404("PDF file is unavailable")

    pdf = get_object_or_404(
        PDFFile.objects.filter(
            pk=pdf_id,
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            folder__in=searchable_folders(request.user, public=True),
        )
    )
    if not pdf.file:
        raise Http404("PDF file is unavailable")
    try:
        handle = open(pdf.file.path, "rb")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("PDF file is unavailable") from exc
    response = FileResponse(handle, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=False,
        filename=os.path.basename(pdf.file.name),
    )
    return response

#===========================Registration view====================
@admin_required
def register_view(request):
    # Account creation is an operator workflow. Public visitors may search,
    # but may never create accounts or select an operational role. Department
    # scoped admin roles remain a phase-2 authorization boundary.
    allow_privileged_roles = True
    allow_superadmin = request.user.role == "superadmin"
    if request.method == 'POST':
        form = UserRegisterForm(
            request.POST,
            allow_privileged_roles=allow_privileged_roles,
            allow_superadmin=allow_superadmin,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Registration successful! Please login.")
            return redirect('login')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserRegisterForm(
            allow_privileged_roles=allow_privileged_roles,
            allow_superadmin=allow_superadmin,
        )
    return render(request, 'register.html', {
        'form': form,
        'breadcrumb_items': [
            {"label": gettext("Dashboard"), "url": reverse("dashboard")},
            {"label": gettext("Users"), "url": reverse("user_list")},
            {"label": gettext("Create user"), "url": None},
        ],
    })

#===========================Login view==========================
@rate_limit(max_attempts=5, window_seconds=60)
def login_view(request):
    next_url = _safe_login_destination(request)
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")

            user = authenticate(username=username, password=password)

            if user is not None:
                login(request, user)
                return redirect(next_url)
            else:
                messages.error(request, "Invalid username or password.")

        else:
            messages.error(request, "Invalid credentials.")

    else:
        form = AuthenticationForm()

    return render(request, "login.html", {"form": form, "next": next_url})
# ---------------- Authentication -------------------
@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')

#===========================user list=================
@admin_required
def user_list_view(request):
    users = CustomUser.objects.all().order_by('username')
    query = request.GET.get('q', '').strip()
    role = request.GET.get('role', '').strip()
    department = request.GET.get('department', '').strip()
    status = request.GET.get('status', '').strip()
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query))
    if role in {'user', 'admin', 'superadmin'}:
        users = users.filter(role=role)
    if department:
        users = users.filter(department=department)
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)

    all_users = CustomUser.objects.all()
    metrics = {
        'total': all_users.count(),
        'active': all_users.filter(is_active=True).count(),
        'admins': all_users.filter(role__in=('admin', 'superadmin')).count(),
        'departments': all_users.exclude(department__isnull=True).exclude(department='').values('department').distinct().count(),
    }
    return render(request, 'user_list.html', {
        'users': users,
        'metrics': metrics,
        'filters': {'q': query, 'role': role, 'department': department, 'status': status},
        'role_choices': [('user', 'User'), ('admin', 'Admin'), ('superadmin', 'Superadmin')],
        'department_choices': DEPARTMENT_CHOICES,
        'breadcrumb_items': [
            {"label": gettext("Dashboard"), "url": reverse("dashboard")},
            {"label": gettext("Users"), "url": None},
        ],
    })


@admin_required
def edit_user(request, user_id):
    user = get_object_or_404(CustomUser, pk=user_id)
    if user.role == 'superadmin' and request.user.role != 'superadmin':
        return HttpResponseForbidden("Only superadmins can edit superadmin accounts.")
    if request.method == 'POST':
        form = UserManageForm(
            request.POST,
            instance=user,
            allow_superadmin=request.user.role == 'superadmin',
            lock_role=user.pk == request.user.pk,
        )
        if form.is_valid():
            updated = form.save(commit=False)
            if user.pk == request.user.pk:
                updated.role = user.role
            if user.role == 'superadmin' and updated.role != 'superadmin':
                return HttpResponseForbidden("Superadmin role changes require a separate protected workflow.")
            updated.save()
            messages.success(request, f"{user.username} access details updated.")
            return redirect('user_list')
    else:
        form = UserManageForm(
            instance=user,
            allow_superadmin=request.user.role == 'superadmin',
            lock_role=user.pk == request.user.pk,
        )
    return render(request, 'user_edit.html', {
        'form': form,
        'managed_user': user,
        'breadcrumb_items': [
            {"label": gettext("Dashboard"), "url": reverse("dashboard")},
            {"label": gettext("Users"), "url": reverse("user_list")},
            {"label": user.username, "url": None},
        ],
    })

#============================================ activate deactivate users ====================================
@admin_required
@require_POST
def toggle_user_status(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect('user_list')
    if user.role == 'superadmin' and request.user.role != 'superadmin':
        return HttpResponseForbidden("Only superadmins can change superadmin status.")
    if user.is_active and user.role == 'superadmin' and CustomUser.objects.filter(role='superadmin', is_active=True).count() <= 1:
        messages.error(request, "The last active superadmin cannot be deactivated.")
        return redirect('user_list')
    user.is_active = not user.is_active
    user.save()
    messages.success(request, f"{user.username} status updated successfully.")
    return redirect('user_list')


#=============================delete user======================
@admin_required
@require_POST
def delete_user(request, user_id):
    current_user = request.user
    user_to_delete = get_object_or_404(CustomUser, id=user_id)

    # Deletion remains a superadmin-only operation.
    if current_user.role != 'superadmin':
        return HttpResponseForbidden("Only superadmins can delete users.")

    # Prevent superadmin from deleting themselves
    if user_to_delete == current_user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('user_list')

    # Existing CASCADE foreign keys would otherwise remove the user's PDFs and
    # folders. Keep the account until its owned content is explicitly handled.
    if (PDFFile.objects.filter(uploaded_by=user_to_delete).exists() or
            Folder.objects.filter(created_by=user_to_delete).exists()):
        return HttpResponse(
            "The user owns documents or folders and cannot be deleted.",
            status=409,
        )

    username = user_to_delete.username
    user_to_delete.delete()
    messages.success(request, f"User '{username}' deleted successfully.")
    return redirect('user_list')

#==================rename category option====================
@admin_required
@require_POST
def rename_folder(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)
    new_name = request.POST.get("folder_name", "").strip()
    if new_name:
        folder.name = new_name
        folder.save()
        messages.success(request, "Category renamed successfully!")
    return redirect("dashboard")  # change "categories" to your category list URL name


#===================pdf title rename ==================
@admin_required
@require_POST
def rename_pdf(request, pdf_id):
    pdf = get_object_or_404(PDFFile, id=pdf_id)
    redirect_name = "dashboard_folder" if pdf.folder_id else "dashboard"
    redirect_kwargs = {"folder_id": pdf.folder_id} if pdf.folder_id else {}
    new_title = request.POST.get('title', '').strip()
    if new_title:
        pdf.title = new_title
        pdf.save()
        messages.success(request, "PDF renamed successfully.")
    else:
        messages.error(request, "Title cannot be empty.")
    return redirect(redirect_name, **redirect_kwargs)


@admin_required
@require_POST
def assign_pdf_owner(request, pdf_id):
    pdf = get_object_or_404(PDFFile, id=pdf_id)
    redirect_name = "dashboard_folder" if pdf.folder_id else "dashboard"
    redirect_kwargs = {"folder_id": pdf.folder_id} if pdf.folder_id else {}
    owner_id = request.POST.get("owner_id", "").strip()

    if not owner_id:
        messages.error(request, "Choose an owner before saving.")
        return redirect(redirect_name, **redirect_kwargs)

    owner = get_object_or_404(CustomUser, id=owner_id, is_active=True)
    pdf.uploaded_by = owner
    pdf.save(update_fields=["uploaded_by"])
    messages.success(request, f"Owner for '{pdf.title}' assigned to {owner.username}.")
    return redirect(redirect_name, **redirect_kwargs)


#====================================Update and add keywords ==========================
@admin_required
@require_POST
def update_folder_keywords(request, folder_id):
    """Update folder keywords from modal."""
    folder = get_object_or_404(Folder, id=folder_id)
    new_keywords = request.POST.get('keywords', '').strip()
    folder.keywords = new_keywords
    folder.save()
    messages.success(request, f"Keywords for '{folder.name}' updated successfully!")
    return redirect('dashboard_folder', folder_id=folder_id)

# ---------------- Delete Folder / Category ----------------
@admin_required
@require_POST
def delete_folder(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)

    # Delete all files in folder first (DB + storage)
    pdfs = PDFFile.objects.filter(folder=folder)
    for pdf in pdfs:
        if pdf.file:
            pdf.file.delete(save=False)
    pdfs.delete()

    folder_name = folder.name
    folder.delete()

    messages.success(request, f"Category '{folder_name}' deleted successfully!")
    return redirect("dashboard")

# ---------------- Add Subcategory ----------------
@admin_required
@require_POST
def add_subcategory(request):
    """
    Creates a new folder/category.
    Note: Parent folder feature was removed in migration 0009.
    Subcategories are now standalone folders.
    """
    sub_name = request.POST.get("subcategory_name", "").strip()

    if sub_name:
        _, created = Folder.objects.get_or_create(
            name=sub_name,
            defaults={'created_by': request.user}
        )
        if created:
            messages.success(request, f"Category '{sub_name}' created successfully.")
        else:
            messages.info(request, f"Category '{sub_name}' already exists.")
    else:
        messages.error(request, "Category name is required.")

    return redirect('dashboard')

# ---------------- Create Folder / Category ----------------
@admin_required
@require_POST
def create_folder(request):
    folder_name = request.POST.get("folder_name", "").strip()  # match input name
    keywords_raw = request.POST.get("folder_keywords", "")  # new input from modal
    keywords = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]  # clean list

    if not folder_name:
        messages.error(request, "Category name is required.")
        return redirect("dashboard")

    try:
        folder, created = Folder.objects.get_or_create(
            name=folder_name,
            defaults={
                'created_by': request.user,
                'keywords': keywords  # save keywords here
            }
        )
        if created:
            messages.success(request, f"Category '{folder.name}' created successfully.")
        else:
            messages.warning(request, f"Category '{folder.name}' already exists.")
    except IntegrityError:
        messages.error(request, f"Category '{folder_name}' could not be created.")

    return redirect("dashboard")


# ---------------- Delete PDF ----------------
@login_required
@require_POST
def delete_pdf(request, file_id):
    pdf = get_object_or_404(PDFFile, pk=file_id)

    # Users may remove only their own uploads; admins may remove any PDF.
    if not is_admin_user(request.user) and pdf.uploaded_by_id != request.user.pk:
        return HttpResponseForbidden("You do not have permission to delete this PDF.")

    try:
        if pdf.file:
            pdf.file.delete(save=False)  # delete file from storage
        pdf.delete()
        messages.success(request, "PDF deleted successfully.")
    except Exception as e:
        messages.error(request, f"Error deleting PDF: {e}")

    return safe_referer_redirect(request)


@admin_required
@require_POST
def deprecate_pdf_view(request, pdf_id):
    """Mark a PDF as deprecated — hides from search but preserves the row."""
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    try:
        deprecate_pdf(pdf, requested_by=request.user)
        messages.success(request, f"Document '{pdf.title}' deprecated. It will no longer appear in search results.")
    except Exception as exc:
        messages.error(request, f"Cannot deprecate: {exc}")
    return safe_referer_redirect(request)


@admin_required
@require_POST
def archive_pdf_view(request, pdf_id):
    """Mark a PDF as archived — hides from search and dashboard lists."""
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    archive_pdf(pdf, requested_by=request.user)
    messages.success(request, f"Document '{pdf.title}' archived.")
    return safe_referer_redirect(request)


@admin_required
@require_POST
def mark_pdf_unavailable_view(request, pdf_id):
    """Explicitly quarantine a document row while preserving its identity."""
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    values = {
        "expected_sha256": request.POST.get("expected_sha256", "").strip().lower(),
        "expected_size": request.POST.get("expected_size", "").strip(),
        "reason": request.POST.get("reason", "").strip(),
        "case_reference": request.POST.get("case_reference", "").strip(),
    }
    errors = {}
    if not re.fullmatch(r"[0-9a-f]{64}", values["expected_sha256"]):
        errors["expected_sha256"] = gettext(
            "Enter the complete 64-character SHA-256."
        )
    try:
        parsed_size = int(values["expected_size"])
        if parsed_size < 0 or parsed_size > 2**63 - 1:
            raise ValueError
    except ValueError:
        errors["expected_size"] = gettext(
            "Enter a valid non-negative byte size."
        )
    if values["reason"] not in MEDIA_QUARANTINE_REASONS:
        errors["reason"] = gettext("Choose a verified reason.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", values["case_reference"]):
        errors["case_reference"] = gettext(
            "Use 1–80 letters, numbers, periods, underscores, or hyphens."
        )
    if request.POST.get("confirmation", "").strip() != "MARK UNAVAILABLE":
        errors["confirmation"] = gettext(
            "Type MARK UNAVAILABLE exactly as shown."
        )
    if errors:
        request.session["media_quarantine_form"] = {
            "pdf_id": pdf.pk,
            "values": values,
            "errors": errors,
        }
        messages.error(request, gettext("Review the highlighted fields."))
        return safe_referer_redirect(request)
    try:
        outcome = mark_pdf_unavailable(
            pdf,
            requested_by=request.user,
            expected_sha256=request.POST.get("expected_sha256", ""),
            expected_size=request.POST.get("expected_size", ""),
            reason=request.POST.get("reason", ""),
            case_reference=request.POST.get("case_reference", ""),
        )
    except SearchDataIntegrityError:
        request.session["media_quarantine_form"] = {
            "pdf_id": pdf.pk,
            "values": values,
            "errors": {
                "general": gettext(
                    "The document could not be marked unavailable from this evidence."
                )
            },
        }
        messages.error(
            request,
            gettext(
                "Provide the expected SHA-256, byte size, reason, and case reference before marking this file unavailable."
            ),
        )
        return safe_referer_redirect(request)
    if not outcome.changed:
        messages.info(
            request,
            gettext("This document was already marked unavailable; no state changed."),
        )
        return safe_referer_redirect(request)
    messages.warning(
        request,
        gettext(
            "The document is unavailable and excluded from search and index work."
        ),
    )
    return safe_referer_redirect(request)


@admin_required
@require_POST
def restore_pdf_view(request, pdf_id):
    """Restore a deprecated or archived PDF to uploaded state."""
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    try:
        if pdf.lifecycle == "unavailable":
            outcome = restore_unavailable_pdf(pdf, requested_by=request.user)
        else:
            outcome = restore_pdf(pdf, requested_by=request.user)
    except SearchDataIntegrityError:
        messages.error(
            request,
            gettext(
                "Restore the verified document file to its approved location before restoring availability."
            ),
        )
        return safe_referer_redirect(request)
    if not outcome.changed:
        messages.info(
            request,
            gettext("This document was already available; no state changed."),
        )
        return safe_referer_redirect(request)
    messages.success(
        request,
        gettext(
            "Document “%(title)s” was restored and queued for index maintenance."
        )
        % {"title": pdf.title},
    )
    return safe_referer_redirect(request)


@superadmin_required
@require_POST
def folder_operations(request, folder_id):
    folder = get_object_or_404(Folder, pk=folder_id)
    operation = request.POST.get("operation", "").strip()
    mapped = {
        "repair_stored_index": "repair_indexes",
        "reprocess_needed": "reindex_needed",
        "reprocess_all": "reindex_selected",
    }.get(operation)
    if mapped is None:
        messages.error(request, "Unknown folder maintenance action.")
        return redirect("dashboard_folder", folder_id=folder.pk)
    try:
        plan = create_maintenance_plan(
            operation=mapped,
            data={"folder_ids": [folder.pk]},
            actor=request.user,
            idempotency_key=f"folder:{folder.pk}:{uuid.uuid4()}",
        )
    except MaintenancePlanError as exc:
        messages.error(request, present_reason(exc.reason_code)["title"])
        return redirect("dashboard_folder", folder_id=folder.pk)
    messages.success(
        request,
        f"Maintenance preview created for {plan.preview['pdf_count']} "
        "document(s); confirm it in Documents & Indexes.",
    )
    return redirect(
        f"{reverse('operations_panel')}?section=maintenance&plan={plan.public_id}"
    )

# ---------------- DPDA / Legal Pages ----------------
LEGAL_NAVIGATION = (
    ("privacy", gettext("Privacy Policy")),
    ("terms", gettext("Terms of Service")),
    ("data_policy", gettext("Data Policy")),
    ("cookie_policy", gettext("Cookie Policy")),
    ("disclaimer", gettext("Disclaimer")),
)


def privacy_view(request):
    return render(request, "privacy.html", {"title": "Privacy Policy", "legal_navigation": LEGAL_NAVIGATION})

def terms_view(request):
    return render(request, "terms.html", {"title": "Terms of Service", "legal_navigation": LEGAL_NAVIGATION})

def data_policy_view(request):
    return render(request, "data_policy.html", {"title": "Data Policy", "legal_navigation": LEGAL_NAVIGATION})

def cookie_policy_view(request):
    return render(request, "cookie_policy.html", {"title": "Cookie Policy", "legal_navigation": LEGAL_NAVIGATION})

def disclaimer_view(request):
    return render(request, "disclaimer.html", {"title": "Disclaimer", "legal_navigation": LEGAL_NAVIGATION})


# -------------- Dashboard upload: call precompute on upload --------------
@login_required
def dashboard(request, folder_id=None):
    role = getattr(request.user, "role", "user")

    if folder_id:
        folder = get_object_or_404(Folder, id=folder_id)
        if not is_admin_user(request.user) and folder.created_by_id != request.user.pk:
            return HttpResponseForbidden("You do not have permission to access this folder.")
        if request.method == "POST":
            if not is_admin_user(request.user):
                return HttpResponseForbidden("Administrator permission required.")
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                pdf = form.save(commit=False)
                pdf.folder = folder
                pdf.uploaded_by = request.user
                # Keywords handling as before
                raw_keywords = form.cleaned_data.get("keywords_input", "")
                pdf.keywords = [k.strip().lower() for k in raw_keywords.split(",") if k.strip()] if raw_keywords else []
                try:
                    with transaction.atomic():
                        pdf.save()
                        # Keep extraction, embeddings, and index construction in
                        # the same transaction as the PDF row.
                        precompute_pdf_embeddings(pdf)
                except Exception:
                    logger.exception("PDF upload preprocessing or indexing failed")
                    try:
                        if pdf.pk:
                            pdf.delete()
                        elif pdf.file:
                            pdf.file.delete(save=False)
                    except Exception:
                        logger.exception("Failed to clean up PDF after preprocessing failure")
                    form.add_error(
                        None,
                        "PDF upload failed during preprocessing or indexing. "
                        "No document was saved; please try again.",
                    )
                    pdfs, folder_stats = folder_cockpit_context(request.user, folder)
                    owner_options = CustomUser.objects.filter(is_active=True).order_by("username")
                    return render(
                        request,
                        "dashboard_pdfs.html",
                        {
                            "folder": folder,
                            "pdfs": pdfs,
                            "folder_stats": folder_stats,
                            "form": form,
                            "role": role,
                            "owner_options": owner_options,
                            "unavailable_presentation": present_reason(
                                "document_media_unavailable"
                            ),
                            "media_quarantine_form": request.session.pop(
                                "media_quarantine_form",
                                None,
                            ),
                            "breadcrumb_items": [
                                {"label": gettext("Dashboard"), "url": reverse("dashboard")},
                                {"label": folder.name, "url": None},
                            ],
                        },
                        status=400,
                    )

                return redirect('dashboard_folder', folder_id=folder_id)
        else:
            form = UploadForm()

        pdfs, folder_stats = folder_cockpit_context(request.user, folder)
        owner_options = CustomUser.objects.filter(is_active=True).order_by("username")
        return render(
            request,
            "dashboard_pdfs.html",
            {
                "folder": folder,
                "pdfs": pdfs,
                "folder_stats": folder_stats,
                "form": form,
                "role": role,
                "owner_options": owner_options,
                "unavailable_presentation": present_reason(
                    "document_media_unavailable"
                ),
                "media_quarantine_form": request.session.pop(
                    "media_quarantine_form",
                    None,
                ),
                "breadcrumb_items": [
                    {"label": gettext("Dashboard"), "url": reverse("dashboard")},
                    {"label": folder.name, "url": None},
                ],
            },
        )

    # else: folders list
    cockpit = build_dashboard_state(user=request.user, data=request.GET)
    folders = cockpit["category_page"].object_list
    return render(
        request,
        "dashboard.html",
        {
            "folders": folders,
            "cockpit": cockpit,
            "role": role,
            "category_query": cockpit["filters"].query,
            "breadcrumb_items": [
                {"label": gettext("Dashboard"), "url": None},
            ],
        },
    )


def _parse_bulk_filters(request):
    """Parse filter parameters from POST/GET data and return a Q object + dict."""
    filters = {}
    q_obj = Q()

    category = request.POST.get("filter_category") or request.GET.get("filter_category") or ""
    if category:
        filters["category"] = category
        q_obj &= Q(category=category)

    subject = request.POST.get("filter_subject") or request.GET.get("filter_subject") or ""
    if subject:
        filters["subject"] = subject
        q_obj &= Q(subject=subject)

    indexed = request.POST.get("filter_indexed") or request.GET.get("filter_indexed") or ""
    if indexed == "true":
        filters["indexed"] = "true"
        q_obj &= Q(indexed=True)
    elif indexed == "false":
        filters["indexed"] = "false"
        q_obj &= Q(indexed=False)

    keywords_raw = request.POST.get("filter_keywords") or request.GET.get("filter_keywords") or ""
    if keywords_raw:
        keywords = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]
        filters["keywords"] = keywords
        keyword_q = Q()
        for kw in keywords:
            keyword_q |= Q(keywords__icontains=kw)
        q_obj &= keyword_q

    uploaded_after = request.POST.get("filter_uploaded_after") or request.GET.get("filter_uploaded_after") or ""
    if uploaded_after:
        try:
            dt = datetime.strptime(uploaded_after, "%Y-%m-%d")
            filters["uploaded_after"] = uploaded_after
            q_obj &= Q(uploaded_at__gte=dt)
        except ValueError:
            pass

    uploaded_before = request.POST.get("filter_uploaded_before") or request.GET.get("filter_uploaded_before") or ""
    if uploaded_before:
        try:
            dt = datetime.strptime(uploaded_before, "%Y-%m-%d")
            filters["uploaded_before"] = uploaded_before
            q_obj &= Q(uploaded_at__lte=dt)
        except ValueError:
            pass

    return q_obj, filters


@superadmin_required
@require_POST
def bulk_maintenance(request):
    """Reject the retired direct-mutation and generation control path."""
    operation = request.POST.get("operation", "").strip()
    if operation in {"sync_generation", "restore_generation"}:
        messages.error(
            request,
            "operation_replaced: use Vault Operations for publication or restore.",
        )
        return redirect(f"{reverse('operations_panel')}?section=overview")
    else:
        messages.error(
            request,
            "operation_replaced: create and confirm a Documents & Indexes preview.",
        )
        return redirect(f"{reverse('operations_panel')}?section=maintenance")


@superadmin_required
def bulk_filter_preview(request):
    """Reject the retired ad-hoc preview path without calculating authority."""
    return JsonResponse(
        {
            "status": "retired",
            "error": "operation_replaced",
            "reason_code": "operation_replaced",
            "detail": "Create a durable preview in Documents & Indexes.",
            "recommended_action": "create_maintenance_plan",
            "workbench_url": (
                f"{reverse('operations_panel')}?section=maintenance"
            ),
        },
        status=410,
    )


@superadmin_required
@require_POST
def maintenance_job_action(request, job_id):
    """Reject direct mutation; guarded Workbench routes own cancel and retry."""
    return JsonResponse(
        {
            "status": "retired",
            "error": "operation_replaced",
            "reason_code": "operation_replaced",
            "detail": "Use the guarded Documents & Indexes job controls.",
            "recommended_action": "review_workbench_job",
            "job_id": str(job_id),
            "workbench_url": (
                f"{reverse('operations_panel')}?section=maintenance&job={job_id}"
            ),
        },
        status=410,
    )

@superadmin_required
def job_audit_trail(request, job_id):
    """Return the audit trail for a maintenance job as JSON."""
    job = get_object_or_404(MaintenanceJob, public_id=job_id)
    events = job.audit_events.order_by("created_at").values(
        "event_type", "created_at", "payload",
    )
    return JsonResponse({
        "job_id": str(job.public_id),
        "kind": job.kind,
        "status": job.status,
        "events": [
            {
                "event_type": e["event_type"],
                "created_at": e["created_at"].isoformat() if e["created_at"] else None,
                "payload": e["payload"],
            }
            for e in events
        ],
    })


@superadmin_required
def generation_validations(request, generation_id):
    """Return validation records for a generation as JSON."""
    try:
        generation = ArtifactGeneration.objects.get(generation_id=generation_id)
    except ArtifactGeneration.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    validations = generation.validations.order_by("-created_at").values(
        "validation_type", "status", "details", "created_at",
    )
    return JsonResponse({
        "generation_id": generation.generation_id,
        "status": generation.status,
        "validations": [
            {
                "validation_type": v["validation_type"],
                "status": v["status"],
                "details": v["details"],
                "created_at": v["created_at"].isoformat() if v["created_at"] else None,
            }
            for v in validations
        ],
    })


@superadmin_required
@require_POST
def promote_generation(request, generation_id):
    """Retired database-label action; require the workbench confirmation."""
    messages.warning(request, "typed_confirmation_required")
    return redirect(
        f"{reverse('operations_panel')}?section=generations"
    )


@superadmin_required
@require_POST
def rollback_generation(request, generation_id):
    """Retired local relabel action; rollback is a compensating operation."""
    messages.warning(request, "operation_replaced")
    return redirect(
        f"{reverse('operations_panel')}?section=generations"
    )


@superadmin_required
@require_POST
def purge_generation_view(request, generation_id):
    """Retired misleading purge; no deletion or status mutation is performed."""
    messages.warning(request, "operation_replaced")
    return redirect(
        f"{reverse('operations_panel')}?section=generations"
    )


@superadmin_required
@require_POST
def purge_expired_generations_view(request):
    """Retired misleading bulk purge; GC remains feature-flagged off."""
    messages.warning(request, "operation_replaced")
    return redirect(
        f"{reverse('operations_panel')}?section=generations"
    )


def _job_status_json(job):
    """Serialize a MaintenanceJob into a JSON-serializable dict."""
    items = []
    if job.total_items > 0:
        items = [
            {
                "id": item.pk,
                "status": item.status,
                "pdf_title": item.pdf.title if item.pdf else None,
                "folder_name": item.folder.name if item.folder else None,
                "error_code": item.error_code,
                "attempts": item.attempts,
            }
            for item in job.items.order_by("pk")[:50]
        ]
    progress = 0
    if job.total_items > 0:
        progress = round((job.completed_items + job.failed_items) / job.total_items * 100)
    return {
        "job_id": str(job.public_id),
        "kind": job.kind,
        "kind_display": job.get_kind_display(),
        "status": job.status,
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "failed_items": job.failed_items,
        "progress": progress,
        "error_summary": job.error_summary[:200] if job.error_summary else "",
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "items": items,
    }


@superadmin_required
def job_status(request, job_id):
    """Return detailed status for a single maintenance job as JSON."""
    job = get_object_or_404(MaintenanceJob, public_id=job_id)
    return JsonResponse(_job_status_json(job))


@superadmin_required
def active_jobs(request):
    """Return all queued/running/cancel_requested jobs as JSON."""
    jobs = MaintenanceJob.objects.filter(
        status__in=("queued", "running", "cancel_requested"),
        kind__in=sorted(LOCAL_MAINTENANCE_JOB_KINDS),
    ).order_by("-created_at")[:20]
    return JsonResponse({
        "jobs": [_job_status_json(job) for job in jobs],
        "count": len(jobs),
    })


@require_GET
def robots_txt(request):
    """Serve robots.txt — allow public pages, disallow admin and auth routes."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /search/",
        "Allow: /privacy/",
        "Allow: /terms/",
        "Allow: /data-policy/",
        "Allow: /cookies/",
        "Allow: /disclaimer/",
        "Allow: /livez",
        "Allow: /readyz",
        "Disallow: /dashboard/",
        "Disallow: /register/",
        "Disallow: /login/",
        "Disallow: /logout/",
        "Disallow: /pdf/",
        "Disallow: /folder/",
        "Disallow: /i18n/",
        "",
        "Sitemap: https://ai-sahakar.net/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
def sitemap_xml(request):
    """Serve sitemap.xml with static public URLs."""
    base_url = "https://ai-sahakar.net"
    lastmod = timezone.now().strftime("%Y-%m-%d")
    urls = [
        {"loc": f"{base_url}/", "changefreq": "weekly", "priority": "1.0"},
        {"loc": f"{base_url}/privacy/", "changefreq": "monthly", "priority": "0.5"},
        {"loc": f"{base_url}/terms/", "changefreq": "monthly", "priority": "0.5"},
        {"loc": f"{base_url}/data-policy/", "changefreq": "monthly", "priority": "0.5"},
        {"loc": f"{base_url}/cookies/", "changefreq": "monthly", "priority": "0.5"},
        {"loc": f"{base_url}/disclaimer/", "changefreq": "monthly", "priority": "0.5"},
    ]
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        xml_parts.append("  <url>")
        xml_parts.append(f"    <loc>{url['loc']}</loc>")
        xml_parts.append(f"    <lastmod>{lastmod}</lastmod>")
        xml_parts.append(f"    <changefreq>{url['changefreq']}</changefreq>")
        xml_parts.append(f"    <priority>{url['priority']}</priority>")
        xml_parts.append("  </url>")
    xml_parts.append("</urlset>")
    return HttpResponse("\n".join(xml_parts), content_type="application/xml")


# -------------- New logic for folder search --------------
def search_query(request):
    if request.method in {"GET", "HEAD"}:
        if request.path.rstrip("/") == "/search":
            return redirect("home", permanent=True)
        return render(
            request,
            "search.html",
            {
                "welcome_message": gettext(
                    "I am Sahakar AI. Learn how to ask better questions and get more useful answers."
                ),
                "welcome_help_url": (
                    "https://docs.google.com/document/d/1K4Z0RnRcQFXXDxxO10xFVjAbFRBXu7errbWbqtIK8qE/"
                    "edit?usp=sharing"
                ),
                "welcome_help_label": gettext("Click Here"),
                "welcome_prompt_label": gettext("Try asking"),
                "welcome_prompts": [
                    gettext("Housing society audit procedure"),
                    gettext("Co-operative member rights"),
                    gettext("Agricultural credit society rules"),
                    gettext("Society election rules"),
                    gettext("Documents required for registration"),
                    gettext("Procedure for amending society bye-laws"),
                ],
                "workbench_copy": {
                    "empty_heading": gettext("Ask AI Sahakar"),
                    "empty_support": gettext(
                        "Search Maharashtra cooperative laws, rules, circulars and department guidance in plain language."
                    ),
                    "empty_trust": gettext(
                        "Answers are prepared from the department's indexed documents and include source references."
                    ),
                    "common_topics": gettext("Common topics"),
                    "suggested_questions": gettext("Suggested questions"),
                    "browse_topics": gettext("Browse all topics"),
                    "new_question": gettext("New question"),
                    "how_to_ask": gettext("How to ask a useful question"),
                    "how_to_ask_detail": gettext(
                        "Include the Act, Rule, section number, society type or topic when you know it."
                    ),
                    "how_it_works": gettext("How AI Sahakar works"),
                    "how_it_works_detail": gettext(
                        "Ask in English or Marathi. AI Sahakar searches official department documents and shows the sources used for the answer."
                    ),
                    "evidence_title": gettext("Evidence and help"),
                    "evidence_detail": gettext(
                        "Your answer will show the official documents used so you can verify the explanation."
                    ),
                    "human_support": gettext("Human department support"),
                    "contact_whatsapp": gettext("Contact on WhatsApp"),
                    "feedback": gettext("Send feedback"),
                    "whatsapp": gettext("Share on WhatsApp"),
                    "share_answer": gettext("Share answer"),
                    "share_title": gettext("AI Sahakar answer"),
                    "share_question": gettext("Question"),
                    "share_answer_label": gettext("Answer"),
                    "share_sources": gettext("Source documents"),
                    "share_menu_label": gettext("Share options"),
                    "share_whatsapp": gettext("WhatsApp"),
                    "share_telegram": gettext("Telegram"),
                    "share_teams": gettext("Microsoft Teams"),
                    "copy_share_text": gettext("Copy share text"),
                    "copy_failed": gettext("Copy failed"),
                    "view_sources": gettext("View sources"),
                    "about_answer": gettext("About this answer"),
                    "view_original": gettext("View original PDF"),
                    "close": gettext("Close"),
                    "copy_answer": gettext("Copy answer"),
                    "copied": gettext("Copied"),
                    "empty_sources": gettext("Sources will appear here after you ask a question."),
                    "word_count": gettext("of 30 words"),
                },
                "search_loading_stages": [
                    gettext("Searching official department documents…"),
                    gettext("Reviewing relevant sources…"),
                    gettext("Preparing an answer…"),
                ],
                "retry_label": gettext("Try again"),
                "about_close_label": gettext("Close"),
                "source_documents_label": gettext("Source documents"),
                "search_messages": {
                    "question_too_long": gettext("Question is too long"),
                    "question_too_long_detail": gettext("Please keep it within 30 words."),
                    "sign_in": gettext("Please sign in to search."),
                    "security": gettext("The request could not be secured. Refresh the page and try again."),
                    "limit": gettext("Your question cannot exceed 30 words."),
                    "request_failed": gettext("The search request could not be completed. Please try again later."),
                    "unavailable": gettext("The search service is temporarily unavailable. Please try again later."),
                    "rate_limited": gettext("Please wait a moment before starting another search."),
                    "search_unavailable": gettext("Search unavailable"),
                    "try_later": gettext("Please try again later."),
                    "timeout": gettext("Search is taking longer than expected"),
                    "try_again": gettext("Please try again."),
                    "unexpected": gettext("Something went wrong"),
                    "feedback": gettext("Send feedback"),
                },
                "display_service_footer": getattr(settings, "DISPLAY_SERVICE_FOOTER", False),
                "whatsapp_number": os.environ.get("PUBLIC_WHATSAPP_NUMBER", ""),
                "indexed_count": PDFFile.objects.filter(
                    indexed=True,
                    lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
                ).count(),
                "total_count": PDFFile.objects.filter(
                    lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
                ).count(),
            },
        )

    if request.method == "POST":
        public_search = not request.user.is_authenticated
        if public_search and not settings.PUBLIC_SEARCH_ENABLED:
            return JsonResponse(
                {
                    "error": "authentication_required",
                    "detail": "Authentication is required to execute document search.",
                    "references": [],
                },
                status=401,
            )
        try:
            query = request.POST.get("query", "").strip()
            language = request.POST.get("language", request.LANGUAGE_CODE).split("-", 1)[0]
            if language not in {"en", "mr"}:
                language = "en"

            if len(query.split()) > settings.PUBLIC_SEARCH_MAX_WORDS:
                return JsonResponse(
                    {
                        "error": "query_too_long",
                        "detail": f"Queries are limited to {settings.PUBLIC_SEARCH_MAX_WORDS} words.",
                        "references": [],
                    },
                    status=400,
                )

            if public_search and _public_search_rate_limited(request):
                return JsonResponse(
                    {
                        "error": "rate_limited",
                        "detail": "Please wait before submitting another public search.",
                        "references": [],
                    },
                    status=429,
                )

            if not query:
                return JsonResponse({
                    "answer": gettext("Please ask your question 🙏"),
                    "references": []
                })

            # --------------------------------------------------
            # 1️⃣ GENERAL QUESTIONS (NO REFERENCES)
            # --------------------------------------------------
            if is_general_query(query):
                return JsonResponse({
                    "answer": gettext("Hello! How can I help you?"),
                    "references": []
                })

            # --------------------------------------------------
            # 2️⃣ KEYWORD-BASED FOLDER DETECTION
            # --------------------------------------------------
            detected = detect_folder_by_keywords_multi(
                query,
                min_score_threshold=0.40,
                folders=searchable_folders(request.user, public=public_search),
            )

            visible_folder_ids = set(
                searchable_folders(request.user, public=public_search).values_list("pk", flat=True)
            )

            # --------------------------------------------------
            # 🔒 DOMINANCE-AWARE FOLDER LOCKING (FIX)
            # --------------------------------------------------
            locked_folders = []

            if detected:
                top_folder, top_score = detected[0]
                second_score = detected[1][1] if len(detected) > 1 else 0.0

                # HARD LOCK if dominant
                if top_score >= 0.65 and (top_score - second_score) >= 0.15:
                    locked_folders = [top_folder]
                else:
                    locked_folders = [folder for folder, _ in detected]
                locked_folders = [
                    folder for folder in locked_folders if folder.pk in visible_folder_ids
                ]

            # --------------------------------------------------
            # 3️⃣ SEARCH ONLY INSIDE LOCKED FOLDERS
            # --------------------------------------------------
            if locked_folders:
                aggregated_scores = {}

                for folder in locked_folders:
                    try:
                        scoped_pdfs = (
                            None
                            if is_admin_user(request.user)
                            else visible_pdfs(
                                request.user,
                                PDFFile.objects.filter(
                                    folder=folder,
                                    lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
                                ),
                                public=public_search,
                            )
                        )
                        _, refs = search_pdfs_fast(
                            folder,
                            query,
                            top_n_pdfs=3,
                            pdfs=scoped_pdfs,
                            language=language,
                        )

                        for r in refs:
                            key = r.get("pdf_id") or r.get("title")
                            score = float(r.get("score", 0))

                            if key in aggregated_scores:
                                aggregated_scores[key]["score"] += score
                            else:
                                aggregated_scores[key] = {
                                    "score": score,
                                    "meta": {
                                        "title": r.get("title"),
                                        "pdf_id": r.get("pdf_id"),
                                        "folder": folder.name,
                                        "uploaded_at": r.get("uploaded_at"),
                                    }
                                }
                    except SearchDataIntegrityError:
                        raise
                    except Exception:
                        continue

                if aggregated_scores:
                    ranked = sorted(
                        aggregated_scores.values(),
                        key=lambda x: x["score"],
                        reverse=True
                    )[:3]

                    final_refs = []
                    combined_snippets = []

                    for item in ranked:
                        meta = item["meta"]
                        final_refs.append({
                            "title": meta["title"],
                            "pdf_id": meta["pdf_id"],
                            "folder": meta["folder"],
                            "uploaded_at": meta["uploaded_at"],
                            "score": item["score"],
                        })

                        pdf = visible_pdfs(
                            request.user,
                            PDFFile.objects.filter(pk=meta["pdf_id"]),
                            public=public_search,
                        ).first()
                        if pdf:
                            combined_snippets.append(f"--- {pdf.title} ---")
                            combined_snippets.extend(
                                (pdf.page_chunks or [])[:TOP_K_CHUNKS]
                            )

                    combined_context = truncate_context(
                        "\n\n".join(combined_snippets),
                        MAX_CONTEXT_WORDS
                    )

                    answer = generate_gpt_answer(
                        user_question=query,
                        context=combined_context,
                        references=final_refs,
                        max_words=400,
                        language=language,
                    )

                    return JsonResponse({
                        "answer": answer,
                        "references": _protected_references(final_refs, public=public_search)
                    })

                # Folder matched but no PDFs → DO NOT go to Act
                return JsonResponse({
                    "answer": gettext(
                        "Sorry, information related to this topic is not available "
                        "in this section."
                    ),
                    "references": []
                })

            # --------------------------------------------------
            # 4️⃣ NO FOLDER MATCH → ACT FOLDER ONLY HERE
            # --------------------------------------------------
            acts_folder = Folder.objects.filter(name__icontains="act").first()

            if acts_folder and searchable_folders(request.user, public=public_search).filter(pk=acts_folder.pk).exists():
                answer, refs = search_pdfs_fast(
                    acts_folder,
                    query,
                    top_n_pdfs=3,
                    pdfs=(
                        None
                        if is_admin_user(request.user)
                        else visible_pdfs(
                            request.user,
                            PDFFile.objects.filter(folder=acts_folder),
                            public=public_search,
                        )
                    ),
                    language=language,
                )
                if answer.strip():
                    return JsonResponse({
                        "answer": answer,
                        "references": _protected_references(refs, public=public_search)
                    })

            # --------------------------------------------------
            # 5️⃣ FINAL FALLBACK
            # --------------------------------------------------
            return JsonResponse({
                "answer": gettext(
                    "Sorry, the requested information was not found in the available documents."
                ),
                "references": []
            })

        except SearchDataIntegrityError:
            logger.exception("Search integrity error")
            return JsonResponse(
                {
                    "error": "search_unavailable",
                    "detail": "Document search is temporarily unavailable. Please try again later.",
                    "references": [],
                },
                status=503,
            )
        except Exception:
            logger.exception("Unhandled search error")
            return JsonResponse({
                "error": "search_failed",
                "detail": "The search request could not be completed. Please try again later.",
                "references": []
            }, status=500)


@superadmin_required
def operations_panel(request):
    """Compatibility alias retained for legacy callers."""
    return redirect(f"{reverse('operations_panel')}?section=overview")
def health_data(request):
    """Public: return coarse data readiness status only."""
    return JsonResponse({"status": _data_readiness_check()})

def health_lease(request):
    """Public: return coarse lease status only."""
    env_identity = getattr(settings, "ENV_IDENTITY", None)
    if env_identity is None or env_identity.backup_role.value == "disabled":
        return JsonResponse({"status": "not_configured"})
    try:
        from .lease import get_lease_status
        status = get_lease_status(env_identity.dataset_id)
        return JsonResponse({"status": "held" if status is not None else "not_held"})
    except Exception:
        return JsonResponse({"status": "error"})


@superadmin_required
def operations_data(request):
    """Compatibility read endpoint backed by the redacted workbench model."""
    from vaultops.services.read_model import build_workbench_state

    state = build_workbench_state()
    return JsonResponse({
        "status": state["status"],
        "reason_code": state["reason_code"],
        "observed_at": state["observed_at"],
        "state_version": state["state_version"],
        "environment": state["environment"],
        "authority": state["authority"],
        "sync": state["sync"],
    })


@superadmin_required
def operations_lease(request):
    """Compatibility lease endpoint; owner tokens never leave the server."""
    from vaultops.services.read_model import build_workbench_state

    state = build_workbench_state()
    return JsonResponse({
        "status": state["lease"]["state"],
        "reason_code": state["lease"].get("reason_code", ""),
        "dataset_id": state["environment"]["dataset_id"],
        "lease": state["lease"],
        "observed_at": state["observed_at"],
        "state_version": state["state_version"],
    })


@superadmin_required
def s3_operations_view(request):
    """Redirect the retired mixed-control page to the local maintenance hub."""
    return redirect(f"{reverse('operations_panel')}?section=maintenance")


ALLOWED_SETTING_KEYS = {
    "PUBLIC_SEARCH_ENABLED", "DISPLAY_SERVICE_FOOTER",
    "PUBLIC_SEARCH_RATE_LIMIT", "PUBLIC_SEARCH_RATE_WINDOW",
    "PUBLIC_SEARCH_MAX_WORDS", "MAINTENANCE_SCHEDULER_ENABLED",
    "BACKUP_SYNC_MODE", "DATA_MODE", "EXTERNAL_SIDE_EFFECTS_MODE",
}


def get_setting(key: str, default: str = "") -> str:
    """Read a setting from DB → cache → env var chain."""
    cache_key = f"sitesetting:{key}"
    value = cache.get(cache_key)
    if value is not None:
        return value
    try:
        obj = SiteSetting.objects.get(key=key)
        value = obj.value
        cache.set(cache_key, value, timeout=300)
        return value
    except SiteSetting.DoesNotExist:
        pass
    return os.environ.get(key, default)


@superadmin_required
def settings_view(request):
    """Superadmin settings page — read-only env identity + editable feature flags."""

    SETTINGS_EDIT_ENABLED = os.environ.get("SETTINGS_EDIT_ENABLED", "0") == "1"

    feature_flags = {}
    setting_values = {}
    for key in (
        "PUBLIC_SEARCH_ENABLED", "DISPLAY_SERVICE_FOOTER",
        "PUBLIC_SEARCH_RATE_LIMIT", "PUBLIC_SEARCH_RATE_WINDOW",
        "PUBLIC_SEARCH_MAX_WORDS", "MAINTENANCE_SCHEDULER_ENABLED",
        "BACKUP_SYNC_MODE", "DATA_MODE", "EXTERNAL_SIDE_EFFECTS_MODE",
    ):
        db_value = SiteSetting.objects.filter(key=key).values_list("value", flat=True).first()
        env_value = os.environ.get(key, "")
        current_value = db_value if db_value not in (None, "") else env_value
        feature_flags[key] = {
            "current": current_value,
            "source": "database" if db_value not in (None, "") else "environment",
            "env_value": env_value,
        }
        setting_values[key] = db_value

    vault_status = {"enabled": False, "configured": False, "reachable": False, "bucket_exists": False, "healthy": False, "error": ""}
    try:
        health = ArtifactVault().health_check()
        vault_status.update({
            "enabled": health.enabled,
            "configured": health.configured,
            "reachable": health.reachable,
            "bucket_exists": health.bucket_exists,
            "healthy": health.healthy,
            "error": health.error_code,
        })
    except ArtifactVaultConfigurationError:
        vault_status["error"] = "configuration_invalid"
    except Exception:
        vault_status["error"] = "probe_failed"

    env_fields = {}
    try:
        env_identity = getattr(settings, "ENV_IDENTITY", None)
        if env_identity:
            identity = env_identity
            env_fields = {
                "app_env": identity.app_env.value if identity.app_env else "unknown",
                "dataset_id": identity.dataset_id,
                "authoritative_dataset_id": identity.authoritative_dataset_id,
                "restore_source_dataset_id": identity.restore_source_dataset_id,
                "production_source_id": identity.production_source_id,
                "deployment_id": identity.deployment_id,
                "instance_id": (identity.instance_id[:20] if identity.instance_id else ""),
                "build_digest": (identity.app_image_digest or identity.build_image_digest)[:24] if (identity.app_image_digest or identity.build_image_digest) else "",
                "app_release": identity.app_release_version or identity.build_release_version or "",
                "backup_role": identity.backup_role.value if identity.backup_role else "",
                "backup_sync_mode": identity.backup_sync_mode.value if identity.backup_sync_mode else "",
                "scheduler_enabled": identity.maintenance_scheduler_enabled,
                "data_mode": identity.data_mode.value if identity.data_mode else "",
                "side_effects": identity.external_side_effects.value if identity.external_side_effects else "",
            }
    except Exception:
        pass

    context = {
        "settings_edit_enabled": SETTINGS_EDIT_ENABLED,
        "feature_flags": feature_flags,
        "configuration_groups": build_configuration_groups(settings, setting_values),
        "vault_status": vault_status,
        "env_fields": env_fields,
        "title": "Settings & Configuration",
        "breadcrumb_items": [
            {"label": gettext("Dashboard"), "url": reverse("dashboard")},
            {"label": gettext("Settings"), "url": None},
        ],
    }
    return render(request, "dashboard_settings.html", context)


@superadmin_required
@require_POST
def save_settings(request):
    """Save feature flag values to SiteSetting table."""
    if os.environ.get("SETTINGS_EDIT_ENABLED", "0") != "1":
        messages.error(request, "Settings editing is not enabled.")
        return redirect("settings")

    saved = 0
    for key in ALLOWED_SETTING_KEYS:
        value = request.POST.get(key, "").strip()
        if value:
            SiteSetting.objects.update_or_create(
                key=key,
                defaults={"value": value, "updated_by": request.user}
            )
            cache.delete(f"sitesetting:{key}")
            saved += 1

    messages.success(request, f"Saved {saved} settings. Changes take effect immediately.")
    return redirect("settings")
