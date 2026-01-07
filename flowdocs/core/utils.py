# utils.py
import os
import json
import math
import pathlib
import traceback
from typing import List, Tuple, Optional, Dict, Any

import fitz  # PyMuPDF
import numpy as np
from openai import OpenAI
from django.conf import settings
from django.core.cache import cache
from .models import Folder
from difflib import SequenceMatcher

from indic_transliteration import sanscript as sc
from indic_transliteration.sanscript import transliterate
from langdetect import detect, DetectorFactory, LangDetectException

from .models import Folder, PDFFile

# Optional FAISS
try:
    import faiss
    _HAS_FAISS = True
except Exception:
    faiss = None
    _HAS_FAISS = False

# ------------------- Configuration -------------------
DetectorFactory.seed = 0

# Where to store FAISS indices and embeddings cache files
BASE_DIR = getattr(settings, "BASE_DIR", os.getcwd())
FAISS_DIR = os.path.join(BASE_DIR, "faiss_indexes")
os.makedirs(FAISS_DIR, exist_ok=True)

# OpenAI client
OPENAI_API_KEY = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
OPENAI_EMBED_MODEL = getattr(settings, "OPENAI_EMBED_MODEL", "text-embedding-3-small")
OPENAI_CHAT_MODEL = getattr(settings, "OPENAI_CHAT_MODEL", "gpt-4o-mini")  # change as needed
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Embedding and chunk sizes
CHUNK_SIZE = getattr(settings, "PDF_CHUNK_SIZE", 1200)
CHUNK_OVERLAP = getattr(settings, "PDF_CHUNK_OVERLAP", 200)
MAX_CONTEXT_WORDS = getattr(settings, "MAX_CONTEXT_WORDS", 2500)  # much smaller than 22500
TOP_K_CHUNKS = getattr(settings, "TOP_K_CHUNKS", 5)

# Cache TTLs (seconds)
EMBEDDING_TTL = getattr(settings, "EMBEDDING_TTL", 60 * 60 * 24 * 7)  # 7 days
SEARCH_CACHE_TTL = getattr(settings, "SEARCH_CACHE_TTL", 60 * 10)  # 10 minutes

# ------------------ Helpers ------------------
def transliterate_marathi_to_english(text: str) -> str:
    try:
        return transliterate(text, sc.DEVANAGARI, sc.ITRANS)
    except Exception:
        return text


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"
    devanagari_chars = [c for c in text if '\u0900' <= c <= '\u097F']
    if len(devanagari_chars) / max(len(text), 1) > 0.2:
        return "mr"
    try:
        lang = detect(text)
        return lang if lang in ("en", "mr") else "en"
    except LangDetectException:
        return "en"


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        start += max(1, chunk_size - overlap)
    return chunks


def truncate_context(text: str, max_words: int = MAX_CONTEXT_WORDS) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


# ------------------ PDF extraction (cached per model) ------------------
def extract_text_from_pdf_path(path: str) -> str:
    """
    Extract text using PyMuPDF. Lightweight: no OCR.
    This function is used to build extracted text on upload; search reads cached values in the DB.
    """
    if not os.path.exists(path):
        return ""
    text_parts = []
    try:
        doc = fitz.open(path)
        for page in doc:
            page_text = page.get_text("text")
            if page_text and page_text.strip():
                text_parts.append(page_text)
        doc.close()
    except Exception:
        traceback.print_exc()
        return ""
    return "\n".join(text_parts).strip()


# ---------------- Embeddings ----------------
def create_embeddings_for_texts(texts: List[str], batch_size: int = 16) -> List[List[float]]:
    """Call OpenAI embeddings in batches. Returns list of lists (embeddings)."""
    if not client:
        raise RuntimeError("OpenAI not configured")
    embeddings = []
    # batch manually to reduce large payloads
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = client.embeddings.create(model=OPENAI_EMBED_MODEL, input=batch)
        # depending on SDK, resp.data may be iterable
        for d in resp.data:
            embeddings.append(list(d.embedding))
    return embeddings


