# rebuild_faiss.py

import django
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
django.setup()

from core.models import PDFFile
from core.ingestion.pdf_loader import extract_and_store_pdf_text
from core.ingestion.embedder import build_faiss_for_pdf

def rebuild_all_faiss():
    print("\n♻️ FULL FAISS REBUILD STARTED")

    pdfs = PDFFile.objects.all()
    print(f"📚 PDFs found: {pdfs.count()}")

    for pdf in pdfs:
        try:
            if not pdf.text_content:
                print(f"📄 Extracting text for PDF ID={pdf.id}")
                extract_and_store_pdf_text(pdf, pdf.file.path)

            build_faiss_for_pdf(pdf)

        except Exception as e:
            print(f"❌ Failed PDF ID={pdf.id} | {e}")

    print("✅ FULL FAISS REBUILD COMPLETE")


if __name__ == "__main__":
    rebuild_all_faiss()
