# views.py (optimized)
import traceback
import hashlib
import os
import time
from functools import wraps
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
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils.translation import gettext

from .models import ArtifactGeneration, PDFFile, Folder, CustomUser, MaintenanceJob
from .artifact_vault import ArtifactVault, ArtifactVaultError
from .maintenance import queue_job
from .forms import UploadForm
from .forms import UserRegisterForm
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


def admin_cockpit_context(user):
    folders = searchable_folders(user).annotate(
        pdf_count=Count("files", distinct=True),
        indexed_count=Count("files", filter=Q(files__indexed=True), distinct=True),
        unknown_uploader_count=Count(
            "files",
            filter=Q(files__uploaded_by__isnull=True),
            distinct=True,
        ),
        latest_upload=Max("files__uploaded_at"),
    ).order_by("name")
    pdfs = visible_pdfs(user).select_related("folder", "uploaded_by")
    total_pdfs = pdfs.count()
    indexed_pdfs = pdfs.filter(indexed=True).count()
    unknown_uploaders = pdfs.filter(uploaded_by__isnull=True).count()
    folder_count = folders.count()
    folders_with_pdfs = folders.filter(files__isnull=False).distinct().count()
    recent_pdfs = list(pdfs.order_by("-uploaded_at")[:5])
    index_debt_folders = [
        folder for folder in folders
        if folder.pdf_count and folder.indexed_count < folder.pdf_count
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
        "indexed_pdfs": indexed_pdfs,
        "needs_index_pdfs": max(total_pdfs - indexed_pdfs, 0),
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
    indexed_pdfs = pdfs.filter(indexed=True).count()
    unknown_uploaders = pdfs.filter(uploaded_by__isnull=True).count()
    stats = {
        "total_pdfs": total_pdfs,
        "indexed_pdfs": indexed_pdfs,
        "needs_index_pdfs": max(total_pdfs - indexed_pdfs, 0),
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
    pdfs = list(PDFFile.objects.filter(folder=folder).order_by("pk"))
    eligible_ids = [
        pdf.pk for pdf in pdfs if _pdf_has_stored_search_artifacts(pdf)
    ]
    if not eligible_ids:
        return 0, PDFFile.objects.filter(folder=folder, indexed=False).count()

    index, _, _ = build_or_load_faiss_index_for_folder(folder, force_rebuild=True)
    if index is None:
        raise SearchDataIntegrityError("No searchable artifacts were available for this category")

    repaired = PDFFile.objects.filter(pk__in=eligible_ids).update(indexed=True)
    remaining = PDFFile.objects.filter(folder=folder, indexed=False).count()
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

    ready = all(value in ("ok", "not_configured") for value in checks.values())
    return JsonResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status=200 if ready else 503)


@login_required
def view_pdf(request, pdf_id):
    pdf = get_object_or_404(PDFFile, pk=pdf_id)
    if not can_access_pdf(request.user, pdf):
        return HttpResponseForbidden("You do not have permission to view this PDF.")
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
    if request.method == 'POST':
        form = UserRegisterForm(
            request.POST,
            allow_privileged_roles=allow_privileged_roles,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Registration successful! Please login.")
            return redirect('login')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserRegisterForm(allow_privileged_roles=allow_privileged_roles)
    return render(request, 'register.html', {'form': form})

#===========================Login view==========================
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
    # Fetch all users
    users = CustomUser.objects.all()
    return render(request, 'user_list.html', {'users': users})

#============================================ activate deactivate users ====================================
@admin_required
@require_POST
def toggle_user_status(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    user.is_active = not user.is_active  # toggle active/inactive
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


@superadmin_required
@require_POST
def folder_operations(request, folder_id):
    folder = get_object_or_404(Folder, pk=folder_id)
    operation = request.POST.get("operation", "").strip()

    if operation == "repair_stored_index":
        job = queue_job(
            kind="repair_indexes",
            requested_by=request.user,
            folders=[folder],
            scope={"folder_id": folder.pk},
        )
        messages.success(request, f"Repair job queued: {job.public_id}.")
        return redirect("dashboard_folder", folder_id=folder.pk)

    if operation in {"reprocess_needed", "reprocess_all"}:
        candidates = PDFFile.objects.filter(folder=folder).order_by("pk")
        if operation == "reprocess_needed":
            candidates = candidates.filter(indexed=False)
        candidates = list(candidates)
        if not candidates:
            messages.info(request, "No documents matched that maintenance action.")
            return redirect("dashboard_folder", folder_id=folder.pk)
        job = queue_job(
            kind={"reprocess_needed": "reindex_needed", "reprocess_all": "reindex_all"}[operation],
            requested_by=request.user,
            pdfs=candidates,
            scope={"folder_id": folder.pk},
        )
        messages.success(request, f"Reindex job queued for {len(candidates)} document(s): {job.public_id}.")
        return redirect("dashboard_folder", folder_id=folder.pk)

    messages.error(request, "Unknown folder maintenance action.")
    return redirect("dashboard_folder", folder_id=folder.pk)

# ---------------- Home View ----------------
def home_view(request):
    return render(request, 'home.html')




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
                    traceback.print_exc()
                    try:
                        if pdf.pk:
                            pdf.delete()
                        elif pdf.file:
                            pdf.file.delete(save=False)
                    except Exception:
                        traceback.print_exc()
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
            },
        )

    # else: folders list
    folders, cockpit = admin_cockpit_context(request.user)
    cockpit["maintenance_jobs"] = list(
        MaintenanceJob.objects.select_related("requested_by").order_by("-created_at")[:8]
    ) if is_superadmin_user(request.user) else []
    cockpit["generations"] = list(ArtifactGeneration.objects.order_by("-created_at")[:12]) if is_superadmin_user(request.user) else []
    if is_superadmin_user(request.user) and os.getenv("ARTIFACT_VAULT_ENABLED", "0").lower() in {"1", "true", "yes"}:
        try:
            cockpit["vault_generations"] = [
                item.key.rsplit("/", 1)[-1][:-5]
                for item in ArtifactVault().list_manifests()
            ]
        except ArtifactVaultError:
            cockpit["vault_generations"] = []
    else:
        cockpit["vault_generations"] = []
    return render(
        request,
        "dashboard.html",
        {"folders": folders, "cockpit": cockpit, "role": role},
    )


@superadmin_required
@require_POST
def bulk_maintenance(request):
    """Queue one bulk operation from the cockpit without doing work in Gunicorn."""
    operation = request.POST.get("operation", "").strip()
    if operation not in {"reindex_needed", "reindex_all", "repair_indexes", "validate", "sync_generation", "restore_generation"}:
        messages.error(request, "Unknown maintenance operation.")
        return redirect("dashboard")

    folder_ids = [int(value) for value in request.POST.getlist("folder_ids") if value.isdigit()]
    folders = Folder.objects.filter(pk__in=folder_ids)
    if operation == "sync_generation":
        job = queue_job(kind=operation, requested_by=request.user, scope={"folder_ids": folder_ids})
        messages.success(request, f"S3 generation sync queued: {job.public_id}.")
        return redirect("dashboard")
    if operation == "restore_generation":
        generation_id = (
            request.POST.get("generation_id", "").strip()
            or request.POST.get("generation_choice", "").strip()
        )
        if not generation_id:
            messages.error(request, "Enter an immutable generation id before staging a restore.")
            return redirect("dashboard")
        job = queue_job(
            kind=operation,
            requested_by=request.user,
            scope={},
            options={"generation_id": generation_id},
        )
        messages.success(request, f"Generation pull queued for staging: {job.public_id}.")
        return redirect("dashboard")
    if operation == "repair_indexes":
        items = list(folders)
        job = queue_job(kind=operation, requested_by=request.user, folders=items, scope={"folder_ids": folder_ids})
    else:
        pdfs = PDFFile.objects.filter(folder_id__in=folder_ids).order_by("pk")
        if operation == "reindex_needed":
            pdfs = pdfs.filter(indexed=False)
        items = list(pdfs)
        job = queue_job(kind=operation, requested_by=request.user, pdfs=items, scope={"folder_ids": folder_ids})
    if not items:
        messages.info(request, "No documents or categories matched that maintenance operation.")
    else:
        messages.success(request, f"{operation.replace('_', ' ').title()} job queued for {len(items)} item(s).")
    return redirect("dashboard")


@superadmin_required
@require_POST
def maintenance_job_action(request, job_id):
    """Cancel active work or requeue a failed job from the cockpit."""
    job = get_object_or_404(MaintenanceJob, public_id=job_id)
    action = request.POST.get("action", "").strip()
    if action == "cancel" and job.status in {"queued", "running"}:
        job.status = "cancel_requested" if job.status == "running" else "cancelled"
        if job.status == "cancelled":
            job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])
        messages.success(request, f"Maintenance job {job.public_id} cancellation recorded.")
    elif action == "retry" and job.status == "failed":
        job.status = "queued"
        job.error_summary = ""
        job.finished_at = None
        job.failed_items = 0
        job.items.filter(status="failed").update(status="queued", error_code="", error_message="", finished_at=None)
        job.save(update_fields=["status", "error_summary", "finished_at", "failed_items", "updated_at"])
        messages.success(request, f"Maintenance job {job.public_id} requeued.")
    else:
        messages.info(request, "That job cannot accept this action in its current state.")
    return redirect("dashboard")

# -------------- New logic for folder search --------------
def search_query(request):
    if request.method in {"GET", "HEAD"}:
        return render(
            request,
            "search.html",
            {
                "welcome_message": gettext(
                    "I am Sahakar AI. Click here to learn how to questions to get correct answers."
                ),
                "welcome_help_url": (
                    "https://docs.google.com/document/d/1K4Z0RnRcQFXXDxxO10xFVjAbFRBXu7errbWbqtIK8qE/"
                    "edit?usp=sharing"
                ),
                "welcome_help_label": gettext("Click Here"),
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
                                PDFFile.objects.filter(folder=folder),
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
            traceback.print_exc()
            return JsonResponse(
                {
                    "error": "search_unavailable",
                    "detail": "Document search is temporarily unavailable. Please try again later.",
                    "references": [],
                },
                status=503,
            )
        except Exception:
            traceback.print_exc()
            return JsonResponse({
                "error": "search_failed",
                "detail": "The search request could not be completed. Please try again later.",
                "references": []
            }, status=500)
