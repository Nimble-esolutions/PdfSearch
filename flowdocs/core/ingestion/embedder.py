import os
import json
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from core.ingestion.chunker import chunk_text
from core.utils.language import normalize_marathi

# ---------------- CONFIG ----------------
FAISS_DIR = "data/faiss"
os.makedirs(FAISS_DIR, exist_ok=True)

MODEL = SentenceTransformer(
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# ---------------------------------------
def embed_chunks(chunks):
    print(f"🔢 Embedding {len(chunks)} chunks")
    embeddings = MODEL.encode(chunks, show_progress_bar=False)
    return embeddings.astype("float32")


# ---------------------------------------
def build_faiss_for_pdf(pdf):
    print(f"\n🏗️ Building FAISS for PDF ID={pdf.id} | {pdf.title}")

    if not pdf.text_content or len(pdf.text_content.strip()) < 200:
        print("❌ PDF text_content is empty — FAISS not built")
        return

    text = normalize_marathi(pdf.text_content)
    chunks = chunk_text(text)

    if not chunks:
        print("❌ No chunks created — FAISS not built")
        return

    print(f"📄 Chunks created: {len(chunks)}")

    embeddings = embed_chunks(chunks)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    faiss.normalize_L2(embeddings)
    index.add(embeddings)

    index_path = os.path.join(FAISS_DIR, f"{pdf.id}.index")
    meta_path = os.path.join(FAISS_DIR, f"{pdf.id}.meta.json")

    faiss.write_index(index, index_path)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "pdf_id": pdf.id,
            "title": pdf.title,
            "folder": "housing",
            "chunks": chunks
        }, f, ensure_ascii=False, indent=2)

    print(f"✅ FAISS saved:")
    print(f"   📌 {index_path}")
    print(f"   📌 {meta_path}")

def embed_text(text: str) -> np.ndarray:
    """
    Embed a single text string using the same model as chunks.
    Returns a normalized float32 vector.
    """
    embedding = MODEL.encode([text], show_progress_bar=False)
    embedding = np.array(embedding, dtype="float32")
    faiss.normalize_L2(embedding)
    return embedding[0]