# core/ingestion/embedder.py

import os
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from core.ingestion.chunker import chunk_text
from core.utils.language import normalize_marathi
from core.utils.faiss_utils import save_faiss_index

print("📦 embedder.py loaded")
# ================= MODEL =================

MODEL = SentenceTransformer(
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

# ---------------------------------------
def embed_chunks(chunks):
    print(f"🔢 Embedding {len(chunks)} chunks")
    embeddings = MODEL.encode(chunks, show_progress_bar=False)
    return embeddings.astype("float32")


# ---------------------------------------
def build_faiss_for_pdfOLD(pdf):
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
            "url": meta.get("pdf_url"),
            "chunks": chunks
        }, f, ensure_ascii=False, indent=2)

    print(f"✅ FAISS saved:")
    print(f"   📌 {index_path}")
    print(f"   📌 {meta_path}")

# ================= EMBED SINGLE TEXT =================

def embed_text(text: str) -> np.ndarray:
    print("🔍 Embedding query text")
    embedding = MODEL.encode([text], show_progress_bar=False)
    embedding = np.array(embedding, dtype="float32")
    faiss.normalize_L2(embedding)
    return embedding[0]


# ================= BUILD FAISS =================

def build_faiss_for_pdf(pdf):
    print("\n==============================")
    print(f"🚀 START FAISS BUILD | PDF ID={pdf.id} | {pdf.title}")
    print("==============================")

    if not pdf.text_content or len(pdf.text_content.strip()) < 200:
        print("❌ PDF text_content too small — skipping FAISS")
        return

    print("🔤 Normalizing text...")
    text = normalize_marathi(pdf.text_content)

    print("✂️ Creating chunks...")
    chunks = chunk_text(text)

    if not chunks:
        print("❌ No chunks created")
        return

    print(f"📄 Total chunks: {len(chunks)}")

    embeddings = embed_chunks(chunks)
    dim = embeddings.shape[1]

    print("🧮 Normalizing vectors...")
    faiss.normalize_L2(embeddings)

    print("🏗️ Creating FAISS index...")
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    metadata = {
        "pdf_id": pdf.id,
        "title": pdf.title,
        "folder": pdf.folder.name if pdf.folder else None,
        "pdf_url": pdf.file.url if pdf.file else None,
        "chunks": chunks
    }

    print("💾 Saving FAISS index...")
    save_faiss_index(index, pdf.id, metadata)

    pdf.indexed = True
    pdf.page_chunks = chunks
    pdf.save(update_fields=["indexed", "page_chunks"])

    print("✅ FAISS BUILD COMPLETE")
