# views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.contrib import messages
from django.conf import settings
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.core.cache import cache
import traceback

from .forms import UploadForm, FolderForm
from .models import PDFFile, Folder
from .utils import  generate_gpt4_answer
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0  # consistent language detection

CACHE_TTL = getattr(settings, "CACHE_TTL", 120)  # seconds
MAX_CACHE_QUERIES = 10  # keep last 10 queries per user


# ---------------- Language Detection ----------------
def detect_language(text: str) -> str:
    try:
        return detect(text)
    except Exception:
        return "en"


# ---------------- Authentication -------------------
def logout_view(request):
    logout(request)
    return redirect('login')
#===========================Registration view====================
from django.shortcuts import render, redirect
from .forms import UserRegisterForm
from django.contrib import messages

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
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect

def login_view(request):
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        next_url = request.POST.get('next')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect(next_url or 'dashboard')
        else:
            messages.error(request, "Invalid username or password.")

    # GET request: show login page even if already logged in
    next_url = request.GET.get('next', '')
    return render(request, 'login.html', {'next': next_url})



# ---------------- Dashboard ------------------------
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from .models import Folder, PDFFile
from .forms import UploadForm


@login_required(login_url='login')
def dashboard(request, folder_id=None):
    role = getattr(request.user, 'role', 'user')

    # ---------------- If viewing a specific folder (PDFs) ----------------
    if folder_id:
        folder = get_object_or_404(Folder, id=folder_id)

        # PDF Upload (Admin/Superadmin only)
        if request.method == "POST" and (role in ["admin", "superadmin"]):
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                pdf = form.save(commit=False)
                pdf.folder = folder
                pdf.uploaded_by = request.user

                # 🔹 Extract and save keywords (from keywords_input)
                raw_keywords = form.cleaned_data.get("keywords_input", "")
                if raw_keywords:
                    pdf.keywords = [
                        k.strip().lower() for k in raw_keywords.split(",") if k.strip()
                    ]
                else:
                    pdf.keywords = []

                pdf.save()
                return redirect("dashboard", folder_id=folder.id)
        else:
            form = UploadForm()

        # List PDFs in the current folder
        pdfs = PDFFile.objects.filter(folder=folder).order_by("-uploaded_at")

        return render(
            request,
            "dashboard_pdfs.html",
            {"folder": folder, "pdfs": pdfs, "form": form, "role": role},
        )

    # ---------------- Show all folders ----------------
    folders = Folder.objects.annotate(pdf_count=Count("files")).order_by("name")

    return render(
        request,
        "dashboard.html",
        {"folders": folders, "role": role},
    )




from django.db import IntegrityError
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Folder, PDFFile

# ---------------- Create Folder / Category ----------------
from django.contrib import messages
from django.db import IntegrityError
from django.contrib.auth.decorators import login_required
from .models import Folder

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

    # SCGI users can delete only their own uploads; Admin/Superadmin can delete any
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


# ---------------- Home View ----------------
def home_view(request):
    return render(request, 'home.html')


# ---------------- Search Query ----------------

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import traceback

from .models import PDFFile, Folder
from .utils import (
    generate_gpt4_answer,
    search_pdfs,
    transliterate_marathi_to_english,
    detect_folder_by_keywords,
    detect_language,truncate_context
)


