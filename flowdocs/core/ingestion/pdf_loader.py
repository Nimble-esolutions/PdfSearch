# core/ingestion/pdf_loader.py

import fitz  # PyMuPDF

def normalize_marathi(text: str) -> str:
    """
    Deterministic Marathi Unicode normalization.
    SAFE for legal text.
    """
    if not text:
        return ""

    # 1️⃣ Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # 2️⃣ Remove zero-width characters
    for ch in ZERO_WIDTH_CHARS:
        text = text.replace(ch, "")

    # 3️⃣ Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 4️⃣ Apply known Marathi fixes
    for wrong, correct in DEVANAGARI_FIXES.items():
        text = text.replace(wrong, correct)

    return text.strip()


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
