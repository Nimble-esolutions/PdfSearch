# core/retrieval/faiss_manager.py
import os
import json
import faiss
import numpy as np
from core.ingestion.embedder import embed_text
FAISS_DIR = "media/faiss"
def retrieve_documents(query: str, allowed_folders=None, limit=8):
    print("\n🔍 FAISS search started")
    print("Query:", query)
    print("Allowed folders:", allowed_folders)

    if not os.path.exists(FAISS_DIR):
        print("❌ FAISS directory not found")
        return []

    # 🔑 Embed + normalize query (CRITICAL)
    query_vec = embed_text(query)
    query_vec = np.array([query_vec], dtype="float32")
    faiss.normalize_L2(query_vec)

    results = []

    for file in os.listdir(FAISS_DIR):
        if not file.endswith(".index"):
            continue

        index_path = os.path.join(FAISS_DIR, file)
        meta_path = index_path.replace(".index", ".meta.json")

        if not os.path.exists(meta_path):
            print(f"⚠️ Meta missing for {file}")
            continue

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        folder = meta.get("folder")

        if allowed_folders and folder not in allowed_folders:
            continue

        print(f"📂 Loading index: {file}")

        index = faiss.read_index(index_path)

        if index.ntotal == 0:
            print("⚠️ Empty FAISS index:", file)
            continue

        D, I = index.search(query_vec, limit)

        for idx in I[0]:
            if idx == -1:
                continue
            if idx < len(meta["chunks"]):
                results.append(meta["chunks"][idx])

    # 🔹 Deduplicate + trim
    results = list(dict.fromkeys(results))[:limit]

    print(f"📄 Retrieved chunks: {len(results)}")
    return results
