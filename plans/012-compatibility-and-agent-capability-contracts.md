# Plan 012: Add compatibility seams and agent-safe capability contracts

> **Executor instructions**: Retain Django, the Civic Knowledge Workbench UI,
> the current public search behavior, and the current authentication model.
> Relax implementation coupling behind adapters; do not force a public API or
> storage migration as part of this plan.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: Plan 011
- **Category**: architecture / developer-experience
- **Planned at**: commit `e6a3bb8`, 2026-07-26

## Decision

The Django framework and visual system remain product boundaries because they
are the proven citizen-facing surface. The current JSON search response and
local-volume data layout are compatibility contracts, not permanent internal
architecture. New work must pass through versioned internal contracts so the
old API can remain stable while storage, retrieval, model providers, and job
execution evolve independently.

The first implementation is a modular Django service layer, not a second
framework or a microservice fleet. Expose the same capability through a
management command and a narrow authenticated endpoint only after its service
contract and permission tests exist.

## Canonical contracts

Define versioned, JSON-schema-validatable records for:

- `EvidenceBundle`: verified chunks, page/source spans, visibility decision,
  retrieval scores, pack/generation digest, and citation metadata;
- `RetrievalResult`: document version, chunk ID, generation, score, source
  location, and policy decision;
- `AnswerRecord`: question/language, evidence digest, provider/model/config
  identity, prompt hash, answer status, citations, and timestamps;
- `GenerationManifest`: source/object hashes, extractor/chunker/embedder
  configuration, index artifacts, validation results, and active/superseded
  state;
- `CapabilityReceipt`: request/correlation/idempotency keys, actor, capability,
  state (`accepted`, `running`, `completed`, `failed`, `superseded`), and safe
  error details.

The current public response is rendered from these records through an adapter;
its fields remain unchanged until a separately approved API version exists.

## Capability surface

Start with narrow operations that are useful to operators and AI agents:

```text
inspect  search  cite  ingest  reindex  correct  retire
```

`inspect`, `search`, and `cite` are read-only first. `ingest`, `reindex`,
`correct`, and `retire` require explicit roles, resource versions,
idempotency keys, audit events, and a durable receipt. `correct` creates an
append-only proposal and a new generation; it never mutates evidence in place.

The browser, management commands, tests, and any future MCP/tool adapter must
use the same service functions and schemas. Agents must not infer job state
from animations, filenames, Redis keys, or undocumented scripts.

## OSS selection matrix

Choose one implementation per concern; avoid adding overlapping frameworks:

| Concern | Preferred starting point | Alternative / gate |
|---|---|---|
| Python validation and schemas | Pydantic models plus JSON Schema output | Typed dataclasses only if dependency minimization wins |
| Django API adapter | Existing Django views for compatibility; evaluate Django Ninja for typed internal endpoints | Django REST Framework if broad CRUD, permissions, or throttling needs justify it; do not add both by default |
| Object storage | `boto3` S3-compatible adapter with the existing capability probe | `django-storages` only where Django file-field integration reduces risk |
| Lexical retrieval | SQLite FTS/BM25 snapshot or PostgreSQL text search | Tantivy/OpenSearch only after corpus/load benchmark |
| Vector retrieval | Current FAISS behind a generation seam | `pgvector` after Plan 009 benchmark; external vector DB later |
| Background work | Existing `MaintenanceJob` semantics first | Celery/RQ only after measured throughput and retry gaps |
| Tests | Django test runner + pytest where useful, Hypothesis for contracts, Playwright + axe-core for browser/accessibility | Avoid a second test stack without a clear boundary |
| Static quality | Ruff, format check, type checking for new contracts, pre-commit | Select one type checker and enforce it incrementally |
| Observability | Structured JSON logs, correlation IDs, Prometheus-compatible metrics | OpenTelemetry when trace propagation across workers is needed |
| UI enhancement | Existing server-rendered templates, tokenized CSS, small vanilla JS modules | HTMX only for a measured interaction that cannot stay simple; no SPA rewrite |

The matrix is a selection record, not permission to add every tool. Each new
library needs a maintenance owner, security/update posture, bundle or runtime
cost, failure behavior, and removal path.

Useful primary references for implementation review are the [Pydantic JSON
Schema documentation](https://docs.pydantic.dev/latest/concepts/json_schema/),
[Django REST Framework serializers](https://www.django-rest-framework.org/api-guide/serializers/),
[Django REST Framework permissions and throttling](https://www.django-rest-framework.org/api-guide/permissions/),
[Django Ninja's API reference](https://django-ninja.dev/reference/api/), and the
[pgvector hybrid-search guidance](https://github.com/pgvector/pgvector).

## Implementation sequence

1. Define schemas and compatibility fixtures without changing routes.
2. Build `EvidenceBundle` and `GenerationManifest` adapters over current
   `PDFFile`, JSON embeddings, FAISS, `ArtifactGeneration`, and maintenance
   records.
3. Add read-only `inspect` and `cite` services; prove permission and
   generation-pinning behavior.
4. Add idempotent `reindex`/`ingest` receipts over existing maintenance jobs.
5. Run current OpenAI answer generation against the provider-neutral bundle in
   shadow mode; store provider/model/config metadata and compare citations,
   quality, latency, and cost.
6. Add one typed endpoint only after the service contract is stable; keep the
   existing public endpoint as an adapter.
7. Expose generation health, failed receipts, active/last-known-good IDs, and
   reconciliation findings in the existing admin/maintenance UI.

## Verification and stop conditions

- Old and new adapters return equivalent public response shapes.
- Repeated bundle/manifests are deterministic and generation-pinned.
- Failed rebuilds leave the prior generation serving.
- Duplicate capability requests do not duplicate jobs or side effects.
- Unauthorized evidence never enters retrieval, bundles, citations, or logs.
- OpenAI outages and provider changes produce safe, inspectable states.
- Browser, Django, API contract, accessibility, and slow-network tests pass.

Stop if the service boundary duplicates business logic, if idempotency cannot
be made durable on the selected relational backend, or if a proposed library
requires changing the public UI/API without an approved versioning plan.
