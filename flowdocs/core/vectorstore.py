# vectorstore.py
import chromadb
from chromadb.utils import embedding_functions
from django.conf import settings

# Persistent DB
client = chromadb.PersistentClient(path="chroma_db")

# Embedding models
embedding_large = embedding_functions.OpenAIEmbeddingFunction(
    api_key=settings.OPENAI_API_KEY,
    model_name="text-embedding-3-large"
)

embedding_small = embedding_functions.OpenAIEmbeddingFunction(
    api_key=settings.OPENAI_API_KEY,
    model_name="text-embedding-3-small"
)

# ✅ Collection must include embedding_function only here
pdf_collection_large = client.get_or_create_collection(
    name="pdf_chunks",
    embedding_function=embedding_large
)

pdf_collection_small = client.get_or_create_collection(
    name="pdf_chunks_small",
    embedding_function=embedding_small
)
