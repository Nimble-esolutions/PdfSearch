import os
import re
import fitz  # PyMuPDF
import numpy as np
from openai import OpenAI
from langdetect import detect, DetectorFactory, LangDetectException
from django.conf import settings
from indic_transliteration import sanscript as sc
from indic_transliteration.sanscript import transliterate
from .models import Folder, PDFFile

# Optional: FAISS for semantic search
try:
    import faiss
    _HAS_FAISS = True
    print("[DEBUG] FAISS imported successfully")
except ImportError:
    _HAS_FAISS = False
    print("[⚠️] FAISS not available. Semantic search disabled.")

# ---------------- Seed for consistent language detection ----------------
DetectorFactory.seed = 0

# ---------------- OpenAI Setup ----------------
client = None
OPENAI_API_KEY = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
    print(f"[🔑] OpenAI API Key loaded: Yes")
else:
    print("[❌] OpenAI API key not found")

# ---------------- PDF Text Extraction (No OCR) ----------------
def extract_text_from_pdf(file_path: str) -> str:
    """
    Extracts text from PDF using PyMuPDF only.
    OCR is disabled for faster processing.
    """
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
            else:
                print(f"[⚠️] Page {page_number} of {os.path.basename(file_path)} has no text")
        doc.close()
        print(f"[✅] Extracted text length from {os.path.basename(file_path)}: {len(text)}")
    except Exception as e:
        print(f"[❌] PyMuPDF extraction failed: {e}")

    return text.strip()

# ---------------- Transliteration ----------------
def transliterate_marathi_to_english(text: str) -> str:
    try:
        return transliterate(text, sc.DEVANAGARI, sc.ITRANS)
    except Exception as e:
        print(f"[⚠️] Transliteration failed: {e}")
        return text

