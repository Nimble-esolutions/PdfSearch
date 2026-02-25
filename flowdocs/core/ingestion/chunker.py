# core/ingestion/chunker.py
def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100
) -> list[str]:
    """
    Split clean text into overlapping chunks.
    Assumes text is already normalized.
    """
    if not text:
        return []

    chunks = []
    start = 0
    length = len(text)

    while start < length:
        end = start + chunk_size
        chunk = text[start:end]

        if len(chunk.strip()) > 200:
            chunks.append(chunk)

        start = end - overlap

    return chunks
