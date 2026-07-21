import os
import fitz  # PyMuPDF
from openai import OpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
import chromadb
from chromadb.utils import embedding_functions
from django.conf import settings

# --------------------------- CONFIG ---------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PERSIST_DIR = str(getattr(settings, "CHROMA_DIR", "chroma_store"))

client = OpenAI(api_key=OPENAI_API_KEY)
chroma_client = chromadb.PersistentClient(path=PERSIST_DIR)

# Create / get a collection
collection = chroma_client.get_or_create_collection(
    name="marathi_pdfs",
    metadata={"description": "Marathi PDF embeddings for semantic search"},
    embedding_function=embedding_functions.OpenAIEmbeddingFunction(
        api_key=OPENAI_API_KEY, model_name="text-embedding-3-large"
    ),
)

# --------------------------- HELPERS ---------------------------
def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    text = ""
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text += page.get_text("text")
    return text.strip()


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100):
    """Split large text into overlapping chunks."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=overlap)
    return splitter.split_text(text)


def embed_and_store(pdf_path: str):
    """Process PDF → chunks → embeddings → store in Chroma."""
    file_name = os.path.basename(pdf_path)
    print(f"📄 Processing: {file_name}")

    text = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)

    # Assign unique IDs per chunk
    ids = [f"{file_name}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"file_name": file_name, "chunk_index": i} for i in range(len(chunks))]

    # Add to Chroma collection
    collection.add(documents=chunks, ids=ids, metadatas=metadatas)
    print(f"✅ Indexed {len(chunks)} chunks from {file_name}")


def query_search(question: str, top_k: int = 5):
    """Search Chroma and query GPT-5 Turbo for final answer."""
    print(f"\n🔍 Query: {question}")

    results = collection.query(query_texts=[question], n_results=top_k)
    contexts = results["documents"][0]
    sources = results["metadatas"][0]

    context_text = "\n\n".join(contexts)
    source_files = ", ".join(sorted({m["file_name"] for m in sources}))

    prompt = f"""
खालील मजकूराचा संदर्भ घेऊन प्रश्नाचे उत्तर द्या (मराठीत):

{context_text}

प्रश्न: {question}
"""

    response = client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[{"role": "user", "content": prompt}],
    )

    answer = response.choices[0].message.content
    print(f"\n🧠 उत्तर:\n{answer}")
    print(f"\n📚 स्रोत फाइल(स): {source_files}")


# --------------------------- MAIN ---------------------------
if __name__ == "__main__":
    print("==== Marathi PDF Search App ====")
    print("1️⃣ Upload and index PDF")
    print("2️⃣ Query existing PDFs")
    choice = input("Enter your choice (1 or 2): ").strip()

    if choice == "1":
        path = input("Enter PDF file path to upload: ").strip()
        if os.path.exists(path):
            embed_and_store(path)
        else:
            print("❌ File not found.")
    elif choice == "2":
        q = input("Enter your Marathi question: ").strip()
        query_search(q)
    else:
        print("❌ Invalid choice.")
