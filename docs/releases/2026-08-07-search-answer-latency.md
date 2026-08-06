Status: In review
Audience: Maintainer, Operator, Developer, Reviewer
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: docs/releases/2026-08-07-search-answer-latency.md
Supersedes: None

# Search answer latency and UI continuity

## Outcome

This change removes avoidable work from the public search request and removes
artificial answer-reveal delay from the Knowledge Workbench. It does not change
the question-language contract, searchable-document authorization, ranking
bounds, protected PDF links, document custody, or Classic/Workbench isolation.

The optimization is deliberately automatic. It adds no environment variables
and no operator choreography.

## Measured baseline

Read-only stage measurements on 2026-08-07 used the same benign public query
twice against the signed 242-document, 46-folder runtime:

| Request | End-to-end response time | What it proved |
| --- | ---: | --- |
| First request | 23,738 ms | Retrieval plus external answer generation were both on the critical path |
| Immediate repeat | 12,910 ms | The existing answer cache saved about 10.8 seconds, but embedding and complete corpus retrieval still ran before that cache was consulted |

These are pre-change observations from the deployed stage revision, not a
claim that this branch is deployed. Post-merge stage certification must repeat
the canary and compare phase telemetry.

## Root-cause analysis

| Root cause | Previous behavior | User impact | Correction |
| --- | --- | --- | --- |
| Cache lookup occurred too late | The application embedded the question and searched every visible folder before consulting the answer cache | A repeated question still took about 12.9 seconds | An exact result cache is checked before embedding and retrieval |
| Full corpus was rebuilt per request | All stored chunk JSON and embedding JSON were parsed for every visible folder on every query | Latency grew with document and folder count | One normalized, process-local corpus is built for the exact signed immutable runtime and reused by that worker |
| Folder data was read twice | Retrieval loaded stored chunks, then the index loader loaded the same rows again; an extra existence query also ran | Duplicate SQLite and JSON work | One coherent snapshot is passed through the fallback path and the redundant existence query is removed |
| Persistent FAISS validation was shape-only | Equal vector count and dimensions could accept stale or reordered vectors | Fast but incorrect matches were possible | Loaded vectors are reconstructed and compared with the current snapshot before reuse |
| Query embeddings were never cached | Repeated questions always called the embedding provider | Repeated provider latency and cost | Provider-scoped query embeddings use the existing `EMBEDDING_TTL` |
| Workbench simulated streaming after completion | Completed JSON answers were revealed one grapheme every 8 ms, up to about 7.2 seconds for 900 characters | The server had finished, but the answer still looked slow | Completed answers render synchronously with safe structural formatting |
| No phase evidence | Logs reported only total request duration and call counts | Operators could not separate embedding, retrieval, model, cache, or UI delay | Secret-free phase timing and cache/corpus diagnostics are logged |

## Safety boundaries

- Exact result and full-corpus reuse require the generation, manifest digest,
  and signed runtime pointer resolved at startup to agree. Unsigned or mutable
  development data stays on the conservative per-folder path.
- Result cache keys bind provider policy, embedding/chat models, answer-contract
  version, language, ordered folder scope, runtime identity, and an explicit
  access scope. Public and per-admin entries cannot cross scopes. Restricted
  non-admin users do not use the whole-result cache.
- Query embedding keys bind provider policy, embedding model, and the exact
  question. A provider-policy change produces a different key.
- Cached provider output is still checked against the requested English or
  Marathi script before it can be returned.
- A transient chat-provider failure is marked in diagnostics and is never
  promoted into the exact-result cache; the next request can retry normally.
- Candidate limits, per-folder bounds, context size, and protected reference
  handling remain unchanged.
- Original PDFs and extracted content are not added to telemetry. Logs contain
  counts, byte sizes, cache flags, and milliseconds only.

## Overall impact

