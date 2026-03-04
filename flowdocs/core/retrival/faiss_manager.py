# core/retrieval/faiss_manager.py
import os
import json
import faiss
import numpy as np

from django.conf import settings
from core.ingestion.embedder import embed_text
from core.ingestion.marathi_normalizer import normalize_marathi
FAISS_DIR = os.path.join(settings.MEDIA_ROOT, "faiss")
FAISS_MEMORY_CACHE = {}

def retrieve_documents(query: str, allowed_folders=None, limit=8):
    print("\n🔍 FAISS search started")
    print("Query:", query)
    print("Allowed folders:", allowed_folders)

    if not os.path.exists(FAISS_DIR):
        print("❌ FAISS directory not found")
        return []

    # Embed query
    print("🔍 Embedding query text")
    query_vec = embed_text(query)
    query_vec = np.array([query_vec]).astype("float32")

    results = []

    for file in os.listdir(FAISS_DIR):
        if not file.endswith(".index"):
            continue

        index_path = os.path.join(FAISS_DIR, file)
        meta_path = index_path.replace(".index", ".meta.json")

        if not os.path.exists(meta_path):
            print(f"⚠️ Meta file missing for {file}")
            continue

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Folder-name filtering (case-insensitive)
        folder = meta.get("folder", "").lower()
        allowed_folders_lower = [f.lower() for f in allowed_folders] if allowed_folders else []

        if allowed_folders and folder not in allowed_folders_lower:
            print(f"⛔ Skipping index {file} due to folder filter ({folder})")
            continue

        print(f"📂 Loading index: {file}, folder: {folder}, chunks available: {len(meta.get('chunks', []))}")

        if index_path in FAISS_MEMORY_CACHE:
            print("⚡ Using cached FAISS index")
            index = FAISS_MEMORY_CACHE[index_path]
        else:
            print("📂 Loading FAISS from disk...")
            index = faiss.read_index(index_path)
            FAISS_MEMORY_CACHE[index_path] = index

        # Search FAISS
        D, I = index.search(query_vec, limit)

        for idx in I[0]:
            if idx == -1:
                continue
            try:
                results.append({
                    "text": meta["chunks"][idx],
                    "pdf_id": meta.get("pdf_id"),
                    "title": meta.get("title"),
                    "pdf_url": meta.get("pdf_url"),
                    "folder": meta.get("folder")
                })
            except IndexError:
                print(f"⚠️ IndexError for idx {idx} in {file}")
                continue

    print(f"📄 Retrieved chunks: {len(results)}")
    return results