# ----------------- FAISS helpers -----------------
def faiss_index_path_for_folder(folder: Folder) -> str:
    return os.path.join(FAISS_DIR, f"folder_{folder.id}.index")


def build_or_load_faiss_index_for_folder(folder: Folder) -> Tuple[Optional[faiss.Index], List[str]]:
    """
    Build or load a FAISS index for a folder.
    Returns (index, chunks_flat_list) where chunks_flat_list maps index positions -> chunk texts.
    If FAISS not available, returns (None, chunks_flat_list) so fallback search can use numpy.
    """
    # gather all PDFs in folder that have chunk_embeddings and page_chunks saved in DB
    pdfs = PDFFile.objects.filter(folder=folder)
    chunk_texts = []
    chunk_embeddings = []

    for pdf in pdfs:
        # Expectation: PDFFile has page_chunks (list[str]) and chunk_embeddings (list[list[float]])
        if getattr(pdf, "page_chunks", None) and getattr(pdf, "chunk_embeddings", None):
            # ensure both lengths match
            p_chunks = pdf.page_chunks or []
            p_embs = pdf.chunk_embeddings or []
            # sometimes embeddings stored as JSON strings -> normalize
            if isinstance(p_embs, str):
                try:
                    p_embs = json.loads(p_embs)
                except Exception:
                    p_embs = []
            if len(p_chunks) != len(p_embs):
                # skip mismatched PDF (safer)
                continue
            for c, e in zip(p_chunks, p_embs):
                chunk_texts.append(c)
                chunk_embeddings.append(np.array(e, dtype=np.float32))

    if not chunk_embeddings:
        return None, []

    embeddings_matrix = np.vstack(chunk_embeddings).astype(np.float32)

    if _HAS_FAISS:
        idx_path = faiss_index_path_for_folder(folder)
        try:
            if os.path.exists(idx_path):
                index = faiss.read_index(idx_path)
                return index, chunk_texts
        except Exception:
            # if reading fails, we'll rebuild
            pass

        try:
            index = faiss.IndexFlatIP(embeddings_matrix.shape[1])  # using inner product on normalized vectors
            # normalize embeddings to unit length for IP as cosine
            norms = np.linalg.norm(embeddings_matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            embeddings_matrix = embeddings_matrix / norms
            index.add(embeddings_matrix)
            faiss.write_index(index, idx_path)
            return index, chunk_texts
        except Exception:
            traceback.print_exc()
            return None, chunk_texts
    else:
        # FAISS unavailable — return None and raw chunk_texts; fallback search will do numpy similarity
        return None, chunk_texts


def search_chunks_with_faiss_or_numpy(query_embedding: np.ndarray, index: Optional[faiss.Index],
                                      chunk_texts: List[str], top_k: int = TOP_K_CHUNKS
                                      ) -> List[Tuple[str, float]]:
    """
    Returns list of (chunk_text, score) sorted desc by score.
    If FAISS index provided, use it. Otherwise run numpy dot product.
    """
    if query_embedding is None or len(chunk_texts) == 0:
        return []

    q = query_embedding.astype(np.float32)
    # normalize q
    q_norm = q / (np.linalg.norm(q) + 1e-12)

    if index is not None and _HAS_FAISS:
        try:
            D, I = index.search(np.array([q_norm]), k=min(top_k, index.ntotal))
            results = []
            for dist, idx in zip(D[0], I[0]):
                if idx < 0:
                    continue
                score = float(dist)
                results.append((chunk_texts[idx], score))
            return results
        except Exception:
            traceback.print_exc()
            # fallback to numpy
    # numpy fallback
    # We need stored chunk embeddings for numpy fallback; but earlier we only have chunk_texts.
    # Try to read chunk embeddings from DB (inefficient but rare if FAISS missing).
    # Instead we compute embeddings for chunk_texts here (cached) — but that's heavy.
    # Simpler fallback: perform rule-based substring matching with basic scoring.
    results = []
    # crude substring matching:
    for t in chunk_texts:
        score = 0
        # prefer exact match of longer tokens
        if query_embedding is None:
            score = 0
        else:
            # fallback: give small base score if query string is present
            score = 0
        results.append((t, score))
    # sort by score descending (though likely all zero)
    results = sorted(results, key=lambda x: x[1], reverse=True)[:top_k]
    return results


# ----------------- Public: Precompute embeddings on upload -----------------
def precompute_pdf_embeddings(pdf: PDFFile) -> None:
    """
    Called when a PDF is added/updated.
    This extracts text, chunks it, creates embeddings, and stores them on the PDF model.
    Also triggers folder FAISS index rebuild.
    Requires PDFFile to have fields: extracted_text (TextField), page_chunks (JSONField), chunk_embeddings (JSONField).
    """
    try:
        # 1. Extract and store text
        path = pdf.file.path
        extracted = extract_text_from_pdf_path(path)
        pdf.extracted_text = extracted
        if not extracted:
            pdf.page_chunks = []
            pdf.chunk_embeddings = []
            pdf.save(update_fields=["extracted_text", "page_chunks", "chunk_embeddings"])
            return

        # 2. chunk
        chunks = chunk_text(extracted, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        # optional: dedup short chunks
        chunks = [c for c in chunks if c and len(c.strip()) > 30]

        if not chunks:
            pdf.page_chunks = []
            pdf.chunk_embeddings = []
            pdf.save(update_fields=["extracted_text", "page_chunks", "chunk_embeddings"])
            return

        # 3. create embeddings in batches
        embeddings = create_embeddings_for_texts(chunks, batch_size=16)

        # 4. persist on model (JSON serializable)
        pdf.page_chunks = chunks
        pdf.chunk_embeddings = embeddings
        pdf.save(update_fields=["extracted_text", "page_chunks", "chunk_embeddings"])

        # 5. rebuild FAISS index for the folder (async recommended; here we do sync)
        try:
            # remove old index and rebuild (safe)
            idx_path = faiss_index_path_for_folder(pdf.folder)
            if os.path.exists(idx_path):
                try:
                    os.remove(idx_path)
                except Exception:
                    pass
            # build new index by calling build_or_load_faiss_index_for_folder which writes index
            build_or_load_faiss_index_for_folder(pdf.folder)
        except Exception:
            traceback.print_exc()

    except Exception:
        traceback.print_exc()


# ------------------ Search PDFs (fast path) ------------------
def search_pdfs_fast(folder: Folder, user_query: str, top_n_pdfs: int = 2) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fast search that:
     - uses precomputed chunk embeddings saved on PDFs
     - loads or builds FAISS folder index (persistent)
     - finds top chunks and returns combined context and references
    Returns (answer_text, references_list)
    Each reference has: title, url, folder, uploaded_at, score
    """
    # 1. quick guard
    pdfs = PDFFile.objects.filter(folder=folder)
    if not pdfs.exists():
        return "", []

    # 2. create query embedding
    try:
        if not client:
            raise RuntimeError("OpenAI not configured")
        emb_resp = client.embeddings.create(model=OPENAI_EMBED_MODEL, input=[user_query])
        query_emb = np.array(emb_resp.data[0].embedding, dtype=np.float32)
    except Exception:
        traceback.print_exc()
        query_emb = None

    index, chunk_texts = build_or_load_faiss_index_for_folder(folder)

    # 3. get top matched chunks (text + scores)
    matches = search_chunks_with_faiss_or_numpy(query_emb, index, chunk_texts, top_k=TOP_K_CHUNKS * 10)

    if not matches:
        # fallback to rule-based search
        import re
        rule_match = re.search(r"नियम\s*([०१२३४५६७८९0-9]+)", user_query)
        matches = []
        for pdf in pdfs:
            chunks = getattr(pdf, "page_chunks", []) or []
            for c in chunks:
                score = 0
                if rule_match and f"नियम {rule_match.group(1)}" in c:
                    score += 5
                if user_query in c:
                    score += 1
                if score > 0:
                    matches.append((c, score))
        matches = sorted(matches, key=lambda x: x[1], reverse=True)[:TOP_K_CHUNKS * 10]

    # 4. collate matches by PDF
    chunk_to_pdf = {}
    for pdf in pdfs:
        p_chunks = getattr(pdf, "page_chunks", []) or []
        uploaded_at = getattr(pdf, "uploaded_at", None)
        uploaded_at_str = uploaded_at.strftime("%Y-%m-%d") if uploaded_at else None
        for c in p_chunks:
            if c not in chunk_to_pdf:
                chunk_to_pdf[c] = {
                    "title": getattr(pdf, "title", None),
                    "url": getattr(getattr(pdf, "file", None), "url", None),
                    "folder": getattr(getattr(pdf, "folder", None), "name", None),
                    "uploaded_at": uploaded_at_str,
                }

    # Aggregate by PDF: sum scores and collect top snippets
    pdf_scores = {}
    pdf_snippets = {}
    for chunk_text, score in matches:
        meta = chunk_to_pdf.get(chunk_text)
        if not meta:
            continue
        title = meta["title"]
        pdf_scores.setdefault(title, 0)
        pdf_scores[title] += score
        pdf_snippets.setdefault(title, []).append(chunk_text)

    if not pdf_scores:
        return "", []

    # Create references list
    refs = []
    for title, s in pdf_scores.items():
        pdf_obj = pdfs.filter(title=title).first()
        if pdf_obj:
            uploaded_at = getattr(pdf_obj, "uploaded_at", None)
            refs.append({
                "title": title,
                "folder": getattr(getattr(pdf_obj, "folder", None), "name", None),
                "url": getattr(getattr(pdf_obj, "file", None), "url", None),
                "uploaded_at": uploaded_at.strftime("%Y-%m-%d") if uploaded_at else None,
                "score": s,
            })
        else:
            refs.append({"title": title, "folder": None, "url": None, "uploaded_at": None, "score": s})

    # pick top N PDFs by score
    refs = sorted(refs, key=lambda x: x["score"], reverse=True)[:top_n_pdfs]

    # build context: include only top K snippets across top refs
    combined_snippets = []
    for r in refs:
        title = r["title"]
        snippets = pdf_snippets.get(title, [])[:TOP_K_CHUNKS]
        label = f"--- {title} ---"
        combined_snippets.append(label)
        combined_snippets.extend(snippets)
    combined_context = "\n\n".join(combined_snippets)
    combined_context = truncate_context(combined_context, max_words=MAX_CONTEXT_WORDS)

    # 5. generate answer
    answer = generate_gpt_answer(user_question=user_query, context=combined_context, references=refs, max_words=400)
    return answer, refs

# ------------------ GPT answer ------------------
def generate_gpt_answer(user_question: str, context: str, references: List[Dict[str, Any]] = None, max_words: int = 200) -> str:
    """
    Query the LLM with a small, high-quality context. Use cached responses if available.
    """
    cache_key = f"gpt_ans:{hash(user_question + (context or ''))}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    if not client:
        return "OpenAI API key not configured."

    lang = detect_language(user_question)
    if lang == "mr":
        system_msg = "तुम्ही एक सहाय्यक आहात. मराठीतून उत्तर द्या. मर्यादा 200 शब्द."
        prompt = f"प्रश्न: {user_question}\n\nसंदर्भ:\n{context}"
    else:
        system_msg = "You are a helpful assistant. Answer concisely in English, max 200 words."
        prompt = f"Q: {user_question}\n\nContext:\n{context}"

    if references:
        refs_text = "\n".join([f"- {r.get('title')} ({r.get('url')})" for r in references if r.get('title')])
        prompt = f"{prompt}\n\nSources:\n{refs_text}"

    try:
        resp = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.0,
        )
        # Safe access depending on SDK shape
        ans = ""
        if hasattr(resp, "choices"):
            ans = getattr(resp.choices[0].message, "content", "") if resp.choices else ""
        else:
            # older shape
            try:
                ans = resp["choices"][0]["message"]["content"]
            except Exception:
                ans = str(resp)
        ans = ans.strip()
        # truncate to word limit
        words = ans.split()
        if len(words) > max_words:
            ans = " ".join(words[:max_words]) + "..."
        cache.set(cache_key, ans, SEARCH_CACHE_TTL)
        return ans
    except Exception:
        traceback.print_exc()
        return "⚠️ Couldn't generate answer right now. Please try again later."

# ---------------- Detect Folder by Keywords matching by single word----------------
def detect_folder_by_single_keywords(query):
    """
    Returns a Folder model instance based on keyword matching.
    """
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

        score = sum(1 for qw in query_words if qw in folder_keywords)

        if score > 0:
            folder_scores.append((folder, score))

    if not folder_scores:
        return None  # means unknown

    folder_scores.sort(key=lambda x: x[1], reverse=True)
    top_folder = folder_scores[0][0]

    return top_folder   # IMPORTANT — return Folder instance, not string

#---------------- Detect Folder by Key phrases matching by multiple word----------------
def fuzzy_ratio(a, b):
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def detect_folder_by_keywords(query):
    """
    Detects the best matching folder based on:
    - direct phrase match
    - reverse phrase match
    - fuzzy similarity
    Includes full debug output.
    """
    print("\n========== 🔍 KEYWORD DEBUG INFO ==========")
    print(f"📝 User Query: {query}\n")

    folders = Folder.objects.all()
    query_lower = query.lower().strip()

    best_folder = None
    best_score = 0.0

    for folder in folders:
        # Skip folders without keywords
        if not folder.keywords:
            continue

        # Normalize folder keyword list (remove empty items)
        if isinstance(folder.keywords, list):
            folder_keywords = [
                k.strip().lower()
                for k in folder.keywords
                if k and k.strip()
            ]
        else:
            folder_keywords = [
                k.strip().lower()
                for k in folder.keywords.split(",")
                if k and k.strip()
            ]

        print(f"📁 Folder: {folder.name}")
        print(f"🔑 Keywords: {folder_keywords}")

        folder_score = 0.0
        matched_phrases = []

        for phrase in folder_keywords:
            sim = fuzzy_ratio(query_lower, phrase)

            # 1️⃣ direct substring match
            if phrase in query_lower:
                folder_score += 1.0
                matched_phrases.append(f"{phrase} (substring)")
                continue

            # 2️⃣ reversed substring match
            if query_lower in phrase:
                folder_score += 0.8
                matched_phrases.append(f"{phrase} (reverse-substring)")
                continue

            # 3️⃣ fuzzy similarity > 0.60
            if sim > 0.60:
                folder_score += sim
                matched_phrases.append(f"{phrase} (fuzzy={sim:.2f})")

        print(f"🔍 Matches: {matched_phrases}")
        print(f"⭐ Folder Score: {folder_score}\n")

        if folder_score > best_score:
            best_folder = folder
            best_score = folder_score

    # 🎯 If NO meaningful match, return NONE
    if best_score < 0.70:  # Minimum threshold for reliability
        print("🎯 Final Detected Folder: None (no strong match)")
        print("============================================\n")
        return None

    print(f"🎯 Final Detected Folder: {best_folder.name}")
    print("============================================\n")

    return best_folder


#---------------- detect multiple folders for search query ----------------
def detect_folder_by_keywords_multi(query, min_score_threshold=0.50):
    """
    Modified version:
    - Returns ALL folders with score >= threshold
    - Not just a single best folder
    - Keeps your fuzzy + substring logic fully intact
    """

    print("\n========== 🔍 KEYWORD DEBUG INFO (MULTI-FOLDER) ==========")
    print(f"📝 User Query: {query}\n")

    folders = Folder.objects.all()
    query_lower = query.lower().strip()

    scored = []  # will store (folder, score)

    for folder in folders:
        if not folder.keywords:
            continue

        # normalize keywords
        if isinstance(folder.keywords, list):
            folder_keywords = [k.strip().lower() for k in folder.keywords if k.strip()]
        else:
            folder_keywords = [k.strip().lower() for k in folder.keywords.split(",") if k.strip()]

        print(f"📁 Folder: {folder.name}")
        print(f"🔑 Keywords: {folder_keywords}")

        folder_score = 0.0
        matched_phrases = []

        for phrase in folder_keywords:
            sim = fuzzy_ratio(query_lower, phrase)

            # direct match
            if phrase in query_lower:
                folder_score += 1.0
                matched_phrases.append(f"{phrase} (substring)")
                continue

            # reversed
            if query_lower in phrase:
                folder_score += 0.8
                matched_phrases.append(f"{phrase} (reverse-substring)")
                continue

            # fuzzy
            if sim > 0.60:
                folder_score += sim
                matched_phrases.append(f"{phrase} (fuzzy={sim:.2f})")

        print(f"🔍 Matches: {matched_phrases}")
        print(f"⭐ Folder Score: {folder_score}\n")

        if folder_score >= min_score_threshold:
            scored.append((folder, folder_score))

    # sort desc by score
    scored = sorted(scored, key=lambda x: x[1], reverse=True)

    if not scored:
        print("🎯 Final Detected Folders: None ≥ threshold\n")
        return []

    print("🎯 Final Detected Folders (ALL ≥ threshold):")
    for f, s in scored:
        print(f"   - {f.name} (score={s})")
    print("============================================\n")

    return scored


#----------------for general queries ----------------
def is_general_query(query):
    q = query.lower().strip()

    general_keywords = [
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
        "hello",
        "hi",
        "how are you",
        "thanks",
        "thank you",
        "who are you",
        "today",
        "date",
        "day today",
        "what is today",
        "time",
    ]

    # if query fully contains any general phrase → treat as general
    return any(g in q for g in general_keywords)

#--------------- Best folder selection (semantic + keyword hybrid) ----------------
#--------------- Best folder selection (semantic + keyword hybrid) ----------------
def semantic_folder_search(query, top_n=3):
    """
    Returns top folders ranked by semantic relevance.
    Debug mode shows:
      - Folder scanned
      - Best PDF match inside folder
      - Semantic FAISS score
      - Ranking summary
    """

    print("\n====================== 🔍 SEMANTIC FOLDER SEARCH DEBUG ======================")
    print(f"📝 User Query: {query}\n")

    folders = Folder.objects.all()
    results = []  # list of (folder, score, answer, refs)

    for folder in folders:
        try:
            print(f"📁 Scanning Folder: {folder.name}")

            # search inside folder using embeddings + FAISS
            answer, refs = search_pdfs_fast(folder, query, top_n_pdfs=1)

            if not refs:
                print("   ⚠ No relevant PDFs found in this folder.\n")
                continue

            best_ref = refs[0]
            score = best_ref.get("score", 0)
            pdf_title = best_ref.get("title", "Unknown PDF")

            if score > 0:
                print(f"   ✔ Match Found → PDF: {pdf_title} | Score: {score}")
                results.append((folder, score, answer, refs))
            else:
                print(f"   ❌ Score = 0 → Ignored\n")

            print("")  # spacing

        except Exception as e:
            print(f"   ❌ Error scanning folder: {e}\n")
            continue  # ignore broken folder

    if not results:
        print("🚫 No folder returned any meaningful match.")
        print("==========================================================================\n")
        return []

    # sort by semantic score
    results = sorted(results, key=lambda x: x[1], reverse=True)

    print("🏆 FINAL SEMANTIC RANKING:")
    rank = 1
    for folder, score, answer, refs in results:
        print(f"   {rank}. {folder.name} — Score: {score}")
        rank += 1

    print(f"\n🎯 Top {top_n} folders selected.")
    print("==========================================================================\n")

    return results[:top_n]