# ---------------- Chunking ----------------
def chunk_text(text: str, chunk_size=1200, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap
    print(f"[DEBUG] Total chunks created: {len(chunks)}")
    return chunks

# ---------------- FAISS Embedding ----------------
def get_embeddings(texts: list, model="text-embedding-3-large"):
    if not client:
        raise RuntimeError("OpenAI API not configured")
    resp = client.embeddings.create(input=texts, model=model)
    embeddings = [np.array(d.embedding, dtype=np.float32) for d in resp.data]
    print(f"[DEBUG] Created embeddings for {len(texts)} chunks")
    return embeddings

# ---------------- Rule + Semantic Search ----------------
import re
import numpy as np
import faiss

# ---------------- Helper: Devanagari to ASCII ----------------
def devanagari_to_ascii(num_str):
    mapping = "०१२३४५६७८९"
    return "".join(str(mapping.index(ch)) if ch in mapping else ch for ch in num_str)

# ---------------- Main search function ----------------
def search_pdfs(folder, user_query: str):
    pdf_files = PDFFile.objects.filter(folder=folder)
    if not pdf_files:
        print(f"[⚠️] No PDFs found in folder: {folder.name}")
        return "", []  # <-- return empty context

    matched_contexts = []
    matched_files_metadata = []

    # ---------------- Rule detection ----------------
    rule_match = re.search(r"नियम\s*([०१२३४५६७८९0-9]+)", user_query)
    if rule_match:
        print(f"[DEBUG] Rule detected in query: {rule_match.group(0)}")

    # ---------------- Semantic embedding of query ----------------
    query_embedding = None
    if _HAS_FAISS and client:
        try:
            emb_resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=user_query
            )
            query_embedding = np.array(emb_resp.data[0].embedding, dtype=np.float32)
            print("[DEBUG] Query embedding created successfully")
        except Exception as e:
            print(f"[❌] Query embedding failed: {e}")

    for pdf in pdf_files:
        pdf_title = pdf.title
        pdf_path = pdf.file.path
        print(f"[DEBUG] Processing PDF: {pdf_title}")

        try:
            pdf_text = extract_text_from_pdf(pdf_path)
        except Exception as e:
            print(f"[❌] Failed to extract {pdf_title}: {e}")
            continue

        if not pdf_text.strip():
            print(f"[⚠️] PDF {pdf_title} has no text")
            continue

        snippets = []
        score = 0

        # ---------------- Rule-based search ----------------
        if rule_match:
            num = rule_match.group(1)
            regex = re.compile(rf"नियम\s*{num}")
            for m in regex.finditer(pdf_text):
                start, end = max(0, m.start() - 600), min(len(pdf_text), m.end() + 800)
                snippet = pdf_text[start:end]
                snippets.append(snippet)
                score += 3
                print(f"[DEBUG] Rule match found in {pdf_title}: {snippet[:80]}...")

        # ---------------- Semantic Search ----------------
        pdf_chunks = chunk_text(pdf_text)
        if _HAS_FAISS and query_embedding is not None and pdf_chunks:
            try:
                chunk_embeddings = get_embeddings(pdf_chunks, model="text-embedding-3-small")
                embeddings_matrix = np.vstack(chunk_embeddings)
                index = faiss.IndexFlatL2(embeddings_matrix.shape[1])
                index.add(embeddings_matrix)

                k = min(5, len(pdf_chunks))
                distances, indices = index.search(np.array([query_embedding]), k=k)

                for i, idx in enumerate(indices[0]):
                    if idx < 0 or idx >= len(pdf_chunks):
                        continue
                    matched_chunk = pdf_chunks[idx]
                    snippets.append(matched_chunk)
                    score += 2
                    print(f"[DEBUG] Semantic match {i+1} in {pdf_title}: {matched_chunk[:80]}...")
            except Exception as e:
                print(f"[❌] Semantic search failed for {pdf_title}: {e}")

        # ---------------- Add context & metadata ----------------
        if snippets and score > 0:
            matched_contexts.append(" ... ".join(snippets))
            matched_files_metadata.append({
                "title": pdf_title,
                "folder": getattr(pdf.folder, "name", None),
                "url": getattr(pdf.file, "url", None),
                "uploaded_at": pdf.uploaded_at.strftime("%Y-%m-%d") if getattr(pdf, "uploaded_at", None) else None,
                "score": score,
            })
            print(f"[DEBUG] Added context from {pdf_title}, score: {score}")

    # ---------------- Deduplicate PDFs ----------------
    unique_refs = {}
    for ref in matched_files_metadata:
        key = ref.get("title")
        if key not in unique_refs:
            unique_refs[key] = ref
        else:
            existing = unique_refs[key]
            # Prefer higher score or detected folder
            if ref.get("score", 0) > existing.get("score", 0):
                unique_refs[key] = ref

    matched_files_metadata = list(unique_refs.values())

    # ---------------- Select top N PDFs ----------------
    TOP_N = 3
    if matched_files_metadata:
        matched_files_metadata = sorted(
            matched_files_metadata, key=lambda x: x.get("score", 0), reverse=True
        )
        top_refs = matched_files_metadata[:TOP_N]
        combined_context = "\n\n".join(matched_contexts)
        combined_context = truncate_context(combined_context, max_words=22500)
        print(f"[DEBUG] Total combined context length: {len(combined_context)}")
        answer = generate_gpt4_answer(
            user_question=user_query,
            context=combined_context,
            references=top_refs,
            max_words=400
        )
        print("[DEBUG] GPT answer generated")
        return answer, top_refs
    else:
        print(f"[DEBUG] No matching content found in folder: {folder.name}")
        return "", []  # <-- crucial: return empty if no match




# ---------------- Language Detection ----------------
DetectorFactory.seed = 0
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

# ---------------- Truncate Context ----------------
def truncate_context(text: str, max_words=22500) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text

# ---------------- GPT Answer ----------------
def generate_gpt4_answer(user_question: str, context: str, references: list = None, max_words=400) -> str:
    if not client:
        return "OpenAI API key not configured."
    context = truncate_context(context, max_words=22500)
    lang = detect_language(user_question)

    if lang == "mr":
        system_msg = "तू एक मदत करणारा सहाय्यक आहेस. उत्तर फक्त मराठीत द्या, 400 शब्दांमध्ये."
        prompt = f"खालील संदर्भांचा वापर करून उत्तर द्या:\n\nप्रश्न: {user_question}\n\nसंदर्भ:\n{context}"
    else:
        system_msg = "You are a helpful assistant. Answer in English only, max 400 words."
        prompt = f"Using the following context, answer:\n\nQ: {user_question}\n\nContext:\n{context}"

    if references:
        ref_list = "\n".join([f"- {r.get('title')} ({r.get('url')})" for r in references])
        prompt += f"\n\nSources:\n{ref_list}"

    resp = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
    )

    ans = resp.choices[0].message.content.strip()
    words = ans.split()
    if len(words) > max_words:
        ans = " ".join(words[:max_words]) + "..."
    return ans

# ---------------- Detect Folder by Keywords ----------------
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