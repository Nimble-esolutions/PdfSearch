# views.py (optimized)
import traceback
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import authenticate, login, logout



from .models import PDFFile, Folder, CustomUser
from .forms import UploadForm
from .utils import (
    detect_language,
    precompute_pdf_embeddings,
    search_pdfs_fast
)

CACHE_TTL = getattr(settings, "SEARCH_CACHE_TTL", 60 * 10)

#===========================Registration view====================
def register_view(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Registration successful! Please login.")
            return redirect('login')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserRegisterForm()
    return render(request, 'register.html', {'form': form})

#===========================Login view==========================
def login_view(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            username = form.cleaned_data.get("username")
            password = form.cleaned_data.get("password")

            user = authenticate(username=username, password=password)

            if user is not None:
                login(request, user)
                return redirect("dashboard")
            else:
                messages.error(request, "Invalid username or password.")

        else:
            messages.error(request, "Invalid credentials.")

    else:
        form = AuthenticationForm()

    return render(request, "login.html", {"form": form})
# ---------------- Authentication -------------------
def logout_view(request):
    logout(request)
    return redirect('login')

#===========================user list=================
@login_required
def user_list_view(request):
    # Fetch all users
    users = CustomUser.objects.all()
    return render(request, 'user_list.html', {'users': users})

#============================================ activate deactivate users ====================================
@login_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    user.is_active = not user.is_active  # toggle active/inactive
    user.save()
    messages.success(request, f"{user.username} status updated successfully.")
    return redirect('user_list')


#=============================delete user======================
@login_required
def delete_user(request, user_id):
    current_user = request.user
    user_to_delete = get_object_or_404(CustomUser, id=user_id)

    # Only superadmins can delete other users
    if current_user.role != 'superadmin':
        messages.error(request, "You do not have permission to delete users.")
        return redirect('user_list')

    # Prevent superadmin from deleting themselves
    if user_to_delete == current_user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('user_list')

    user_to_delete.delete()
    messages.success(request, f"User '{user_to_delete.username}' deleted successfully.")
    return redirect('user_list')

#==================rename category option====================
def rename_folder(request, folder_id):
    if request.method == "POST":
        folder = get_object_or_404(Folder, id=folder_id)
        new_name = request.POST.get("folder_name")
        if new_name:
            folder.name = new_name
            folder.save()
            messages.success(request, "Category renamed successfully!")
    return redirect("dashboard")  # change "categories" to your category list URL name


#===================pdf title rename ==================
def rename_pdf(request, pdf_id):
    pdf = get_object_or_404(PDFFile, id=pdf_id)

    # Only admin/superadmin can rename
    if request.user.role not in ['admin', 'superadmin']:
        messages.error(request, "You do not have permission to rename this PDF.")
        return redirect('dashboard_pdfs')

    if request.method == 'POST':
        new_title = request.POST.get('title', '').strip()
        if new_title:
            pdf.title = new_title
            pdf.save()
            messages.success(request, "PDF renamed successfully.")
        else:
            messages.error(request, "Title cannot be empty.")
    return redirect('dashboard_pdfs')


#====================================Update and add keywords ==========================
def update_folder_keywords(request, folder_id):
    """Update folder keywords from modal."""
    if request.method == 'POST':
        folder = get_object_or_404(Folder, id=folder_id)
        new_keywords = request.POST.get('keywords', '').strip()
        folder.keywords = new_keywords
        folder.save()
        messages.success(request, f"Keywords for '{folder.name}' updated successfully!")
    return redirect('dashboard', folder_id=folder_id)

# ---------------- Delete Folder / Category ----------------
@login_required
def delete_folder(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)

    # Only admin or superadmin can delete
    if request.user.role not in ["admin", "superadmin"]:
        messages.error(request, "You don't have permission to delete categories.")
        return redirect("dashboard")

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
@login_required
def add_subcategory(request):
    if request.method == "POST":
        parent_id = request.POST.get("parent_id")
        sub_name = request.POST.get("subcategory_name", "").strip()

        if parent_id and sub_name:
            parent = get_object_or_404(Folder, id=parent_id)
            subcategory, created = Folder.objects.get_or_create(
                name=sub_name,
                parent=parent,
                defaults={'created_by': request.user}
            )
            if created:
                messages.success(request, f"Subcategory '{sub_name}' added under '{parent.name}'.")
            else:
                messages.info(request, f"Subcategory '{sub_name}' already exists under '{parent.name}'.")
        else:
            messages.error(request, "Subcategory name is required.")

    return redirect('dashboard')

# ---------------- Create Folder / Category ----------------
@login_required
def create_folder(request):
    if request.method == "POST":
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
def delete_pdf(request, file_id):
    pdf = get_object_or_404(PDFFile, pk=file_id)

    #  users can delete only their own uploads; Admin/Superadmin can delete any
    if request.user.role not in ["admin", "superadmin"] and pdf.uploaded_by != request.user:
        messages.error(request, "You don't have permission to delete this PDF.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard"))

    try:
        if pdf.file:
            pdf.file.delete(save=False)  # delete file from storage
        pdf.delete()
        messages.success(request, "PDF deleted successfully.")
    except Exception as e:
        messages.error(request, f"Error deleting PDF: {e}")

    return redirect(request.META.get("HTTP_REFERER", "dashboard"))

# ---------------- Home View ----------------
def home_view(request):
    return render(request, 'home.html')




# -------------- Dashboard upload: call precompute on upload --------------
@login_required(login_url='login')
def dashboard(request, folder_id=None):
    role = getattr(request.user, "role", "user")

    if folder_id:
        folder = get_object_or_404(Folder, id=folder_id)
        if request.method == "POST" and role in ["admin", "superadmin"]:
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                pdf = form.save(commit=False)
                pdf.folder = folder
                pdf.uploaded_by = request.user
                # Keywords handling as before
                raw_keywords = form.cleaned_data.get("keywords_input", "")
                pdf.keywords = [k.strip().lower() for k in raw_keywords.split(",") if k.strip()] if raw_keywords else []
                pdf.save()

                # Precompute extraction + embeddings — do this synchronously here for simplicity,
                # but in production you should enqueue this as a background job (Celery/RQ).
                try:
                    precompute_pdf_embeddings(pdf)
                except Exception:
                    traceback.print_exc()

                return redirect("dashboard", folder_id=folder.id)
        else:
            form = UploadForm()

        pdfs = PDFFile.objects.filter(folder=folder).order_by("-uploaded_at")
        return render(request, "dashboard_pdfs.html", {"folder": folder, "pdfs": pdfs, "form": form, "role": role})

    # else: folders list
    folders = Folder.objects.all().order_by("name")
    return render(request, "dashboard.html", {"folders": folders, "role": role})


# -------------- Search endpoint: uses caching and fast search --------------
@csrf_exempt
def search_query(request):
    if request.method == "GET":
        welcome_message = (
            "🙏 नमस्कार — मी तुमचा AI सहाय्यक आहे. प्रश्न विचारा; मी आधी अपलोड केलेल्या दस्तऐवजांचा उपयोग करून उत्तर देईन."
        )
        return render(request, "search.html", {"welcome_message": welcome_message})

    if request.method == "POST":
        try:
            query = request.POST.get("query", "").strip()
            if not query:
                return JsonResponse({"answer": "कृपया आपला प्रश्न विचारा 🙏", "references": []})

            # check cached answer first (global cache; can also be per-user)
            cache_key = f"search_ans:{hash(query)}"
            cached = cache.get(cache_key)
            if cached:
                return JsonResponse(cached)

            # detect folder by keywords (existing logic in your project)
            # simple fallback: try to match a folder name token
            detected_folder = None
            # attempt to use Folder.keywords if available
            # (assuming detect_folder_by_keywords exists in another util)
            from .utils import detect_folder_by_keywords
            detected_folder = detect_folder_by_keywords(query)

            if not detected_folder:
                # try name-based fallback
                detected_folder = Folder.objects.filter(name__icontains="act").first()  # cheap fallback
                # If still none, return helpful message
                if not detected_folder:
                    available = ", ".join(list(Folder.objects.values_list("name", flat=True)[:50]))
                    return JsonResponse({
                        "answer": f"क्षमस्व — संदर्भासाठी योग्य category सापडले नाही. उपलब्ध आहेत: {available}",
                        "references": []
                    })

            # perform fast search using precomputed embeddings + FAISS
            answer, refs = search_pdfs_fast(detected_folder, query, top_n_pdfs=3)

            # store in cache
            payload = {"answer": answer or "⚠️ संबंधित माहिती उपलब्ध नाही.", "references": refs}
            cache.set(cache_key, payload, CACHE_TTL)

            return JsonResponse(payload)

        except Exception:
            traceback.print_exc()
            return JsonResponse({"answer": "⚠️ काहीतरी चूक झाली आहे. पुन्हा प्रयत्न करा.", "references": []})
