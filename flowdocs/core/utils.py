# utils.py
import os
import re
import fitz  # PyMuPDF
import numpy as np
from openai import OpenAI
import uuid
import hashlib
from langdetect import detect, DetectorFactory, LangDetectException
from django.conf import settings
from .models import Folder, PDFFile
from .vectorstore import pdf_collection_large,pdf_collection_small, embedding_large, embedding_small
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
def split_text_into_chunks(text, chunk_size=500, overlap=50):
    """
    Split text into overlapping word-based chunks.
    - chunk_size: max words in each chunk
    - overlap: repeated words between chunks for better context
    """
    words = text.replace("\n", " ").split()
    chunks = []
    start = 0
    total_words = len(words)

    while start < total_words:
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)

        # Move forward with overlap for context continuity
        start = end - overlap  

        if start < 0:
            start = 0

    return chunks



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
    try:
        pdf_path = pdf.file.path
        file_name = pdf.file.name
        folder_name = pdf.folder.name
       # file_id = pdf.id

    #print(f"[✅] File id : {file_name} ({file_id} id).")
        # 🔍 Checksum to avoid duplicates
        with open(pdf_path, "rb") as f:
            pdf_hash = hashlib.md5(f.read()).hexdigest()

        existing = pdf_collection_large.get(where={"file_name": file_name})
        if existing and len(existing["ids"]) > 0:
            existing_hash = existing["metadatas"][0].get("hash")
            if existing_hash == pdf_hash:
                print(f"⚠️ Skipping {file_name} — already indexed.")
                return

        # ✅ Extract text
        doc = fitz.open(pdf_path)
        full_text = "\n".join([page.get_text("text") for page in doc])
        doc.close()

        # ✅ Chunk text
        chunks = split_text_into_chunks(full_text, chunk_size=500,overlap=100)  # ← your function

        print("[✅] Split into chunks.")

        # ✅ Metadata per chunk
        metadatas = [{
          #  "file_id": file_id,
            "file_name": file_name,
            "folder": folder_name,
            "hash": pdf_hash,
        } for _ in chunks]

        # ✅ Add to Chroma (NO embedding_function here)
        pdf_collection_large.add(
            documents=chunks,
            metadatas=metadatas,
            ids=[str(uuid.uuid4()) for _ in chunks]
        )

        print(f"[✅] Indexed {file_name} ({len(chunks)} chunks).")

    except Exception as e:
        print(f"[❌] Failed to index {pdf.file.name} in Chroma: {e}")

# ---------------- Chroma Search ----------------
from django.core.cache import cache

def truncate_context(text, max_words=20000):
    words = text.split()
    return " ".join(words[:max_words])

def search_pdfs(user_query: str = ""):
    """Search PDFs directly from ChromaDB (no folder logic)."""
    try:
        # ✅ Cache key (safe for Marathi text)
        query_hash = hashlib.md5(
            user_query.strip().lower().encode("utf-8")
        ).hexdigest()

        cache_key = f"chroma:global:{query_hash}"

        # ✅ Return from cache if present
        cached = cache.get(cache_key)
        if cached:
            print("[⚡ Cache Hit: Redis]")
            return cached["answer"], cached["refs"]

        print("[Chroma 🔍] Global search (no folder filter)…")

        # ✅ Pure global vector search in Chroma
        results = pdf_collection_large.query(
            query_texts=[user_query],
            n_results=10
        )

        if not results or not results.get("documents") or not results["documents"][0]:
            print("[⚠️] No documents found in Chroma.")
            return "", []

        docs = results["documents"][0]
        metas = results["metadatas"][0]

        unique_refs = []
        context_parts = []
        seen_files = set()

        for doc, meta in zip(docs, metas):
            file_name = meta.get("file_name")

            # Skip invalid or repeated files
            if not file_name or file_name in seen_files:
                continue

            seen_files.add(file_name)
            pdf_url = f"{settings.MEDIA_URL}{file_name}"

            # ✅ Prepare reference
            unique_refs.append({
                "title": os.path.basename(file_name),
                "url": pdf_url,
                "score": 10,
            })

            context_parts.append(doc)

            # Limit number of references
            if len(unique_refs) >= 5:
                break

        # ✅ Combine text for GPT
        context_text = truncate_context("\n\n".join(context_parts), max_words=22000)

        # ✅ Generate final answer from GPT
        answer = generate_gpt4_answer(
            user_question=user_query,
            context=context_text,
            references=unique_refs,
            max_words=1000,
        )

        # ✅ Save to cache (24 hours)
        cache.set(cache_key, {"answer": answer, "refs": unique_refs}, timeout=86400)

        return answer, unique_refs

    except Exception as e:
        print(f"[❌] Chroma Search Failed: {e}")
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
        system_msg = (
            "तू एक अत्यंत शिस्तबद्ध सहाय्यक आहेस. "
            "उत्तर खालील कठोर फॉरमॅटमध्येच द्यावे:"
            "\n\n"
            "STRICT RULES:\n"
            "1) उत्तर क्रमांकित यादीमध्ये द्या (1., 2., 3., ...).\n"
            "2) प्रत्येक क्रमांक **नवीन ओळीत** सुरू केला पाहिजे.\n"
            "3) प्रत्येक बिंदूचा शीर्षक **ठळक** (bold) असावा.\n"
            "4) प्रत्येक बिंदू लहान, स्पष्ट परिच्छेदात असावा.\n"
            "5) markdown formatting वापरावे.\n"
            "6) कोणतेही मुद्दे एका ओळीत एकत्र टाकू नका.\n"
        )

        user_prompt = (
            f"खालील संदर्भ वापरून प्रश्नाचे नीटसंरचित उत्तर लिहा:\n\n"
            f"प्रश्न:\n{user_question}\n\n"
            f"संदर्भ:\n{context}"
        )

    else:
        system_msg = (
            "You must answer in STRICT markdown:\n"
            "1. Each numbered point must start on a **new line**.\n"
            "2. Each point must have a **bold title**.\n"
            "3. Use short paragraphs.\n"
            "4. Never merge numbered points in one line.\n"
        )

        user_prompt = (
            f"Use this context to answer:\n\n"
            f"Question:\n{user_question}\n\n"
            f"Context:\n{context}"
        )

    if references:
        src = "\n".join([f"- {r.get('title')}" for r in references])
        user_prompt += f"\n\nSources:\n{src}"

    response = client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_prompt},
        ],
    )

    answer = response.choices[0].message.content.strip()

    # ✅ DO NOT REMOVE NEWLINES (critical)
    return answer
def generate_gpt4_answerOLD(user_question: str, context: str, references: list = None, max_words=500) -> str:
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
