# core/views.py

import os
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import FileSystemStorage

from .models import PDFFile
from core.ingestion.pdf_loader import extract_and_store_pdf_text
from core.ingestion.embedder import build_faiss_for_pdf
from core.answering.housing_answer import generate_housing_answer
from core.answering.generic_answer import generate_generic_answer

def ask_page(request):
    return render(request, "ask.html")


def admin_dashboard(request):
    pdfs = PDFFile.objects.all().order_by("-uploaded_at")
    return render(request, "admin_dashboard.html", {"pdfs": pdfs})


@csrf_exempt
def upload_pdf_view(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    print("🚀 upload_pdf_view HIT")

    pdf_file = request.FILES.get("pdf")
    subject = request.POST.get("subject")
    category = request.POST.get("category")

    upload_dir = os.path.join("data", "pdfs", subject, category)
    os.makedirs(upload_dir, exist_ok=True)

    fs = FileSystemStorage(location=upload_dir)
    filename = fs.save(pdf_file.name, pdf_file)

    relative_path = os.path.join(subject, category, filename)

    pdf = PDFFile.objects.create(
        title=filename,
        file=os.path.join("pdfs", relative_path),
        uploaded_by=request.user if request.user.is_authenticated else None,
    )

    # 🔑 CRITICAL STEPS
    extract_and_store_pdf_text(pdf, os.path.join(upload_dir, filename))
    build_faiss_for_pdf(pdf)

    return JsonResponse({
        "status": "success",
        "pdf_id": pdf.id
    })


@csrf_exempt
def ask_question_view(request):
    data = json.loads(request.body)
    question = data.get("question", "").strip()
    subject = data.get("subject", "generic")

    if not question:
        return JsonResponse({"error": "Empty question"}, status=400)

    if subject == "housing":
        answer = generate_housing_answer(question)
    else:
        answer = generate_generic_answer(question)

    return JsonResponse({"answer": answer})

def rebuild_faiss_view(request):
    # Example placeholder
    return HttpResponse("FAISS index rebuild triggered!")
