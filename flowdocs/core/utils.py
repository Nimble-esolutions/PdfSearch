# utils.py
import os
import re
import fitz  # PyMuPDF
import numpy as np
from openai import OpenAI
import uuid
from langdetect import detect, DetectorFactory, LangDetectException
from django.conf import settings
from .models import Folder, PDFFile
from .vectorstore import pdf_collection  # 🔹 NEW: use Chroma collection
from indic_transliteration import sanscript as sc
from indic_transliteration.sanscript import transliterate
from django.urls import reverse
#from .text_utils import split_text_into_chunks  # adjust if you have a helper function
#from langchain.text_splitter import RecursiveCharacterTextSplitter

DetectorFactory.seed = 0

# ---------------- OpenAI Setup ----------------
client = None
OPENAI_API_KEY = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
    print(f"[🔑] OpenAI API Key loaded: Yes")
else:
    print("[❌] OpenAI API key not found")

# ---------------- PDF Text Extraction ----------------
def extract_text_from_pdf(file_path: str) -> str:
    if not os.path.exists(file_path):
        print(f"[❌] File not found: {file_path}")
        return ""

    text = ""
    try:
        doc = fitz.open(file_path)
        for page_number, page in enumerate(doc, start=1):
            page_text = page.get_text("text")
            if page_text.strip():
                text += page_text + "\n"
        doc.close()
        print(f"[✅] Extracted text from {os.path.basename(file_path)} (len={len(text)})")
    except Exception as e:
        print(f"[❌] PDF extraction failed: {e}")

    return text.strip()

# ---------------- Transliteration ----------------
def transliterate_marathi_to_english(text: str) -> str:
    try:
        return transliterate(text, sc.DEVANAGARI, sc.ITRANS)
    except Exception as e:
        print(f"[⚠️] Transliteration failed: {e}")
        return text


# ---------------- Chunking ----------------
def split_text_into_chunks(text, max_words=500):
    """
    Splits text into chunks by word count instead of character length.
    """
    words = text.replace("\n", " ").split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def chunk_text(text: str, chunk_size=800, overlap=100):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap
    print(f"[DEBUG] Created {len(chunks)} chunks")
    return chunks

# ---------------- Index to Chroma ----------------
def index_pdf_to_chroma(pdf):
    """Extract text from PDF and add to Chroma with proper metadata."""
    try:
        # 1️⃣ Extract text
        pdf_path = pdf.file.path
        doc = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text("text") + "\n"
        doc.close()

        # 2️⃣ Split text into smaller chunks (important for embeddings)
        chunks = split_text_into_chunks(full_text, max_words=500)  # use your own chunking method

        # 3️⃣ Prepare Chroma data
        metadatas = [{
            "file_id": pdf.id,
            "file_name": pdf.file.name,
            "folder": pdf.folder.name,
        } for _ in chunks]

        # 4️⃣ Add to Chroma
        pdf_collection.add(
            documents=chunks,            # ✅ list of text chunks
            metadatas=metadatas,         # ✅ parallel metadata list
            ids=[str(uuid.uuid4()) for _ in chunks],
        )

        print(f"[✅] Indexed {pdf.file.name} with {len(chunks)} chunks.")

    except Exception as e:
        print(f"[❌] Failed to index {pdf.file.name} in Chroma: {e}")

# ---------------- Chroma Search ----------------
def search_pdfs(folder=None, user_query: str = ""):
    """Search PDFs using ChromaDB. If folder=None, searches across all."""
    try:
        from django.conf import settings
        import os

        folder_name = folder.name if folder else None
        print(f"[Chroma 🔍] Searching in folder: {folder_name or 'ALL'}")

        # Build query for Chroma
        where_clause = {"folder": folder_name} if folder_name else None
        results = pdf_collection.query(
            query_texts=[user_query],
            n_results=10,
            where=where_clause,
        )

        if not results or not results.get("documents") or not results["documents"][0]:
            print("[⚠️] No relevant chunks found in Chroma.")
            return "", []

        contexts = results["documents"][0]
        metadatas = results["metadatas"][0]

        seen_files = set()
        unique_refs = []
        combined_context_parts = []

        for ctx, meta in zip(contexts, metadatas):
            file_name = meta.get("file_name")  # e.g. pdfs/MCS_Rules_1961.pdf
            folder_meta = meta.get("folder")

            if not file_name:
                continue

            if file_name not in seen_files:
                seen_files.add(file_name)

                # ✅ Build full media URL
                pdf_url = f"{settings.MEDIA_URL}{file_name}"

                unique_refs.append({
                    "title": os.path.basename(file_name),
                    "folder": folder_meta,
                    "url": pdf_url,
                    "uploaded_at": None,
                    "score": 10,
                })

            combined_context_parts.append(ctx)

            # Limit to 5 unique PDFs
            if len(unique_refs) >= 5:
                break

        # ✅ Combine and truncate context for GPT
        combined_context = "\n\n".join(combined_context_parts)
        combined_context = truncate_context(combined_context, max_words=22500)

        # ✅ Generate GPT answer
        answer = generate_gpt4_answer(
            user_question=user_query,
            context=combined_context,
            references=unique_refs,
            max_words=500,
        )

        print(f"[✅] Found {len(unique_refs)} unique reference document(s).")
        return answer, unique_refs

    except Exception as e:
        print(f"[❌] Chroma search failed: {e}")
        return "", []
# ---------------- Helper Functions ----------------
def detect_language(text: str) -> str:
    if not text.strip():
        return "en"
    devanagari_chars = re.findall(r'[\u0900-\u097F]', text)
    if len(devanagari_chars) / max(len(text), 1) > 0.2:
        return "mr"
    try:
        lang = detect(text)
        return lang if lang in ["en", "mr"] else "en"
    except LangDetectException:
        return "en"

def truncate_context(text: str, max_words=22500) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text

def generate_gpt4_answer(user_question: str, context: str, references: list = None, max_words=500) -> str:
    if not client:
        return "OpenAI API key not configured."
    context = truncate_context(context)
    lang = detect_language(user_question)

    if lang == "mr":
        system_msg = "तू एक मदत करणारा सहाय्यक आहेस. उत्तर फक्त मराठीत द्या, 500 शब्दांमध्ये."
        prompt = f"खालील संदर्भांचा वापर करून उत्तर द्या:\n\nप्रश्न: {user_question}\n\nसंदर्भ:\n{context}"
    else:
        system_msg = "You are a helpful assistant. Answer in English only, max 500 words."
        prompt = f"Using the following context, answer:\n\nQ: {user_question}\n\nContext:\n{context}"

    if references:
        ref_list = "\n".join([f"- {r.get('title')}" for r in references])
        prompt += f"\n\nSources:\n{ref_list}"

    resp = client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
    )

    ans = resp.choices[0].message.content.strip()
    return " ".join(ans.split()[:max_words])

def detect_folder_by_keywords(query):
    folders = Folder.objects.all()
    query_words = query.lower().split()
    folder_scores = []

    for folder in folders:
        if not hasattr(folder, 'keywords') or not folder.keywords:
            continue

        folder_keywords = (
            [k.strip().lower() for k in folder.keywords]
            if isinstance(folder.keywords, list)
            else [k.strip().lower() for k in folder.keywords.split(",")]
        )

        # count how many query words appear in folder keywords
        score = sum(1 for qw in query_words if qw in folder_keywords)

        if score > 0:
            folder_scores.append((folder, score))

    if not folder_scores:
        return None

    # sort folders by highest match score
    folder_scores.sort(key=lambda x: x[1], reverse=True)
    top_folder = folder_scores[0][0]
    return top_folder
