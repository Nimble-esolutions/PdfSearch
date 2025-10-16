# vectorstore.py
import chromadb
from chromadb.utils import embedding_functions

# Persistent DB (saves on disk)
client = chromadb.PersistentClient(path="chroma_db")

# Embedding model (you can later switch to OpenAI or local embeddings)
embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

# Create collection (like a table in SQL)
pdf_collection = client.get_or_create_collection(
    name="pdf_chunks",
    embedding_function=embedding_func
)
