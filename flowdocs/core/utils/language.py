# utils/language.py

import unicodedata
import re

# Zero width characters common in PDFs
ZERO_WIDTH_CHARS = [
    "\u200b",  # zero width space
    "\u200c",  # zero width non-joiner
    "\u200d",  # zero width joiner
    "\ufeff",  # byte order mark
]

DEVANAGARI_FIXES = {
    "ष्ण": "ष्ण",  # placeholder for future fixes
}

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