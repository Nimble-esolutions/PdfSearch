# core/ingestion/pdf_loader.py
import fitz  # PyMuPDF
from core.utils.language import normalize_marathi
def extract_text_from_pdf_path(path: str) -> str:
    """
    Low-level PDF text extraction (RAW).
    No normalization here.
    """
    doc = fitz.open(path)
    text = ""

    for page in doc:
        text += page.get_text()

    return text


def extract_and_store_pdf_text(pdf, path: str):
    """
    High-level ingestion:
    PDF → raw text → normalized text → DB
    """
    print(f"\n📄 Extracting PDF: {path}")

    raw_text = extract_text_from_pdf_path(path)

    if not raw_text.strip():
        print("⚠️ Empty text extracted from PDF")
        return

    # 🔑 Normalize exactly ONCE
    clean_text = normalize_marathi(raw_text)

    pdf.text_content = clean_text
    pdf.save(update_fields=["text_content"])

    print("✅ PDF text extracted & normalized")
