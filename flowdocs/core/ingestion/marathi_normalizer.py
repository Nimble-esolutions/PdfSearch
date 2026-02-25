import unicodedata
import re

# Ordered fixes – DO NOT reorder
OCR_FIXES = [
    ("गृहननमाषण", "गृहनिर्माण"),
    ("वािुशास्त्रज्ञ", "वास्तुशास्त्रज्ञ"),
    ("अनधननयम", "अधिनियम"),
    ("किवा", "किंवा"),
    ("संसे्थ", "संस्था"),
]

DEVANAGARI_RANGE = re.compile(r"[\u0900-\u097F]+")

def normalize_marathi(text: str) -> str:
    if not text:
        return text

    # 1️⃣ Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # 2️⃣ Remove invisible junk
    text = text.replace("\u200c", "").replace("\u200d", "")

    # 3️⃣ OCR word fixes (exact replacements)
    for wrong, correct in OCR_FIXES:
        text = text.replace(wrong, correct)

    # 4️⃣ Fix broken matras
    text = re.sub(r"([क-ह])\s+([ािीुूेैोौंः])", r"\1\2", text)

    return text
    
    
    
# Where to use this (VERY IMPORTANT)
# ✔ PDF ingestion ONLY
# raw_text = extract_text_from_pdf_path(path)
# clean_text = normalize_marathi(raw_text)

# pdf.text_content = clean_text
# pdf.save()