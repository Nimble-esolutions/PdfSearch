"""PDF extraction, embedding, indexing, and search helpers."""

from __future__ import annotations

import os
import json
import math
import pathlib
import re
import shutil
import subprocess
import traceback
import logging
import tempfile
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any

import fitz  # PyMuPDF
import numpy as np
from openai import OpenAI
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from .models import Folder
from difflib import SequenceMatcher

from indic_transliteration import sanscript as sc
from indic_transliteration.sanscript import transliterate
from langdetect import detect, DetectorFactory, LangDetectException

from .models import Folder, PDFFile, SEARCHABLE_PDF_LIFECYCLES

# Optional FAISS
try:
    import faiss
    _HAS_FAISS = True
except Exception:
    faiss = None
    _HAS_FAISS = False


class SearchDataIntegrityError(RuntimeError):
    """Raised when stored search artifacts cannot be trusted for retrieval."""


logger = logging.getLogger(__name__)

# ------------------- Configuration -------------------
DetectorFactory.seed = 0

# OpenAI client
OPENAI_API_KEY = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
OPENAI_EMBED_MODEL = getattr(settings, "OPENAI_EMBED_MODEL", "text-embedding-3-small")
OPENAI_CHAT_MODEL = getattr(settings, "OPENAI_CHAT_MODEL", "gpt-4o-mini")  # change as needed

_client: Any = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from .ai_guard import get_openai_client
        _client = get_openai_client()
    except Exception as exc:
        from .utils import SearchDataIntegrityError
        raise SearchDataIntegrityError(f"OpenAI client unavailable: {exc}") from exc
    return _client


