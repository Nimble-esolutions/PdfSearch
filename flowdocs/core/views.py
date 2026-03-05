# # views.py (CLEAN MERGED STABLE VERSION)
# from django.shortcuts import render, redirect, get_object_or_404
# from django.views.decorators.csrf import csrf_exempt
# from django.contrib.auth.decorators import login_required
# from django.contrib.auth import authenticate, login, logout
# from django.contrib import messages
# from django.conf import settings
# from django.db.models import Count
# from django.http import JsonResponse
# from django.core.cache import cache
# from django.db import IntegrityError
# import traceback
# from .models import PDFFile, Folder, CustomUser
# from .forms import UploadForm, UserRegisterForm
# from .utils.faiss_utils import build_faiss_for_pdf
# from core.ingestion.pdf_loader import extract_and_store_pdf_text
# from core.ingestion.embedder import build_faiss_for_pdf

# import os
# import uuid
# from django.core.files.storage import FileSystemStorage
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.db import IntegrityError
import traceback
import os
import uuid
from .models import PDFFile, Folder, CustomUser
from .forms import UploadForm, UserRegisterForm
from core.ingestion.pdf_loader import extract_and_store_pdf_text
from core.ingestion.embedder import build_faiss_for_pdf
from core.answering.housing_answer import generate_housing_answer
from core.answering.generic_answer import generate_generic_answer
from core.utils.faiss_utils import detect_folder_by_keywords,search_pdfs_fast

print("📦 views.py loaded")
CACHE_TTL = getattr(settings, "CACHE_TTL", 120)

# ================= AUTHENTICATION =================

def register_view(request):
    if request.method == "POST":
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Registration successful! Please login.")
            return redirect("login")
    else:
        form = UserRegisterForm()
    return render(request, "register.html", {"form": form})

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        next_url = request.POST.get("next")

        user = authenticate(request, username=username, password=password)

        if user:
            login(request, user)

            # ✅ FIXED ROLE HANDLING
            if user.is_superuser or user.role in ["admin", "superadmin"]:
                return redirect("dashboard")
            else:
                return redirect("search")

        messages.error(request, "Invalid username or password.")

    next_url = request.GET.get("next", "")
    return render(request, "login.html", {"next": next_url})

def logout_view(request):
    logout(request)
    return redirect("login")

# ================= DASHBOARD =================
@login_required
def dashboard(request, folder_id=None):

    role = getattr(request.user, 'role', 'user')

    # ---------------- View PDFs Inside Folder ----------------
    if folder_id:
        folder = get_object_or_404(Folder, id=folder_id)

        # Admin Upload
        if request.method == "POST" and role in ["admin", "superadmin"]:
            form = UploadForm(request.POST, request.FILES)

            if form.is_valid():
                pdf = form.save(commit=False)
                pdf.folder = folder
                pdf.uploaded_by = request.user
                pdf.save()

                full_path = pdf.file.path

                # Extract PDF text
                extract_and_store_pdf_text(pdf, full_path)

                # Build FAISS index
                build_faiss_for_pdf(pdf)

                return redirect("dashboard", folder_id=folder.id)

        else:
            form = UploadForm()

        pdfs = PDFFile.objects.filter(folder=folder).order_by("-uploaded_at")

        return render(
            request,
            "dashboard_pdfs.html",
            {
                "folder": folder,
                "pdfs": pdfs,
                "form": form,
                "role": role,
            },
        )

    # ---------------- Folder Dashboard ----------------
    folders = Folder.objects.all().order_by("-created_at")

    return render(
        request,
        "dashboard.html",
        {
            "folders": folders,
            "role": role,
        },
    )
def dashboard432026(request, folder_id=None):
    role = getattr(request.user, 'role', 'user')

    # ---------------- View PDFs Inside Folder ----------------
    if folder_id:
        folder = get_object_or_404(Folder, id=folder_id)

        # Admin Upload
        if form.is_valid():
            pdf = form.save(commit=False)
            pdf.folder = folder
            pdf.uploaded_by = request.user
            pdf.save()
            
            full_path = pdf.file.path
            extract_and_store_pdf_text(pdf, full_path)
            build_faiss_for_pdf(pdf)  
            
            return redirect("dashboard_folder", folder_id=folder.id)
        else:
            form = UploadForm()

        pdfs = PDFFile.objects.filter(folder=folder).order_by("-uploaded_at")

        return render(
            request,
            "dashboard_pdfs.html",
            {
                "folder": folder,
                "pdfs": pdfs,
                "form": form,
                "role": role,
            },
        )

    # ---------------- Show All Folders ----------------
    folders = Folder.objects.annotate(
        pdf_count=Count("files")
    ).order_by("name")

    return render(
        request,
        "dashboard.html",
        {
            "folders": folders,
            "role": role,
        },
    )
