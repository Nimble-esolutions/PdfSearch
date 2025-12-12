# views.py (optimized)
import traceback
import hashlib
import os
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
from django.db.models import Count

from .models import PDFFile, Folder, CustomUser
from .forms import UploadForm
from .forms import UserRegisterForm
from datetime import datetime
from .utils import (
    detect_language,
    precompute_pdf_embeddings,
    search_pdfs_fast,
    is_general_query,
    detect_folder_by_keywords,
    semantic_folder_search,
    truncate_context,
    detect_folder_by_keywords_multi,
    TOP_K_CHUNKS,
    MAX_CONTEXT_WORDS,
    generate_gpt_answer
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
    #folders = Folder.objects.all().order_by("name")
    folders = Folder.objects.annotate(pdf_count=Count('files', distinct=True)).order_by("name")
    return render(request, "dashboard.html", {"folders": folders, "role": role})


# -------------- Search endpoint: uses caching and fast search --------------
@csrf_exempt
def search_query1(request):
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


#CACHE_TTL = 600   # (or your setting)

# -------------- Search : uses caching and fast search from multiple --------------
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
                return JsonResponse({
                    "answer": "कृपया आपला प्रश्न विचारा 🙏",
                    "references": []
                })

            # --------------- GENERAL QUESTION (NO REFERENCES) ---------------
            if is_general_query(query):
                ql = query.lower()

                # Greetings
                if "good" in ql and ("morning" in ql or "afternoon" in ql or "evening" in ql or "day" in ql):
                    return JsonResponse({
                        "answer": "Good day! How can I assist you today?",
                        "references": []
                    })

                if "hello" in ql or "hi" in ql or "नमस्कार" in ql:
                    return JsonResponse({"answer": "नमस्कार! कशासाठी मदत करू शकतो?", "references": []})

                # Default fallback for any greeting
                return JsonResponse({"answer": "How can I assist you today?", "references": []})


            # -------------------------------
            # 2️⃣ FOLDER DETECTION (multi)
            # -------------------------------
            # detect_folder_by_keywords_multi returns list of (folder, score) sorted desc
            candidate_folders = detect_folder_by_keywords_multi(query, min_score_threshold=0.50)

            # If we have candidate folders, search each and aggregate results
            if candidate_folders:
                print(f"🔎 Searching {len(candidate_folders)} candidate folders for query: {query}")

                # aggregated scores keyed by PDF title (use PDF ID if you have it in refs)
                aggregated_pdf_scores = {}  # key -> {"score": float, "meta": {...}}

                for folder, folder_score in candidate_folders:
                    try:
                        print(f"   ➤ Searching folder: {folder.name} (folder_score={folder_score})")
                        # tune per-folder top results; keep small to limit work
                        _, refs = search_pdfs_fast(folder, query, top_n_pdfs=2)

                        if not refs:
                            print(f"     ⚠ No refs from {folder.name}")
                            continue

                        for r in refs:
                            # stable key: prefer 'url' if unique, else 'title'
                            key = r.get("url") or r.get("title") or str(r.get("uploaded_at")) or folder.name
                            r_score = float(r.get("score", 0) or 0)

                            # incorporate folder_score as a multiplicative weight (tunable)
                            combined_score = r_score + float(folder_score)

                            if key in aggregated_pdf_scores:
                                aggregated_pdf_scores[key]["score"] += combined_score
                            else:
                                meta = {
                                    "title": r.get("title"),
                                    "folder": r.get("folder") or folder.name,
                                    "url": r.get("url"),
                                    "uploaded_at": r.get("uploaded_at"),
                                }
                                aggregated_pdf_scores[key] = {"score": combined_score, "meta": meta}

                    except Exception as e:
                        print(f"     ❌ Error searching folder {folder.name}: {e}")
                        continue

                # If we found aggregated PDFs, pick top ones and build context
                if aggregated_pdf_scores:
                    sorted_pdfs = sorted(
                        [
                            {"key": k, "score": v["score"], "meta": v["meta"]}
                            for k, v in aggregated_pdf_scores.items()
                        ],
                        key=lambda x: x["score"],
                        reverse=True
                    )

                    top_n_pdfs_overall = 3
                    top_selection = sorted_pdfs[:top_n_pdfs_overall]

                    # Build final_refs in the shape expected by the frontend
                    final_refs = []
                    for sel in top_selection:
                        m = sel["meta"]
                        final_refs.append({
                            "title": m.get("title"),
                            "folder": m.get("folder"),
                            "url": m.get("url"),
                            "uploaded_at": m.get("uploaded_at"),
                            "score": sel["score"],
                        })

                    # Build combined_context from the selected PDFs' page_chunks
                   # After aggregated PDFs selected:
                    combined_snippets = []

                    for ref in final_refs:
                        try:
                            pdf_obj = None

                            # Extract filename safely
                            if ref.get("url"):
                                filename = os.path.basename(ref["url"])  # e.g., "MCS1.pdf"
                                pdf_obj = PDFFile.objects.filter(file__icontains=filename).first()

                            # Fallback: search by title if no URL match
                            if not pdf_obj and ref.get("title"):
                                pdf_obj = PDFFile.objects.filter(title=ref["title"]).first()

                            if pdf_obj:
                                snippets = getattr(pdf_obj, "page_chunks", [])[:TOP_K_CHUNKS]

                                combined_snippets.append(
                                    f"--- {ref['title']} (in {ref['folder']}) ---"
                                )
                                combined_snippets.extend(snippets)

                            else:
                                print(f"⚠ Could not map PDF to DB: {ref}")

                        except Exception as e:
                            print(f"⚠ Failed to load snippets for {ref.get('title')}: {e}")
                            continue

                    # Build final context
                    combined_context = "\n\n".join(combined_snippets)
                    combined_context = truncate_context(combined_context, max_words=MAX_CONTEXT_WORDS)

                    # Generate the answer using existing generator (which handles language detection & caching)
                    answer = generate_gpt_answer(user_question=query, context=combined_context, references=final_refs, max_words=400)

                    if answer and answer.strip():
                        return JsonResponse({"answer": answer, "references": final_refs})

                # If we reach here, candidate folders yielded no useful PDF/context → fallthrough to acts folder

            # -----------------------------------
            # 3️⃣ NO FOLDER FOUND or no useful results → SEARCH IN "ACTS"
            # -----------------------------------
            acts_folder = Folder.objects.filter(name__icontains="act").first()

            if not acts_folder:
                return JsonResponse({
                    "answer": "क्षमस्व — कोणत्याही category मध्ये माहिती आढळली नाही.",
                    "references": []
                })

            answer, refs = search_pdfs_fast(acts_folder, query, top_n_pdfs=3)

            if answer.strip():
                return JsonResponse({"answer": answer, "references": refs})

            # -----------------------------------
            # 3A. ACT FOLDER ALSO HAS NO ANSWER
            # -----------------------------------
            return JsonResponse({
                "answer": "क्षमस्व, उपलब्ध दस्तऐवजांमध्ये संबंधित माहिती सापडली नाही.",
                "references": []
            })

        except Exception as e:
            print("Error:", e)
            return JsonResponse({
                "answer": "⚠️ काहीतरी चूक झाली. कृपया पुन्हा प्रयत्न करा.",
                "references": []
            })


def search_query_old(request):
    if request.method == "GET":
        welcome_message = (
            "🙏 नमस्कार — मी तुमचा AI सहाय्यक आहे. प्रश्न विचारा; मी आधी अपलोड केलेल्या दस्तऐवजांचा उपयोग करून उत्तर देईन."
        )
        return render(request, "search.html", {"welcome_message": welcome_message})

    if request.method == "POST":
        try:
            query = request.POST.get("query", "").strip()
            if not query:
                return JsonResponse({
                    "answer": "कृपया आपला प्रश्न विचारा 🙏",
                    "references": []
                })

            # --------------- GENERAL QUESTION (NO REFERENCES) ---------------
            if is_general_query(query):
                ql = query.lower()

                # Greetings
                if "good" in ql and ("morning" in ql or "afternoon" in ql or "evening" in ql or "day" in ql):
                    return JsonResponse({
                        "answer": "Good day! How can I assist you today?",
                        "references": []
                    })

                if "hello" in ql or "hi" in ql or "नमस्कार" in ql:
                    return JsonResponse({"answer": "नमस्कार! कशासाठी मदत करू शकतो?", "references": []})

                # Default fallback for any greeting
                return JsonResponse({"answer": "How can I assist you today?", "references": []})


            # -------------------------------
            # 2️⃣ FOLDER DETECTION
            # -------------------------------
            detected_folder = detect_folder_by_keywords(query)

            # -------------------------------
            # 2A. FOLDER FOUND → SEARCH IT
            # -------------------------------
            if detected_folder:
                answer, refs = search_pdfs_fast(detected_folder, query, top_n_pdfs=2)

                if answer.strip():
                    return JsonResponse({"answer": answer, "references": refs})

                # Folder found but no context in PDFs → fallback to Acts below

            # -----------------------------------
            # 3️⃣ NO FOLDER FOUND → SEARCH IN "ACTS"
            # -----------------------------------
            acts_folder = Folder.objects.filter(name__icontains="act").first()

            if not acts_folder:
                return JsonResponse({
                    "answer": "क्षमस्व — कोणत्याही category मध्ये माहिती आढळली नाही.",
                    "references": []
                })

            answer, refs = search_pdfs_fast(acts_folder, query, top_n_pdfs=3)

            if answer.strip():
                return JsonResponse({"answer": answer, "references": refs})

            # -----------------------------------
            # 3A. ACT FOLDER ALSO HAS NO ANSWER
            # -----------------------------------
            return JsonResponse({
                "answer": "क्षमस्व, उपलब्ध दस्तऐवजांमध्ये संबंधित माहिती सापडली नाही.",
                "references": []
            })

        except Exception as e:
            print("Error:", e)
            return JsonResponse({
                "answer": "⚠️ काहीतरी चूक झाली. कृपया पुन्हा प्रयत्न करा.",
                "references": []
            })

# -------------- New logic with semanitc folder search --------------
def search_query_New(request):
    if request.method == "GET":
        welcome_message = (
            "🙏 नमस्कार — मी तुमचा AI सहाय्यक आहे. प्रश्न विचारा; मी आधी अपलोड केलेल्या दस्तऐवजांचा उपयोग करून उत्तर देईन."
        )
        return render(request, "search.html", {"welcome_message": welcome_message})

    if request.method == "POST":
        try:
            query = request.POST.get("query", "").strip()

            if not query:
                return JsonResponse({
                    "answer": "कृपया आपला प्रश्न विचारा 🙏",
                    "references": []
                })

            # -------------------------------
            # 1️⃣ GENERAL GREETINGS / SMALL TALKS
            # -------------------------------
            if is_general_query(query):
                return JsonResponse({
                    "answer": "How can I assist you today?",
                    "references": []
                })

            # -------------------------------
            # 2️⃣ SEMANTIC FOLDER SEARCH (NEW)
            # -------------------------------
            results = semantic_folder_search(query, top_n=3)

            if not results:
                return JsonResponse({
                    "answer": "क्षमस्व — कोणत्याही category मध्ये माहिती आढळली नाही.",
                    "references": []
                })

            # best result:
            best_folder, best_score, best_answer, best_refs = results[0]

            if best_answer.strip():
                return JsonResponse({
                    "answer": best_answer,
                    "references": best_refs
                })

            # -------------------------------
            # 3️⃣ FALLBACK — SEARCH IN 'ACT' FOLDER
            # -------------------------------
            acts_folder = Folder.objects.filter(name__icontains="act").first()

            if acts_folder:
                answer, refs = search_pdfs_fast(acts_folder, query, top_n_pdfs=3)
                if answer.strip():
                    return JsonResponse({"answer": answer, "references": refs})

            # -------------------------------
            # 4️⃣ FINAL FALLBACK  
            # -------------------------------
            return JsonResponse({
                "answer": "क्षमस्व, उपलब्ध दस्तऐवजांमध्ये संबंधित माहिती सापडली नाही.",
                "references": []
            })

        except Exception as e:
            print("Error:", e)
            return JsonResponse({
                "answer": "⚠️ काहीतरी चूक झाली. कृपया पुन्हा प्रयत्न करा.",
                "references": []
            })