@csrf_exempt
def search_query(request):
    if request.method == "GET":
        welcome_message = (
            "🙏 नमस्कार, मी तुमचा AI सहाय्यक आहे. "
            "मी आपल्या प्रश्नांची उत्तरे सरकारी दस्तऐवज यांचा संदर्भ घेऊन देऊ शकतो."
        )
        print("[DEBUG] GET request - rendering search page")
        return render(request, "search.html", {"welcome_message": welcome_message})

    elif request.method == "POST":
        try:
            query = request.POST.get("query", "").strip()
            print(f"[DEBUG] User query received: {query}")

            if not query:
                print("[DEBUG] Empty query submitted")
                return JsonResponse({"answer": "कृपया आपला प्रश्न विचारा 🙏", "references": []})

            user_lang = detect_language(query)
            print(f"[DEBUG] Detected language: {user_lang}")

            # ---------------- Detect Folder ----------------
            detected_folder = detect_folder_by_keywords(query)
            if detected_folder:
                print(f"[DEBUG] Detected folder: {detected_folder.name} (ID: {detected_folder.id})")
            else:
                all_folder_names = Folder.objects.values_list("name", flat=True)
                folder_list_str = ", ".join(all_folder_names)
                fallback_message = (
                    "क्षमस्व, आपल्या प्रश्नाचे उत्तर संदर्भासाठी असलेल्या दस्तऐवजांमध्ये उपलब्ध नाही आहे. "
                    f"उपलब्ध category: {folder_list_str}"
                )
                print("[DEBUG] No folder detected for query")
                return JsonResponse({"answer": fallback_message, "references": []})

            # ---------------- Track session for follow-ups ----------------
            last_locked_folder_id = request.session.get("last_folder")
            current_detected_folder_id = detected_folder.id
            print(f"[DEBUG] Last locked folder ID: {last_locked_folder_id}")

            if last_locked_folder_id and current_detected_folder_id != last_locked_folder_id:
                # New topic, reset session
                print("[DEBUG] New topic detected, resetting session variables")
                request.session["first_query_done"] = False
                request.session["last_folder"] = None
                request.session["folder_locked"] = False

            # ---------------- First Query: Acts + Detected Folder ----------------
            if not request.session.get("first_query_done"):
                print("[DEBUG] Processing first query for this session")
                combined_context = []
                all_refs = []

                # 1️⃣ Search Detected folder first
                print(f"[DEBUG] Searching detected folder: {detected_folder.name}")
                detected_answer, detected_refs = search_pdfs(detected_folder, query)
                if detected_refs:
                    print(f"[DEBUG] Detected folder matched {len(detected_refs)} PDFs")
                    combined_context.append(detected_answer)
                    for ref in detected_refs:
                        ref["folder"] = "detected"
                    all_refs.extend(detected_refs)

                # 2️⃣ Search Acts folder (include only relevant PDFs)
                acts_folder = Folder.objects.filter(name__icontains="acts").first()
                if acts_folder:
                    print(f"[DEBUG] Searching Acts folder: {acts_folder.name}")
                    acts_answer, acts_refs = search_pdfs(acts_folder, query)
                    # Include only Acts PDFs with score >= 7
                    acts_refs = [ref for ref in acts_refs if ref.get("score", 0) >= 7]
                    if acts_refs:
                        print(f"[DEBUG] Relevant Acts PDFs found: {len(acts_refs)}")
                        combined_context.append(acts_answer)
                        for ref in acts_refs:
                            ref["folder"] = "acts"
                        all_refs.extend(acts_refs)
                    else:
                        print("[DEBUG] No relevant Acts PDFs, skipping Acts folder")

                if not all_refs:
                    print("[DEBUG] No relevant PDFs found in either folder")
                    return JsonResponse({
                        "answer": "⚠️ दिलेल्या PDFs मध्ये तुमच्या प्रश्नाशी संबंधित माहिती सापडली नाही.",
                        "references": []
                    })

                # ---------------- Sort and pick top N ----------------
                TOP_N = 3
                # Sort by score descending, detected folder refs first if tie
                all_refs = sorted(
                    all_refs,
                    key=lambda x: (x.get("score", 0), 1 if x.get("folder") == "detected" else 0),
                    reverse=True
                )
                top_refs = all_refs[:TOP_N]
                print(f"[DEBUG] Total top references selected: {len(top_refs)}")

                # ---------------- Combine context and truncate ----------------
                combined_context_text = "\n\n".join(combined_context)
                combined_context_text = truncate_context(combined_context_text, max_words=22500)
                print(f"[DEBUG] Total combined context length: {len(combined_context_text)}")

                # ---------------- Generate GPT answer ----------------
                combined_answer = generate_gpt4_answer(
                    user_question=query,
                    context=combined_context_text,
                    references=top_refs,
                    max_words=400
                )
                combined_answer += "\n\nआपल्याला यासंदर्भात अधिक माहिती हवी असल्यास कृपया प्रश्न अधिक विस्तारीत स्वरूपात विचारावा."
                print("[DEBUG] GPT answer generated for first query")

                # Lock folder for follow-ups
                request.session["last_folder"] = detected_folder.id
                request.session["folder_locked"] = True
                request.session["first_query_done"] = True
                print(f"[DEBUG] Folder locked for follow-ups: {detected_folder.name}")

                return JsonResponse({
                    "answer": combined_answer,
                    "references": top_refs
                })

            # ---------------- Follow-up Queries (Locked Folder) ----------------
            if request.session.get("folder_locked"):
                folder_id = request.session.get("last_folder")
                folder = Folder.objects.get(id=folder_id)
                print(f"[DEBUG] Processing follow-up query for folder: {folder.name}")

                answer, top_refs = search_pdfs(folder, query)
                if not top_refs:
                    print("[DEBUG] No PDFs matched in locked folder")
                    return JsonResponse({
                        "answer": "⚠️ दिलेल्या PDFs मध्ये तुमच्या प्रश्नाशी संबंधित माहिती सापडली नाही.",
                        "references": []
                    })

                print(f"[DEBUG] Follow-up answer generated with {len(top_refs)} references")
                return JsonResponse({"answer": answer, "references": top_refs})

        except Exception as e:
            print(f"[ERROR] {traceback.format_exc()}")
            return JsonResponse({
                "answer": "⚠️ काहीतरी चूक झाली आहे. कृपया पुन्हा प्रयत्न करा.",
                "references": []
            })










#===========================user list=================

from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from .models import CustomUser

@login_required
def user_list_view(request):
    # Fetch all users
    users = CustomUser.objects.all()
    return render(request, 'user_list.html', {'users': users})


#============================================ activate deactivate users ====================================


from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from .models import CustomUser

@login_required
def toggle_user_status(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    user.is_active = not user.is_active  # toggle active/inactive
    user.save()
    messages.success(request, f"{user.username} status updated successfully.")
    return redirect('user_list')


#=============================delete user======================
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import CustomUser

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


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import Folder

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

from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from .models import PDFFile  # adjust your PDF model import

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
    return redirect('dashboard', folder_id=folder_id)