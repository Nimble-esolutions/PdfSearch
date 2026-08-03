"""Embedding model dimensions shared by indexing and recovery preflight."""

from __future__ import annotations

from collections.abc import Iterable


class EmbeddingContractError(RuntimeError):
    """Raised when a configured model cannot preserve stored vector compatibility."""


_MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def embedding_dimension_for_model(model: str) -> int:
    """Return the provider's default vector dimension for a supported model."""

    normalized = str(model or "").strip().lower()
    try:
        return _MODEL_DIMENSIONS[normalized]
    except KeyError as exc:
        raise EmbeddingContractError("embedding_model_dimension_unknown") from exc


def observed_embedding_dimensions(
    embedding_sets: Iterable[object],
) -> set[int]:
    """Collect dimensions from well-shaped stored vectors without mutating them."""

    dimensions: set[int] = set()
    for embeddings in embedding_sets:
        if not isinstance(embeddings, list):
            continue
        for embedding in embeddings:
            if isinstance(embedding, list) and embedding:
                dimensions.add(len(embedding))
    return dimensions


def require_incremental_embedding_compatibility(
    *,
    model: str,
    observed_dimensions: set[int],
) -> int:
    """Reject incremental repair when it would mix incompatible vector spaces."""

    expected = embedding_dimension_for_model(model)
    if observed_dimensions and observed_dimensions != {expected}:
        raise EmbeddingContractError("embedding_model_dimension_mismatch")
    return expected