| Boundary | Effect | Risk and mitigation |
| --- | --- | --- |
| Development/test | Mutable local data uses the existing fallback; query embedding caching still applies under provider scope | Developers see less acceleration than signed stage; this avoids serving stale local edits |
| Stage | Warm workers reuse the signed corpus; exact repeats can bypass embedding, retrieval, and chat generation | Each Gunicorn worker retains one float32 matrix plus bounded chunk metadata; verify RSS under the 2 GiB service limit after rollout |
| Future production | The same signed-runtime behavior is available when production activation is implemented | No production deployment or traffic change is part of this PR; certify an immutable image and memory/latency canaries before cutover |
| Redis | Adds hashed query-embedding and exact-result entries using existing TTL settings | Existing bounded TTLs control pressure; no document text or raw query appears in cache keys |
| Search quality | Ranking and top-N rules are preserved; stale same-shape FAISS indexes are rejected | Regression tests cover authorization, cache partitioning, corpus reuse, and vector integrity |
| UI/UX | Workbench answers appear as soon as the completed response arrives; errors are announced; composer remains usable for another query | Browser tests cover immediate rendering, sequential questions, failures, and both public themes |

The seed database currently contains 1,580 vectors at the configured embedding
dimension; the normalized float32 matrix is about 9.3 MiB. Stage must report
the actual `corpus_vectors` and `corpus_bytes` values because worker memory is
proportional to the active runtime, not the seed fixture.

A local CPU microbenchmark using that exact shape (1,580 × 1,536), 46 folders,
and the production ranking bounds measured the new in-memory retrieval step at
1.02 ms on first invocation, 0.83 ms p50, and 1.01 ms p95 across 25 warm
invocations. This isolates matrix ranking only—it does not include SQLite
corpus warm-up, Redis, network embedding, or chat generation—so the post-rollout
stage canary remains the end-to-end acceptance measurement.

## Classic UI incident closure

The Classic defects reported on 2026-08-06 had three linked causes: raw answer
text was not structurally formatted, the page rather than the transcript owned
long-answer overflow, and the composer shared that unbounded layout. The
current `dev` history fixes these in isolated Classic assets:

- allowlisted headings, lists, paragraphs, and bold text are built from DOM
  nodes and text nodes, never provider HTML;
- the viewport is a bounded grid and the transcript owns `overflow: auto` with
  `min-height: 0` through its ancestors;
- the composer is a non-shrinking sibling of the transcript and remains
  visible and focused after completion or failure; and
- browser coverage proves wheel/keyboard transcript scrolling, protected
  sources, a second question, mobile width, and short-landscape continuity.

This branch does not merge the two frontend implementations. It adds equivalent
sequential-query and retryable-error guarantees to the Workbench while leaving
Classic as the default.

## Verification and rollout gate

Before merge:

1. Run focused Django search tests and the full configured core/DataOps smoke
   test selection.
2. Run Classic, Workbench, motion, Marathi/English, accessibility, and viewport
   browser tests with Playwright's matching headless shell.
3. Run Python compilation, JavaScript syntax, locale, migration/test-isolation,
   operator-language, documentation, Compose, and repository whitespace gates.
4. Render and validate the updated Mermaid source.

After a green merge and stage image rollout:

1. Record the resolved image digest/revision and unchanged signed generation.
2. Run one uncached and one exact-repeat benign public query in English and
   Marathi.
3. Compare `embedding_ms`, `retrieval_ms`, `answer_ms`, total duration, cache
   flags, `corpus_vectors`, and `corpus_bytes` without logging query content.
4. Compare web-worker RSS before and after corpus warm-up and keep aggregate
   service memory below the configured limit with headroom.
5. Canary Classic and `?view=workbench`, including a long formatted answer,
   protected source opening, a second question, and an authored failure.
6. Roll back the application image if latency regresses, RSS loses safe
   headroom, authorization differs, or either theme loses continuity. The
   active data/runtime pointer remains unchanged during an image rollback.
