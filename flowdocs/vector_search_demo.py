import os
import fitz  # PyMuPDF
import easyocr
import numpy as np
import faiss

# ---------------- PDF Folder ----------------
PDF_FOLDER = r"E:\Snehal\GovernmentApp\AISahakar-testing\AISahakar-testing\flowdocs\media\pdfs"

# ---------------- EasyOCR Reader ----------------
reader = easyocr.Reader(['en', 'mr'], gpu=False)  # GPU वापरल्यास True करा

# ---------------- PDF वाचणे + OCR fallback ----------------
def extract_text_from_pdf(file_path):
    text = ""
    try:
        doc = fitz.open(file_path)
        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text("text")
            if not page_text.strip():
                # OCR fallback
                pix = page.get_pixmap()
                ocr_result = reader.readtext(pix.tobytes(), detail=0)
                page_text = "\n".join(ocr_result)
            text += page_text + "\n"
        doc.close()
    except Exception as e:
        print(f"[❌] Failed to read {file_path}: {e}")
    return text.strip()

# ---------------- PDFs process ----------------
pdf_texts = []
pdf_files = []

for filename in os.listdir(PDF_FOLDER):
    if filename.lower().endswith(".pdf"):
        full_path = os.path.join(PDF_FOLDER, filename)
        text = extract_text_from_pdf(full_path)
        print(f"{filename} -> text length: {len(text)}")
        if text:
            pdf_texts.append(text)
            pdf_files.append(filename)

print(f"[✅] Total PDFs processed with text: {len(pdf_texts)}")

# ---------------- Chunking ----------------
def chunk_text(text, chunk_size=500, overlap=100):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

all_chunks = []
chunk_sources = []

for i, text in enumerate(pdf_texts):
    chunks = chunk_text(text)
    all_chunks.extend(chunks)
    chunk_sources.extend([pdf_files[i]] * len(chunks))

print(f"[✅] Total chunks: {len(all_chunks)}")

# ---------------- Offline Embeddings (random) ----------------
dimension = 5  # Offline demo dimension
embeddings = np.random.rand(len(all_chunks), dimension).astype("float32")

# ---------------- Build FAISS Index ----------------
index = faiss.IndexFlatL2(dimension)
index.add(embeddings)
print(f"[✅] FAISS index created with {index.ntotal} vectors")

# ---------------- Debug: Print embeddings ----------------
print("\nEmbeddings (first 5 vectors):")
for i, emb in enumerate(embeddings[:5]):
    print(f"{i+1}: {emb}")

# ---------------- Debug: Print vectors stored in index ----------------
print("\nVectors stored in FAISS index (reconstruct first 5):")
for i in range(min(5, index.ntotal)):
    print(f"{i+1}: {index.reconstruct(i)}")
# ---------------- Query ----------------
query = "जी. डी. सी. ॲण्ड ए. परीक्षा कोण घेते ?"  # तुमचा प्रश्न
query_vec = np.random.rand(1, dimension).astype("float32")  # Offline demo

k = 3  # Top 3 matches
distances, indices = index.search(query_vec, k)

print("\nTop matches:")
for rank, idx in enumerate(indices[0]):
    print(f"{rank+1}. PDF: {chunk_sources[idx]}")
   # print(f"   Snippet: {all_chunks[idx][:2000]}...")
    print(f"   Distance: {distances[0][rank]}")
