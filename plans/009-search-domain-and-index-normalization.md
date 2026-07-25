# Plan 009: Normalize document, chunk, embedding, and index architecture

> **Executor instructions**: Start after Plans 011 and 012. Plan 008 is also a
> prerequisite only when PostgreSQL is the selected retrieval path. Do not
> remove FAISS or the current search path until the replacement has
> retrieval-parity evidence and a rollback switch.
>
> **Drift check (run first)**:
>
> ```bash
> git diff --stat f742b59..HEAD -- \
>   flowdocs/core/models.py flowdocs/core/utils.py \
>   flowdocs/core/views.py flowdocs/core/compatibility.py \
>   flowdocs/core/vectorstore.py flowdocs/core/pdf_serach_app.py \
>   flowdocs/core/tests.py
> ```

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: Plans 011 and 012, then Plan 008 when PostgreSQL is selected
- **Category**: tech-debt / migration
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: TODO

## Why this matters

`PDFFile` currently stores extracted text, page chunks, and the complete
embedding matrix in JSON fields while a separate per-folder FAISS file stores
the same vectors in an opaque derived representation. This creates large row
updates, weak provenance, difficult partial reprocessing, and a model/index
compatibility risk. The target makes document versions and chunks first-class,
records embedding provenance. PostgreSQL `pgvector` is one candidate canonical
retrieval dataset; a global memory-mapped FAISS/SQLite snapshot may be safer and
cheaper for the present corpus. FAISS becomes an optional generated cache or
primary read-only search artifact only when its measured operating envelope is
better.

## Retrieval decision gate

Benchmark these paths against the same sanitized query set and access rules:

1. SQLite projection plus SQLite FTS/BM25 and one global memory-mapped FAISS
   generation with category/visibility sidecars.
2. PostgreSQL text search plus `pgvector`, with database-side filters and
   reciprocal-rank fusion.
3. An external search engine only if corpus size, query load, or independent
   scaling makes its operational cost worthwhile.

Record p50/p95 latency, memory, rebuild duration, English/Marathi recall,
citation coverage, access-control equivalence, freshness, failure recovery, and
monthly cost. Do not choose a provider from familiarity or a dependency that
happens to be present in `requirements.txt`.

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

The retrieval provider must return an `EvidenceBundle` rather than prompt-ready
strings. The answer layer consumes that bundle through a provider-neutral
adapter and records an `AnswerRecord`; OpenAI is the current provider, not the
contract. This makes model changes, local/offline evaluation, and replay
possible without changing the public search response.

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

### Step 4: Introduce hybrid retrieval behind the existing contract

Run lexical retrieval (SQLite FTS/BM25 or PostgreSQL text search) and semantic
retrieval over the same verified chunks, then combine them with a documented
rank-fusion policy. Exact acts, sections, dates, and Marathi terms must not be
discarded by semantic ranking. The snapshot path uses one global index plus
sidecar filters; the PostgreSQL path may use `pgvector` after the decision gate.

Implement a provider interface returning the existing reference shape. Start
with shadow reads between the current provider and each candidate provider that
is actually provisioned (for example SQLite snapshot versus PostgreSQL), record
top-k overlap and latency, but return the current result. Add a reviewed feature
flag to switch by environment, not by user input.

For the PostgreSQL path, use cosine distance with normalized vectors, an HNSW
index only after measuring data size, and PostgreSQL filters for
category/lifecycle/access scope. Keep equivalent sidecar filters for the
snapshot path.

**Verify**: parity threshold, authorization equivalence, Marathi/English
queries, no-result behavior, and p95 latency meet the release record.

### Step 5: Require evidence quorum before answer generation

Before sending context to the answer model, independently check lexical and
semantic agreement, category/lifecycle/access policy, evidence-pack root and
document/page freshness, prompt-boundary limits, and complete source metadata.
If the quorum fails, return the existing no-result/unavailable behavior or a
reviewable refusal rather than inventing confidence. Evaluate false refusals
and missed answers in shadow mode before making the policy user-visible.

### Step 6: Demote or retain FAISS as a rebuildable search artifact

If FAISS remains useful for offline or low-cost deployments, build it from the
normalized source and upload it as an immutable `RetrievalGeneration` artifact.
Never update an active index in place. Otherwise remove it only after a full
rollback window and restore drill.

**Verify**: deleting the FAISS artifact and rebuilding it produces the same
generation checksum and retrieval evidence.

### Step 7: Retire duplicate search paths

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

## Commands, scope, and git workflow

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test core.tests.SearchAndAuthenticationTests \
  core.tests.SearchIndexLifecycleTests core.tests.ArtifactInventoryTests
python -m unittest discover -s integration_tests -p 'test_*.py'
git diff --check
```

Add a deterministic evaluation command that runs without live OpenAI calls and
emits query ID, provider/generation, ranked chunk IDs, access decisions,
latency, and citation coverage. Store only sanitized fixtures/results.

In scope: additive document/version/chunk/embedding/generation schema,
ingestion state, provider adapters, evaluation fixtures, shadow reads, and
tests. Out of scope: changing citizen-facing answer JSON, removing current
retrieval before rollback evidence, introducing an external search service
without a benchmark, and fabricating page metadata.

Use separate commits for characterization fixtures, additive schema,
ingestion, provider/shadow reads, and retirement. Every commit must keep the
existing search path bootable.

## Stop conditions and boundaries

Stop if the active embedding model cannot be identified, if current results
cannot be reproduced from stored chunks, if the selected provider cannot meet
the retrieval contract, or if authorization filtering differs between
providers. If PostgreSQL/pgvector was selected, its unavailability is a stop
condition; it is not a blocker when the SQLite snapshot path wins the decision
gate. Do not change the public answer JSON shape until a separate API
compatibility decision is approved.

## Done criteria

- New documents no longer write embeddings into unbounded JSON fields.
- Every vector has model, dimension, normalization, and generation provenance.
- Search provider can switch between old and new implementations safely.
- Retrieval parity and access-control tests pass.
- FAISS/legacy Chroma status is explicit and no duplicate path is accidental.

## Maintenance notes

Every extractor, chunker, embedder, ranker, and model change creates a new
configuration/generation identity. Reviewers should scrutinize access filters
before ranking and before citation serialization, false refusals from evidence
quorum, and English/Marathi exact-term regression. Never compare generated prose
byte-for-byte as a retrieval parity test.
