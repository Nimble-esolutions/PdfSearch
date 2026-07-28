"""Pure classification of stored document search artifacts."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchArtifactHealth:
    reindex_required: bool
    repair_required: bool
    blocking: bool
    reason_codes: tuple[str, ...]
    chunk_count: int
    embedding_count: int
    dimension: int


def classify_search_artifacts(
    *,
    lifecycle: str,
    indexed: bool,
    media_exists: bool,
    page_chunks,
    chunk_embeddings,
) -> SearchArtifactHealth:
    """Classify document artifacts without logging document or vector content."""
    if lifecycle in {"archived", "deprecated"}:
        return SearchArtifactHealth(
            False, False, False, ("lifecycle_excluded",), 0, 0, 0
        )

    reasons: list[str] = []
    blocking = False
    if not media_exists:
        reasons.append("media_missing")
        blocking = True

    chunks = page_chunks if isinstance(page_chunks, list) else []
    if not chunks or any(
        not isinstance(chunk, str) or not chunk.strip() for chunk in chunks
    ):
        reasons.append("chunks_missing_or_invalid")

    embeddings = chunk_embeddings if isinstance(chunk_embeddings, list) else []
    dimension = 0
    dimensions: set[int] = set()
    vectors_valid = bool(embeddings)
    for vector in embeddings:
        if not isinstance(vector, list) or not vector:
            vectors_valid = False
            continue
        dimensions.add(len(vector))
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in vector
        ):
            vectors_valid = False
    if len(dimensions) == 1:
        dimension = next(iter(dimensions))
    else:
        vectors_valid = False
    if not vectors_valid:
        reasons.append("embeddings_missing_or_invalid")
    if len(chunks) != len(embeddings):
        reasons.append("chunk_embedding_count_mismatch")
    if lifecycle == "processing":
        reasons.append("lifecycle_processing")

    reindex_reasons = {
        "chunks_missing_or_invalid",
        "embeddings_missing_or_invalid",
        "chunk_embedding_count_mismatch",
        "lifecycle_processing",
    }
    reindex_required = not blocking and bool(reindex_reasons.intersection(reasons))
    repair_required = (
        not blocking
        and not reindex_required
        and bool(chunks)
        and bool(embeddings)
        and not indexed
    )
    if repair_required:
        reasons.append("stored_index_repair_required")

    return SearchArtifactHealth(
        reindex_required=reindex_required,
        repair_required=repair_required,
        blocking=blocking,
        reason_codes=tuple(dict.fromkeys(reasons)),
        chunk_count=len(chunks),
        embedding_count=len(embeddings),
        dimension=dimension,
    )
