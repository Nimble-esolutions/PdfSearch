# core/ingestion/embedder.py

import os
import faiss
import numpy as np
from openai import OpenAI
from django.conf import settings

from core.ingestion.chunker import chunk_text
from core.utils.language import normalize_marathi
from core.utils.faiss_utils import save_faiss_index

print("📦 embedder.py loaded")

# ================= OPENAI CLIENT =================

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ================= EMBED CHUNKS =================

def embed_chunks(chunks):
    print(f"🔢 Embedding {len(chunks)} chunks using OpenAI")

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=chunks
    )

    embeddings = [item.embedding for item in response.data]

    return np.array(embeddings).astype("float32")


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

    metadata = {
        "pdf_id": pdf.id,
        "title": pdf.title,
        "folder": "housing",
        "url": pdf.file.url if pdf.file else None,
        "chunks": chunks
    }

    print("💾 Saving FAISS index...")
    save_faiss_index(index, pdf.id, metadata)

    print("✅ FAISS saved")


# ================= EMBED SINGLE TEXT =================

def embed_text(text: str) -> np.ndarray:
    print("🔍 Embedding query text using OpenAI")

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=[text]
    )

    embedding = np.array(response.data[0].embedding).astype("float32")

    faiss.normalize_L2(embedding.reshape(1, -1))

    return embedding


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