# def dashboard4Mar26(request, folder_id=None):

#     # Only admin or superadmin allowed
#     if not (request.user.is_superuser or request.user.role in ["admin", "superadmin"]):
#         return redirect("search")

#     role = request.user.role
#     # View specific folder


#     if folder_id:
#         folder = get_object_or_404(Folder, id=folder_id)

#         if request.method == "POST":
#             form = UploadForm(request.POST, request.FILES)
#             if form.is_valid():
#                 pdf = form.save(commit=False)
#                 pdf.folder = folder
#                 pdf.uploaded_by = request.user
#                 pdf.save()
#                 return redirect("dashboard", folder_id=folder.id)
#         else:
#             form = UploadForm()

#         pdfs = PDFFile.objects.filter(folder=folder).order_by("-uploaded_at")

#         return render(
#             request,
#             "dashboard_pdfs.html",
#             {"folder": folder, "pdfs": pdfs, "form": form, "role": role},
#         )

#     folders = Folder.objects.annotate(pdf_count=Count("files")).order_by("name")
#     return render(
#         request,
#         "dashboard.html",
#         {"folders": folders, "role": role},
#     )
# ================= FOLDER MANAGEMENT =================




# ---------------- Uplad pdf ----------------
@csrf_exempt
def upload_pdf_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    pdf_file = request.FILES.get("pdf")
    subject = request.POST.get("subject")
    category = request.POST.get("category")

    if not pdf_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    # ✅ Flat storage
    upload_dir = os.path.join("data", "storage", "pdfs")
    os.makedirs(upload_dir, exist_ok=True)

    fs = FileSystemStorage(location=upload_dir)

    unique_filename = f"{uuid.uuid4()}.pdf"
    filename = fs.save(unique_filename, pdf_file)

    # ✅ Save DB entry
    pdf = PDFFile.objects.create(
        title=pdf_file.name,
        file=os.path.join("storage/pdfs", filename),
        subject=subject,
        category=category,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )

    full_pdf_path = os.path.join(upload_dir, filename)

    # ✅ Extract text
    extract_and_store_pdf_text(pdf, full_pdf_path)

    # ✅ Build FAISS → data/faiss/{pdf.id}.index
    build_faiss_for_pdf(pdf)

    return JsonResponse({
        "status": "success",
        "pdf_id": pdf.id
    })
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

@login_required
def create_folder(request):
    if request.method == "POST":
        folder_name = request.POST.get("folder_name", "").strip()

        if not folder_name:
            messages.error(request, "Category name is required.")
            return redirect("dashboard")

        try:
            Folder.objects.get_or_create(
                name=folder_name,
                defaults={"created_by": request.user},
            )
            messages.success(request, f"Category '{folder_name}' created.")
        except IntegrityError:
            messages.error(request, "Could not create category.")
    return redirect("dashboard")

@login_required
def delete_folder(request, folder_id):
    folder = get_object_or_404(Folder, id=folder_id)

    if not (request.user.is_superuser or request.user.role in ["admin", "superadmin"]):
        messages.error(request, "No permission.")
        return redirect("dashboard")

    folder.delete()
    messages.success(request, "Category deleted.")
    return redirect("dashboard")

@login_required
def delete_pdf(request, file_id):
    pdf = get_object_or_404(PDFFile, pk=file_id)

    if not (request.user.is_superuser or request.user.role in ["admin", "superadmin"]) and pdf.uploaded_by != request.user:
        messages.error(request, "No permission.")
        return redirect("dashboard")

    pdf.delete()
    messages.success(request, "PDF deleted.")
    return redirect("dashboard")

