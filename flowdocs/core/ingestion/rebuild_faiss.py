# rebuild_faiss.py

import django
django.setup()

from core.models import PDFFile
from core.ingestion.embedder import build_faiss_for_pdf
from pathlib import Path
import shutil

FAISS_DIR = Path("data/faiss")


def rebuild_all_faiss():
    print("\n🔥 FAISS FULL REBUILD STARTED")

    if FAISS_DIR.exists():
        shutil.rmtree(FAISS_DIR)
        print("🧹 Old FAISS indexes removed")

    FAISS_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = PDFFile.objects.select_related("folder").all()
    print(f"📚 PDFs found: {pdfs.count()}")

    for pdf in pdfs:
        build_faiss_for_pdf(pdf)

    print("\n✅ FAISS REBUILD COMPLETED")


if __name__ == "__main__":
    rebuild_all_faiss()