def _test_embeddings_enabled() -> bool:
    return os.getenv("PDFSEARCH_TEST_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}


def _deterministic_embeddings(texts: list[str]) -> list[list[float]]:
    """Use only for credential-free disposable runtime smoke tests."""
    return [[1.0, 0.0] for _ in texts]

# Embedding and chunk sizes
CHUNK_SIZE = getattr(settings, "PDF_CHUNK_SIZE", 1200)
CHUNK_OVERLAP = getattr(settings, "PDF_CHUNK_OVERLAP", 200)
MAX_CONTEXT_WORDS = getattr(settings, "MAX_CONTEXT_WORDS", 2500)  # much smaller than 22500
TOP_K_CHUNKS = getattr(settings, "TOP_K_CHUNKS", 5)

# Cache TTLs (seconds)
EMBEDDING_TTL = getattr(settings, "EMBEDDING_TTL", 60 * 60 * 24 * 7)  # 7 days
SEARCH_CACHE_TTL = getattr(settings, "SEARCH_CACHE_TTL", 60 * 10)  # 10 minutes

# ------------------ Helpers ------------------
def transliterate_marathi_to_english(text: str) -> str:
    try:
        return transliterate(text, sc.DEVANAGARI, sc.ITRANS)
    except Exception:
        return text


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"
    devanagari_chars = [c for c in text if '\u0900' <= c <= '\u097F']
    if len(devanagari_chars) / max(len(text), 1) > 0.2:
        return "mr"
    try:
        lang = detect(text)
        return lang if lang in ("en", "mr") else "en"
    except LangDetectException:
        return "en"


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        start += max(1, chunk_size - overlap)
    return chunks


def truncate_context(text: str, max_words: int = MAX_CONTEXT_WORDS) -> str:
    words = text.split()
    return " ".join(words[:max_words]) if len(words) > max_words else text


# ------------------ PDF extraction (cached per model) ------------------
@dataclass(frozen=True)
class PDFExtractionResult:
    text: str
    metadata: dict[str, Any]


def _ocr_languages() -> str:
    raw = str(getattr(settings, "PDF_OCR_LANGUAGES", "eng+mar") or "").strip()
    languages = [part for part in raw.split("+") if part]
    if not languages or any(not re.fullmatch(r"[A-Za-z0-9_]+", part) for part in languages):
        raise SearchDataIntegrityError("PDF OCR language configuration is invalid")
    return "+".join(languages)


def _ocr_page_text(page, *, page_number: int, temp_dir: pathlib.Path, deadline: float) -> str:
    binary_name = str(getattr(settings, "PDF_OCR_BINARY", "tesseract") or "tesseract")
    binary = shutil.which(binary_name)
    if not binary:
        raise SearchDataIntegrityError("PDF OCR engine is unavailable")

    dpi = int(getattr(settings, "PDF_OCR_DPI", 200))
    scale = dpi / 72.0
    rect = page.rect
    estimated_pixels = int(rect.width * scale * rect.height * scale)
    max_pixels = int(getattr(settings, "PDF_OCR_MAX_PIXELS", 25_000_000))
    if estimated_pixels > max_pixels:
        raise SearchDataIntegrityError("PDF OCR page exceeds the pixel budget")

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SearchDataIntegrityError("PDF OCR document timeout exceeded")
    page_timeout = int(getattr(settings, "PDF_OCR_PAGE_TIMEOUT_SECONDS", 180))
    timeout = max(1, min(page_timeout, int(remaining)))
    image_path = temp_dir / f"page-{page_number}.png"
    try:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.save(str(image_path))
        result = subprocess.run(
            [
                binary,
                str(image_path),
                "stdout",
                "-l",
                _ocr_languages(),
                "--psm",
                "3",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SearchDataIntegrityError("PDF OCR page timeout exceeded") from exc
    except OSError as exc:
        raise SearchDataIntegrityError("PDF OCR process could not start") from exc
    finally:
        image_path.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.warning(
            "PDF OCR page failed without indexing the document: page=%s return_code=%s",
            page_number,
            result.returncode,
        )
        raise SearchDataIntegrityError("PDF OCR process failed")
    return (result.stdout or "").strip()


def _ocr_blank_pages(blank_pages: list[tuple[int, object]]) -> dict[int, str]:
    if not blank_pages or not getattr(settings, "PDF_OCR_FALLBACK_ENABLED", True):
        return {}
    max_pages = int(getattr(settings, "PDF_OCR_MAX_PAGES", 50))
    if len(blank_pages) > max_pages:
        raise SearchDataIntegrityError("PDF OCR page cap exceeded")
    deadline = time.monotonic() + int(getattr(settings, "PDF_OCR_MAX_SECONDS", 900))
    results: dict[int, str] = {}
    with tempfile.TemporaryDirectory(prefix="pdf-ocr-") as temporary:
        temp_dir = pathlib.Path(temporary)
        for page_number, page in blank_pages:
            results[page_number] = _ocr_page_text(
                page,
                page_number=page_number,
                temp_dir=temp_dir,
                deadline=deadline,
            )
    logger.info(
        "PDF OCR fallback completed: pages=%s extracted_chars=%s",
        len(blank_pages),
        sum(len(value) for value in results.values()),
    )
    return results


def extract_text_from_pdf_path(path: str) -> str:
    """Extract native PDF text and OCR only pages without native text.

    OCR is bounded by deployment settings and runs through an argument-list
    subprocess invocation, never a shell command. If OCR is unavailable or
    fails, the caller receives no fabricated text and normal indexing fails
    closed for an image-only document.
    """
    return extract_pdf_content(path).text


def extract_pdf_content(path: str) -> PDFExtractionResult:
    """Extract text and secret-free OCR provenance for one PDF.

    The metadata records processing posture and page counts only; it never
    contains document text, OCR output, or provider credentials.
    """
    empty = PDFExtractionResult(text="", metadata={"schema_version": 1})
    if not os.path.exists(path):
        return empty
    page_texts: list[str] = []
    blank_pages: list[tuple[int, object]] = []
    doc = None
    try:
        doc = fitz.open(path)
        native_text_pages = 0
        for page_number, page in enumerate(doc):
            page_text = (page.get_text("text") or "").strip()
            page_texts.append(page_text)
            native_text_pages += bool(page_text)
            if not page_text:
                blank_pages.append((page_number, page))
        ocr_text = _ocr_blank_pages(blank_pages)
        for page_number, text in ocr_text.items():
            page_texts[page_number] = text
        metadata = {
            "schema_version": 1,
            "native_text_page_count": native_text_pages,
            "page_count": len(page_texts),
            "ocr_page_count": len(ocr_text),
            "ocr_pages": sorted(ocr_text),
            "ocr_languages": _ocr_languages() if blank_pages else "",
            "ocr_engine": str(getattr(settings, "PDF_OCR_BINARY", "tesseract") or "tesseract"),
            "ocr_engine_version": str(getattr(settings, "PDF_OCR_ENGINE_VERSION", "runtime") or "runtime"),
            "ocr_fallback_enabled": bool(getattr(settings, "PDF_OCR_FALLBACK_ENABLED", True)),
            "confidence_available": False,
        }
    except SearchDataIntegrityError as exc:
        logger.warning("PDF text extraction failed closed: %s", exc)
        return empty
    except Exception:
        logger.exception("PDF text extraction failed closed")
        return empty
    finally:
        if doc is not None:
            doc.close()
    return PDFExtractionResult(
        text="\n".join(text for text in page_texts if text).strip(),
        metadata=metadata,
    )

# ---------------- Embeddings ----------------
def create_embeddings_for_texts(texts: List[str], batch_size: int = 16) -> List[List[float]]:
    """Call OpenAI embeddings in batches. Returns list of lists (embeddings)."""
    if _test_embeddings_enabled():
        return _deterministic_embeddings(texts)
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = _get_client().embeddings.create(model=OPENAI_EMBED_MODEL, input=batch)
        for d in resp.data:
            embeddings.append(list(d.embedding))
    return embeddings


# ----------------- FAISS helpers -----------------
def _faiss_dir() -> pathlib.Path:
    configured = getattr(settings, "FAISS_INDEX_DIR", None)
    if configured:
        return pathlib.Path(configured)
    return pathlib.Path(getattr(settings, "DATA_ROOT", getattr(settings, "BASE_DIR", os.getcwd()))) / "faiss_indexes"


def faiss_index_path_for_folder(folder: Folder) -> str:
    return str(_faiss_dir() / f"folder_{folder.id}.index")


def _json_list(value: Any, field_name: str, pdf: PDFFile) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise SearchDataIntegrityError(
                f"PDF {pdf.pk} has invalid {field_name} JSON"
            ) from exc
    if not isinstance(value, list):
        raise SearchDataIntegrityError(f"PDF {pdf.pk} has invalid {field_name} metadata")
    return value


def _folder_embedding_matrix(
    folder: Folder,
    pdfs=None,
) -> tuple[list[str], np.ndarray]:
    """Return deterministic chunk order and validated, normalized embeddings."""
    chunk_texts: list[str] = []
    chunk_embeddings: list[np.ndarray] = []
    expected_dimensions = None

    pdf_queryset = (
        pdfs
        if pdfs is not None
        else PDFFile.objects.filter(folder=folder)
    ).filter(
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
    )
    for pdf in pdf_queryset.order_by("pk"):
        try:
            p_chunks = _json_list(getattr(pdf, "page_chunks", []), "page_chunks", pdf)
            p_embs = _json_list(getattr(pdf, "chunk_embeddings", []), "chunk_embeddings", pdf)
            if not p_chunks or not p_embs:
                raise SearchDataIntegrityError("missing searchable chunks or embeddings")
            if len(p_chunks) != len(p_embs):
                raise SearchDataIntegrityError("chunk and embedding counts differ")

            pdf_chunks = []
            pdf_embeddings = []
            for chunk, embedding in zip(p_chunks, p_embs):
                if not isinstance(chunk, str) or not chunk.strip():
                    raise SearchDataIntegrityError("contains an invalid chunk")
                if not isinstance(embedding, (list, tuple)) or not embedding:
                    raise SearchDataIntegrityError("contains an invalid embedding")
                vector = np.asarray(embedding, dtype=np.float32)
                if vector.ndim != 1 or not np.isfinite(vector).all():
                    raise SearchDataIntegrityError("contains an invalid embedding vector")
                if not np.linalg.norm(vector):
                    raise SearchDataIntegrityError("contains a zero embedding vector")
                pdf_chunks.append(chunk)
                pdf_embeddings.append(vector)
            pdf_dimensions = {vector.shape[0] for vector in pdf_embeddings}
            if len(pdf_dimensions) != 1:
                raise SearchDataIntegrityError("contains inconsistent embedding dimensions")
            pdf_dimension = next(iter(pdf_dimensions))
            if expected_dimensions is not None and pdf_dimension != expected_dimensions:
                raise SearchDataIntegrityError("embedding dimension differs from the folder")
            expected_dimensions = pdf_dimension
        except (SearchDataIntegrityError, TypeError, ValueError) as exc:
            logger.warning("Skipping PDF id=%s from folder id=%s: %s", pdf.pk, folder.pk, exc)
            continue

        chunk_texts.extend(pdf_chunks)
        chunk_embeddings.extend(pdf_embeddings)

    if not chunk_embeddings:
        return [], np.empty((0, 0), dtype=np.float32)

    dimensions = {vector.shape[0] for vector in chunk_embeddings}
    if len(dimensions) != 1:
        raise SearchDataIntegrityError("Folder embeddings have inconsistent dimensions")
    matrix = np.vstack(chunk_embeddings).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return chunk_texts, matrix / norms


def _validate_index(index: Any, chunk_count: int, dimensions: int) -> None:
    if index is None:
        raise SearchDataIntegrityError("FAISS index is unavailable")
    if int(index.d) != dimensions:
        raise SearchDataIntegrityError(
            f"FAISS dimension mismatch: index={index.d}, database={dimensions}"
        )
    if int(index.ntotal) != chunk_count:
        raise SearchDataIntegrityError(
            f"FAISS vector count mismatch: index={index.ntotal}, database={chunk_count}"
        )


def _build_or_load_faiss_index_for_folder(
    folder: Folder,
    pdfs=None,
    force_rebuild: bool = False,
    promote_index=None,
) -> Tuple[Optional[faiss.Index], List[str], np.ndarray]:
    """
    Build or load a FAISS index for a folder.
    Returns (index, chunks_flat_list, normalized_embeddings). A stored index is
    usable only when its dimensions and vector count match the database metadata.
    """
    chunk_texts, embeddings_matrix = _folder_embedding_matrix(folder, pdfs=pdfs)
    if not chunk_texts:
        return None, [], embeddings_matrix
    if not _HAS_FAISS:
        return None, chunk_texts, embeddings_matrix

    # A restricted search scope cannot use the persistent folder index because
    # that index may contain documents outside the caller's access policy.
    if pdfs is not None:
        try:
            index = faiss.IndexFlatIP(embeddings_matrix.shape[1])
            index.add(embeddings_matrix)
            _validate_index(index, len(chunk_texts), embeddings_matrix.shape[1])
            return index, chunk_texts, embeddings_matrix
        except SearchDataIntegrityError:
            raise
        except Exception as exc:
            raise SearchDataIntegrityError("Unable to build scoped FAISS index") from exc

    idx_path = faiss_index_path_for_folder(folder)
    if os.path.exists(idx_path) and not force_rebuild:
        try:
            index = faiss.read_index(idx_path)
        except Exception as exc:
            logger.warning("Rebuilding unreadable FAISS index %s: %s", idx_path, exc)
        else:
            try:
                _validate_index(index, len(chunk_texts), embeddings_matrix.shape[1])
            except SearchDataIntegrityError as exc:
                logger.warning("Rebuilding stale FAISS index %s: %s", idx_path, exc)
            else:
                return index, chunk_texts, embeddings_matrix

    try:
        pathlib.Path(idx_path).parent.mkdir(parents=True, exist_ok=True)
        index = faiss.IndexFlatIP(embeddings_matrix.shape[1])
        index.add(embeddings_matrix)
        _validate_index(index, len(chunk_texts), embeddings_matrix.shape[1])
        fd, temp_path = tempfile.mkstemp(
            prefix=f"{pathlib.Path(idx_path).name}.",
            suffix=".tmp",
            dir=str(pathlib.Path(idx_path).parent),
        )
        os.close(fd)
        try:
            faiss.write_index(index, temp_path)
            if promote_index is None:
                os.replace(temp_path, idx_path)
            else:
                promote_index(temp_path, idx_path)
                temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return index, chunk_texts, embeddings_matrix
    except SearchDataIntegrityError:
        raise
    except Exception as exc:
        raise SearchDataIntegrityError(f"Unable to build FAISS index {idx_path}") from exc


def build_or_load_faiss_index_for_folder(
    folder: Folder,
    pdfs=None,
    force_rebuild: bool = False,
    promote_index=None,
) -> Tuple[Optional[faiss.Index], List[str], np.ndarray]:
    if pdfs is not None:
        return _build_or_load_faiss_index_for_folder(
            folder,
            pdfs=pdfs,
            force_rebuild=force_rebuild,
            promote_index=promote_index,
        )
    from vaultops.services.mutations import mutation_scope

    def fenced_promotion(temp_path, index_path):
        with mutation_scope(
            category="faiss",
            relative_path=f"folder_{folder.pk}.index",
            operation="promote",
        ):
            if promote_index is None:
                os.replace(temp_path, index_path)
            else:
                promote_index(temp_path, index_path)

    return _build_or_load_faiss_index_for_folder(
        folder,
        pdfs=pdfs,
        force_rebuild=force_rebuild,
        promote_index=fenced_promotion,
    )


def search_chunks_with_faiss_or_numpy(
    query_embedding: np.ndarray,
    index: Optional[faiss.Index],
    chunk_texts: List[str],
    top_k: int = TOP_K_CHUNKS,
    embeddings_matrix: Optional[np.ndarray] = None,
) -> List[Tuple[str, float]]:
    """
    Returns list of (chunk_text, score) sorted desc by score.
    If FAISS index provided, use it. Otherwise run numpy dot product.
    """
    if query_embedding is None or len(chunk_texts) == 0:
        return []
    if embeddings_matrix is None:
        raise SearchDataIntegrityError("Database embeddings are required for search")

    q = query_embedding.astype(np.float32)
    if q.ndim != 1 or not np.isfinite(q).all() or not np.linalg.norm(q):
        raise SearchDataIntegrityError("Query embedding is invalid")
    if embeddings_matrix.ndim != 2 or len(chunk_texts) != len(embeddings_matrix):
        raise SearchDataIntegrityError("Database chunk metadata is inconsistent")
    if embeddings_matrix.shape[1] != q.shape[0]:
        raise SearchDataIntegrityError(
            f"Query dimension mismatch: query={q.shape[0]}, database={embeddings_matrix.shape[1]}"
        )
    q_norm = q / np.linalg.norm(q)

    if index is not None and _HAS_FAISS:
        _validate_index(index, len(chunk_texts), embeddings_matrix.shape[1])
        try:
            distances, indices = index.search(np.array([q_norm]), k=min(top_k, index.ntotal))
        except Exception as exc:
            raise SearchDataIntegrityError("FAISS search failed") from exc
        return [
            (chunk_texts[idx], float(distance))
            for distance, idx in zip(distances[0], indices[0])
            if idx >= 0
        ]

    if _HAS_FAISS:
        raise SearchDataIntegrityError("FAISS index is unavailable")

    scores = embeddings_matrix @ q_norm
    order = np.argsort(scores)[::-1][:top_k]
    return [(chunk_texts[int(idx)], float(scores[idx])) for idx in order]


# ----------------- Public: Precompute embeddings on upload -----------------
def _precompute_pdf_embeddings(
    pdf: PDFFile, *, rebuild_index: bool = True
) -> None:
    """
    Called when a PDF is added/updated.
    This extracts text, chunks it, creates embeddings, and stores them on the PDF model.
    Also triggers folder FAISS index rebuild.
    Requires PDFFile to have fields: extracted_text (TextField), page_chunks (JSONField), chunk_embeddings (JSONField).
    """
    path = pdf.file.path
    extraction = extract_pdf_content(path)
    extracted = extraction.text
    if not extracted:
        raise SearchDataIntegrityError(f"PDF {pdf.pk} has no extractable text")

    chunks = [
        chunk for chunk in chunk_text(extracted, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        if chunk and len(chunk.strip()) > 30
    ]
    if not chunks:
        raise SearchDataIntegrityError(f"PDF {pdf.pk} produced no searchable chunks")

    embeddings = create_embeddings_for_texts(chunks, batch_size=16)
    if len(embeddings) != len(chunks):
        raise SearchDataIntegrityError(f"PDF {pdf.pk} embedding count does not match chunk count")

    pdf.extracted_text = extracted
    pdf.text_content = extracted
    pdf.page_chunks = chunks
    pdf.chunk_embeddings = embeddings
    pdf.ocr_metadata = extraction.metadata
    pdf.save(update_fields=["extracted_text", "text_content", "page_chunks", "chunk_embeddings", "ocr_metadata"])

    if not rebuild_index:
        pdf.indexed = False
        pdf.save(update_fields=["indexed"])
        return

    promote_index = None
    if transaction.get_connection().in_atomic_block:
        def defer_index_promotion(temp_path, idx_path):
            def promote():
                from vaultops.services.mutations import mutation_scope
                try:
                    with mutation_scope(
                        category="faiss",
                        relative_path=pathlib.Path(idx_path).name,
                        operation="promote",
                    ):
                        os.replace(temp_path, idx_path)
                        PDFFile.objects.filter(pk=pdf.pk).update(indexed=True)
                except Exception:
                    logger.exception("Deferred FAISS promotion failed for folder id=%s", pdf.folder_id)
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)

            transaction.on_commit(promote)

        promote_index = defer_index_promotion

    index, _, _ = build_or_load_faiss_index_for_folder(
        pdf.folder,
        force_rebuild=True,
        promote_index=promote_index,
    )
    if index is None:
        raise SearchDataIntegrityError("PDF embeddings were stored but no FAISS index was created")
    if promote_index is None:
        pdf.indexed = True
        pdf.save(update_fields=["indexed"])


def precompute_pdf_embeddings(
    pdf: PDFFile, *, rebuild_index: bool = True
) -> None:
    from vaultops.services.mutations import mutation_scope

    with mutation_scope(
        category="pdf",
        relative_path=str(pdf.file.name or pdf.pk),
        operation="precompute_embeddings",
    ):
        _precompute_pdf_embeddings(pdf, rebuild_index=rebuild_index)


# ------------------ Search PDFs (fast path) ------------------
def search_pdfs_fast(
    folder: Folder,
    user_query: str,
    top_n_pdfs: int = 2,
    pdfs=None,
    language: str = "en",
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fast search that:
     - uses precomputed chunk embeddings saved on PDFs
     - loads or builds FAISS folder index (persistent)
     - finds top chunks and returns combined context and references
    Returns (answer_text, references_list)
     Each reference has: title, pdf_id, folder, uploaded_at, score.
     The protected view URL is added by the HTTP view before serialization.
    """
    # 1. quick guard
    restricted_scope = pdfs is not None
    pdfs = (
        pdfs
        if restricted_scope
        else PDFFile.objects.filter(folder=folder)
    ).filter(
        lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
    )
    if not pdfs.exists():
        return "", []

    # 2. create query embedding
    try:
        if _test_embeddings_enabled():
            query_emb = np.array(_deterministic_embeddings([user_query])[0], dtype=np.float32)
        else:
            emb_resp = _get_client().embeddings.create(model=OPENAI_EMBED_MODEL, input=[user_query])
            query_emb = np.array(emb_resp.data[0].embedding, dtype=np.float32)
    except Exception as exc:
        raise SearchDataIntegrityError("Unable to create the query embedding") from exc

    index, chunk_texts, embeddings_matrix = build_or_load_faiss_index_for_folder(
        folder,
        pdfs=pdfs if restricted_scope else None,
    )

    # 3. get top matched chunks (text + scores)
    matches = search_chunks_with_faiss_or_numpy(
        query_emb,
        index,
        chunk_texts,
        top_k=TOP_K_CHUNKS * 10,
        embeddings_matrix=embeddings_matrix,
    )

    if not matches:
        # fallback to rule-based search
        import re
        rule_match = re.search(r"नियम\s*([०१२३४५६७८९0-9]+)", user_query)
        matches = []
        for pdf in pdfs:
            chunks = getattr(pdf, "page_chunks", []) or []
            for c in chunks:
                score = 0
                if rule_match and f"नियम {rule_match.group(1)}" in c:
                    score += 5
                if user_query in c:
                    score += 1
                if score > 0:
                    matches.append((c, score))
        matches = sorted(matches, key=lambda x: x[1], reverse=True)[:TOP_K_CHUNKS * 10]

    # 4. collate matches by PDF
    chunk_to_pdf = {}
    for pdf in pdfs:
        p_chunks = getattr(pdf, "page_chunks", []) or []
        uploaded_at = getattr(pdf, "uploaded_at", None)
        uploaded_at_str = uploaded_at.strftime("%Y-%m-%d") if uploaded_at else None
        for c in p_chunks:
            if c not in chunk_to_pdf:
                chunk_to_pdf[c] = {
                    "title": getattr(pdf, "title", None),
                    "pdf_id": pdf.pk,
                    "folder": getattr(getattr(pdf, "folder", None), "name", None),
                    "uploaded_at": uploaded_at_str,
                }

    # Aggregate by PDF: sum scores and collect top snippets
    pdf_scores = {}
    pdf_snippets = {}
    for chunk_text, score in matches:
        meta = chunk_to_pdf.get(chunk_text)
        if not meta:
            continue
        title = meta["title"]
        pdf_scores.setdefault(title, 0)
        pdf_scores[title] += score
        pdf_snippets.setdefault(title, []).append(chunk_text)

    if not pdf_scores:
        return "", []

    # Create references list
    refs = []
    for title, s in pdf_scores.items():
        pdf_obj = pdfs.filter(title=title).first()
        if pdf_obj:
            uploaded_at = getattr(pdf_obj, "uploaded_at", None)
            refs.append({
                "title": title,
                "pdf_id": pdf_obj.pk,
                "folder": getattr(getattr(pdf_obj, "folder", None), "name", None),
                "uploaded_at": uploaded_at.strftime("%Y-%m-%d") if uploaded_at else None,
                "score": s,
            })
        else:
            refs.append({"title": title, "pdf_id": None, "folder": None, "uploaded_at": None, "score": s})

    # pick top N PDFs by score
    refs = sorted(refs, key=lambda x: x["score"], reverse=True)[:top_n_pdfs]

    # build context: include only top K snippets across top refs
    combined_snippets = []
    for r in refs:
        title = r["title"]
        snippets = pdf_snippets.get(title, [])[:TOP_K_CHUNKS]
        label = f"--- {title} ---"
        combined_snippets.append(label)
        combined_snippets.extend(snippets)
    combined_context = "\n\n".join(combined_snippets)
    combined_context = truncate_context(combined_context, max_words=MAX_CONTEXT_WORDS)

    # 5. generate answer
    answer = generate_gpt_answer(
        user_question=user_query,
        context=combined_context,
        references=refs,
        max_words=400,
        language=language,
    )
    return answer, refs

# ------------------ GPT answer ------------------
def generate_gpt_answer(
    user_question: str,
    context: str,
    references: List[Dict[str, Any]] = None,
    max_words: int = 200,
    language: str = "en",
) -> str:
    """
    Query the LLM with a small, high-quality context. Use cached responses if available.
    """
    language = language if language in {"en", "mr"} else "en"
    cache_key = f"gpt_ans:{language}:{hash(user_question + (context or ''))}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    if not _get_client():
        return "OpenAI API key not configured."

    if language == "mr":
        system_msg = "तुम्ही एक सहाय्यक आहात. मराठीतून उत्तर द्या. मर्यादा 200 शब्द."
        prompt = f"प्रश्न: {user_question}\n\nसंदर्भ:\n{context}"
    else:
        system_msg = "You are a helpful assistant. Answer concisely in English, max 200 words."
        prompt = f"Q: {user_question}\n\nContext:\n{context}"

    if references:
        refs_text = "\n".join([f"- {r.get('title')}" for r in references if r.get('title')])
        prompt = f"{prompt}\n\nSources:\n{refs_text}"

    try:
        resp = _get_client().chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.0,
        )
        # Safe access depending on SDK shape
        ans = ""
        if hasattr(resp, "choices"):
            ans = getattr(resp.choices[0].message, "content", "") if resp.choices else ""
        else:
            # older shape
            try:
                ans = resp["choices"][0]["message"]["content"]
            except Exception:
                ans = str(resp)
        ans = ans.strip()
        # truncate to word limit
        words = ans.split()
        if len(words) > max_words:
            ans = " ".join(words[:max_words]) + "..."
        cache.set(cache_key, ans, SEARCH_CACHE_TTL)
        return ans
    except Exception:
        traceback.print_exc()
        return "⚠️ Couldn't generate answer right now. Please try again later."

# ---------------- Detect Folder by Keywords matching by single word----------------
def detect_folder_by_single_keywords(query):
    """
    Returns a Folder model instance based on keyword matching.
    """
    folders = Folder.objects.all()
    query_words = query.lower().split()
    folder_scores = []

    for folder in folders:
        if not hasattr(folder, 'keywords') or not folder.keywords:
            continue

        folder_keywords = (
            [k.strip().lower() for k in folder.keywords]
            if isinstance(folder.keywords, list)
            else [k.strip().lower() for k in folder.keywords.split(",")]
        )

        score = sum(1 for qw in query_words if qw in folder_keywords)

        if score > 0:
            folder_scores.append((folder, score))

    if not folder_scores:
        return None  # means unknown

    folder_scores.sort(key=lambda x: x[1], reverse=True)
    top_folder = folder_scores[0][0]

    return top_folder   # IMPORTANT — return Folder instance, not string

#---------------- Detect Folder by Key phrases matching by multiple word----------------
def fuzzy_ratio(a, b):
    """Calculate similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def detect_folder_by_keywords(query):
    """
    Detects the best matching folder based on:
    - direct phrase match
    - reverse phrase match
    - fuzzy similarity
    Includes full debug output.
    """
    print("\n========== 🔍 KEYWORD DEBUG INFO ==========")
    print(f"📝 User Query: {query}\n")

    folders = Folder.objects.all()
    query_lower = query.lower().strip()

    best_folder = None
    best_score = 0.0

    for folder in folders:
        # Skip folders without keywords
        if not folder.keywords:
            continue

        # Normalize folder keyword list (remove empty items)
        if isinstance(folder.keywords, list):
            folder_keywords = [
                k.strip().lower()
                for k in folder.keywords
                if k and k.strip()
            ]
        else:
            folder_keywords = [
                k.strip().lower()
                for k in folder.keywords.split(",")
                if k and k.strip()
            ]

        print(f"📁 Folder: {folder.name}")
        print(f"🔑 Keywords: {folder_keywords}")

        folder_score = 0.0
        matched_phrases = []

        for phrase in folder_keywords:
            sim = fuzzy_ratio(query_lower, phrase)

            # 1️⃣ direct substring match
            if phrase in query_lower:
                folder_score += 1.0
                matched_phrases.append(f"{phrase} (substring)")
                continue

            # 2️⃣ reversed substring match
            if query_lower in phrase:
                folder_score += 0.8
                matched_phrases.append(f"{phrase} (reverse-substring)")
                continue

            # 3️⃣ fuzzy similarity > 0.60
            if sim > 0.60:
                folder_score += sim
                matched_phrases.append(f"{phrase} (fuzzy={sim:.2f})")

        print(f"🔍 Matches: {matched_phrases}")
        print(f"⭐ Folder Score: {folder_score}\n")

        if folder_score > best_score:
            best_folder = folder
            best_score = folder_score

    # 🎯 If NO meaningful match, return NONE
    if best_score < 0.70:  # Minimum threshold for reliability
        print("🎯 Final Detected Folder: None (no strong match)")
        print("============================================\n")
        return None

    print(f"🎯 Final Detected Folder: {best_folder.name}")
    print("============================================\n")

    return best_folder


#---------------- detect multiple folders for search query ----------------
def detect_folder_by_keywords_multi(query, min_score_threshold=0.50, folders=None):
    """
    Modified version:
    - Returns ALL folders with score >= threshold
    - Not just a single best folder
    - Keeps your fuzzy + substring logic fully intact
    """

    print("\n========== 🔍 KEYWORD DEBUG INFO (MULTI-FOLDER) ==========")
    print(f"📝 User Query: {query}\n")

    folders = folders if folders is not None else Folder.objects.all()
    query_lower = query.lower().strip()

    scored = []  # will store (folder, score)

    for folder in folders:
        if not folder.keywords:
            continue

        # normalize keywords
        if isinstance(folder.keywords, list):
            folder_keywords = [k.strip().lower() for k in folder.keywords if k.strip()]
        else:
            folder_keywords = [k.strip().lower() for k in folder.keywords.split(",") if k.strip()]

        print(f"📁 Folder: {folder.name}")
        print(f"🔑 Keywords: {folder_keywords}")

        folder_score = 0.0
        matched_phrases = []

        for phrase in folder_keywords:
            sim = fuzzy_ratio(query_lower, phrase)

            # direct match
            if phrase in query_lower:
                folder_score += 1.0
                matched_phrases.append(f"{phrase} (substring)")
                continue

            # reversed
            if query_lower in phrase:
                folder_score += 0.8
                matched_phrases.append(f"{phrase} (reverse-substring)")
                continue

            # fuzzy
            if sim > 0.60:
                folder_score += sim
                matched_phrases.append(f"{phrase} (fuzzy={sim:.2f})")

        print(f"🔍 Matches: {matched_phrases}")
        print(f"⭐ Folder Score: {folder_score}\n")

        if folder_score >= min_score_threshold:
            scored.append((folder, folder_score))

    # sort desc by score
    scored = sorted(scored, key=lambda x: x[1], reverse=True)

    if not scored:
        print("🎯 Final Detected Folders: None ≥ threshold\n")
        return []

    print("🎯 Final Detected Folders (ALL ≥ threshold):")
    for f, s in scored:
        print(f"   - {f.name} (score={s})")
    print("============================================\n")

    return scored


#----------------for general queries ----------------
def is_general_query(query):
    q = query.lower().strip()

    general_keywords = [
        "good morning",
        "good afternoon",
        "good evening",
        "good day",
        "hello",
        "hi",
        "how are you",
        "thanks",
        "thank you",
        "who are you",
        "today",
        "date",
        "day today",
        "what is today",
        "time",
    ]

    # if query fully contains any general phrase → treat as general
    return any(g in q for g in general_keywords)

#--------------- Best folder selection (semantic + keyword hybrid) ----------------
#--------------- Best folder selection (semantic + keyword hybrid) ----------------
def semantic_folder_search(query, top_n=3):
    """
    Returns top folders ranked by semantic relevance.
    Debug mode shows:
      - Folder scanned
      - Best PDF match inside folder
      - Semantic FAISS score
      - Ranking summary
    """

    print("\n====================== 🔍 SEMANTIC FOLDER SEARCH DEBUG ======================")
    print(f"📝 User Query: {query}\n")

    folders = Folder.objects.all()
    results = []  # list of (folder, score, answer, refs)

    for folder in folders:
        try:
            print(f"📁 Scanning Folder: {folder.name}")

            # search inside folder using embeddings + FAISS
            answer, refs = search_pdfs_fast(folder, query, top_n_pdfs=1)

            if not refs:
                print("   ⚠ No relevant PDFs found in this folder.\n")
                continue

            best_ref = refs[0]
            score = best_ref.get("score", 0)
            pdf_title = best_ref.get("title", "Unknown PDF")

            if score > 0:
                print(f"   ✔ Match Found → PDF: {pdf_title} | Score: {score}")
                results.append((folder, score, answer, refs))
            else:
                print(f"   ❌ Score = 0 → Ignored\n")

            print("")  # spacing

        except Exception as e:
            print(f"   ❌ Error scanning folder: {e}\n")
            continue  # ignore broken folder

    if not results:
        print("🚫 No folder returned any meaningful match.")
        print("==========================================================================\n")
        return []

    # sort by semantic score
    results = sorted(results, key=lambda x: x[1], reverse=True)

    print("🏆 FINAL SEMANTIC RANKING:")
    rank = 1
    for folder, score, answer, refs in results:
        print(f"   {rank}. {folder.name} — Score: {score}")
        rank += 1

    print(f"\n🎯 Top {top_n} folders selected.")
    print("==========================================================================\n")

    return results[:top_n]
