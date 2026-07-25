# Plan 009: Normalize document, chunk, embedding, and index architecture

> **Executor instructions**: Treat this as a design-and-implementation plan
> that follows Plan 008's custody migration. Do not remove FAISS or the current
> search path until the replacement has retrieval-parity evidence and a
> rollback switch.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: Plan 008
- **Category**: tech-debt / migration
- **Planned at**: commit `d3fc328`, 2026-07-26

## Why this matters

`PDFFile` currently stores extracted text, page chunks, and the complete
embedding matrix in JSON fields while a separate per-folder FAISS file stores
the same vectors in an opaque derived representation. This creates large row
updates, weak provenance, difficult partial reprocessing, and a model/index
compatibility risk. The target makes document versions and chunks first-class,
records embedding provenance, and uses PostgreSQL `pgvector` for the canonical
retrieval dataset; FAISS becomes an optional generated cache during transition.

## Current state and risks

- `flowdocs/core/models.py:34-88` overloads `PDFFile` with identity, storage,
  text, chunks, embeddings, category, and lifecycle.
- `flowdocs/core/utils.py:377-424` recomputes the entire PDF embedding payload
  during upload and rebuilds the folder index.
- `flowdocs/core/utils.py:257-326` loads or rebuilds one FAISS index per
  folder, validating only vector count and dimension.
- `flowdocs/core/utils.py:329-373` searches FAISS or falls back to NumPy over
  the database JSON embedding matrix.
- `flowdocs/core/vectorstore.py` and `flowdocs/core/pdf_serach_app.py` are
  separate Chroma/console paths and must be classified as legacy or brought
  under the same provider contract before further use.
- `flowdocs/core/compatibility.py:105-136` checks model and dimension at
  generation compatibility level, but the current `PDFFile` row does not
  independently record embedding provenance.

## Target domain model

Use these concepts and invariants:

- `Category`: stable taxonomy node; supports hierarchy and aliases without
  making a folder name the retrieval boundary.
- `Document`: stable logical document identity and access policy.
- `DocumentVersion`: immutable source PDF checksum, object key, title/version
  metadata, extraction status, and provenance.
- `Chunk`: immutable ordinal, page/section offsets, text, text checksum, and
  document-version foreign key.
- `EmbeddingSet`: provider, model, dimension, metric, normalization,
  chunking configuration, language, and creation timestamp.
- `ChunkEmbedding`: one vector per chunk per embedding set, unique on
  `(embedding_set, chunk)`.
- `RetrievalGeneration`: embedding set, index strategy, category/access scope,
  build commit, artifact digest, and active/superseded status.

Every search result must carry document version, chunk ID, retrieval
generation, score, and source location. Every answer citation must point to a
retrieval result, never reconstructed title text.

## Implementation sequence

### Step 1: Characterize current behavior

Capture current top-k IDs, scores, source metadata, access filtering,
English/Marathi behavior, and no-result behavior for a fixed query corpus.
Mark `vectorstore.py` and `pdf_serach_app.py` as legacy unless an owner proves
they are deployed.

**Verify**: a repeatable fixture produces stable retrieval evidence without
calling the live OpenAI service.

### Step 2: Add provenance and normalized tables

Add additive migrations and backfill from `PDFFile`. Preserve old fields during
the transition. Store embeddings as `vector(n)` with a check on dimension when
`pgvector` is selected; do not store unbounded float arrays in JSON for new
records. Keep an explicit provider/model/config hash.

**Verify**: a document version can be independently re-extracted, rechunked,
re-embedded, and indexed without rewriting unrelated documents.

### Step 3: Build an idempotent ingestion state machine

Implement these durable states:

```text
received → stored → extracted → chunked → embedded → indexed → ready
                 ↘ failed (retryable/non-retryable with reason)
```

Use an outbox or durable maintenance job to enqueue each transition. Each step
must be idempotent by document-version checksum and configuration hash. A
failed index build must not mark the document ready or expose partial results.

**Verify**: retries, worker restart, duplicate upload, missing source object,
and model outage leave one coherent state with a recoverable reason.

### Step 4: Introduce pgvector retrieval behind the existing contract

Implement a provider interface returning the existing reference shape. Start
with shadow reads: execute both pgvector and current FAISS retrieval, record
top-k overlap and latency, but return the current result. Add a reviewed
feature flag to switch by environment, not by user input.

Use cosine distance with normalized vectors, an HNSW index after measuring data
size, and PostgreSQL filters for category/lifecycle/access scope. Keep a
metadata index on document version, category, lifecycle, and visibility.

**Verify**: parity threshold, authorization equivalence, Marathi/English
queries, no-result behavior, and p95 latency meet the release record.

### Step 5: Demote FAISS to a rebuildable artifact

If FAISS remains useful for offline or low-cost deployments, build it from the
normalized source and upload it as an immutable `RetrievalGeneration` artifact.
Never update an active index in place. Otherwise remove it only after a full
rollback window and restore drill.

**Verify**: deleting the FAISS artifact and rebuilding it produces the same
generation checksum and retrieval evidence.

### Step 6: Retire duplicate search paths

After deployment usage is proven, either delete the legacy Chroma/console path
or document it as a separately maintained tool with its own model/config
contract. Do not leave two embedding models silently available to the same
dataset.

## Test plan

- Model and migration tests for one-to-many document versions and chunks.
- Idempotency and state-machine tests for every transition and retry.
- Retrieval parity tests with fixed embeddings and real sanitized fixtures.
- Authorization tests proving restricted categories never leak through vector
  search or citations.
- Index rebuild and checksum tests.
- Load tests for concurrent search and background ingestion.
- Browser tests for upload progress, ready/error states, source links, and
  follow-up search.

## Stop conditions and boundaries

Stop if the active embedding model cannot be identified, if current results
cannot be reproduced from stored chunks, if pgvector is unavailable in the
approved PostgreSQL service, or if authorization filtering differs between
providers. Do not change the public answer JSON shape until a separate API
compatibility decision is approved.

## Done criteria

- New documents no longer write embeddings into unbounded JSON fields.
- Every vector has model, dimension, normalization, and generation provenance.
- Search provider can switch between old and new implementations safely.
- Retrieval parity and access-control tests pass.
- FAISS/legacy Chroma status is explicit and no duplicate path is accidental.