# ================= SEARCH =================
@csrf_exempt
def search_query(request):

    print("🔵 search_query called")

    if request.method == "GET":
        print("🟢 GET request received")

        welcome_message = (
            "🙏 नमस्कार, मी तुमचा AI सहाय्यक आहे. "
            "मी आपल्या प्रश्नांची उत्तरे दस्तऐवजांच्या आधारे देऊ शकतो."
        )
        return render(request, "search.html", {"welcome_message": welcome_message})

    if request.method == "POST":
        print("🟠 POST request received")

        try:
            data = json.loads(request.body)
            print("📦 Raw JSON data:", data)

            question = data.get("question", "").strip()
            subject = data.get("subject", "generic")

            print("❓ Question:", question)
            print("📚 Subject:", subject)

            if not question:
                print("⚠️ Empty question received")
                return JsonResponse({
                    "answer": "कृपया प्रश्न विचारा 🙏",
                    "references": []
                })

            # =====================================
            # 🏠 NEW HOUSING LOGIC
            # =====================================
            if subject.lower() == "housing":
                print("🏠 Housing logic triggered")

                answer = generate_housing_answer(question)

                print("✅ Housing answer generated")

                return JsonResponse({
                    "answer": answer,
                    "references": []
                })

            # =====================================
            # 📄 OLD LOGIC
            # =====================================
            print("📄 Running OLD folder detection logic")

            detected_folder = detect_folder_by_keywords(question)

            print("📂 Detected Folder:", detected_folder)

            if not detected_folder:
                print("❌ No folder detected")
                return JsonResponse({
                    "answer": "क्षमस्व, संबंधित category सापडली नाही.",
                    "references": []
                })

            print("🔍 Searching PDFs in folder:", detected_folder.name)

            answer, refs = search_pdfs_fast(detected_folder, question)

            print("📑 References found:", len(refs))

            if not refs:
                print("⚠️ No references returned")
                return JsonResponse({
                    "answer": "⚠️ संबंधित माहिती उपलब्ध नाही.",
                    "references": []
                })

            print("✅ OLD logic answer generated successfully")

            return JsonResponse({
                "answer": answer,
                "references": refs
            })

        except Exception as e:
            print("🔥 Exception occurred:", str(e))
            traceback.print_exc()

            return JsonResponse({
                "answer": "⚠️ काहीतरी चूक झाली.",
                "references": []
            })
# ================= USER MANAGEMENT =================
@login_required
def user_list_view(request):
    users = CustomUser.objects.all()
    return render(request, "user_list.html", {"users": users})


@login_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    user.is_active = not user.is_active
    user.save()
    messages.success(request, "User status updated.")
    return redirect("user_list")

@login_required
def delete_user(request, user_id):
    user_to_delete = get_object_or_404(CustomUser, id=user_id)

    if not request.user.is_superuser:
        messages.error(request, "Only superadmin can delete users.")
        return redirect("user_list")

    user_to_delete.delete()
    messages.success(request, "User deleted.")
    return redirect("user_list")

# ================= HOME =================
def home_view(request):
    return render(request, "home.html")

def rename_folder(request, folder_id):
    if request.method == "POST":
        folder = get_object_or_404(Folder, id=folder_id)
        new_name = request.POST.get("folder_name")
        if new_name:
            folder.name = new_name
            folder.save()
            messages.success(request, "Category renamed successfully!")
    return redirect("dashboard")  # change "categories" to your category list URL name

from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import Folder

def update_folder_keywords(request, folder_id):
    """Update folder keywords from modal."""
    if request.method == 'POST':
        folder = get_object_or_404(Folder, id=folder_id)
        new_keywords = request.POST.get('keywords', '').strip()
        folder.keywords = new_keywords
        folder.save()
        messages.success(request, f"Keywords for '{folder.name}' updated successfully!")
    return redirect('dashboard_folder', folder_id=folder_id)

def rename_pdf(request, pdf_id):
    pdf = get_object_or_404(PDFFile, id=pdf_id)

    # Only admin/superadmin can rename
    if request.user.role not in ['admin', 'superadmin']:
        messages.error(request, "You do not have permission to rename this PDF.")
        return redirect("dashboard", folder_id=pdf.folder.id)

    if request.method == 'POST':
        new_title = request.POST.get('title', '').strip()
        if new_title:
            pdf.title = new_title
            pdf.save()
            messages.success(request, "PDF renamed successfully.")
        else:
            messages.error(request, "Title cannot be empty.")
    return redirect("dashboard", folder_id=pdf.folder.id)

