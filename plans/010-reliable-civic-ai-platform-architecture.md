# Plan 010: Evolve PdfSearch into a reliable, cost-controlled civic AI platform

> **Executor instructions**: This is a target architecture plan, not permission
> to replace the current deployment wholesale. Prefer a modular monolith and
> incremental boundaries; each platform change must preserve the existing
> search API, UI contract, authentication, and data-custody rules.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED/HIGH
- **Depends on**: Plans 008 and 009
- **Category**: direction / architecture
- **Planned at**: commit `d3fc328`, 2026-07-26

## Recommended target topology

```text
Users
  │ HTTPS / Traefik / CDN for static assets
  ▼
Django web: auth, admin, public search API, civic workbench
  │                 │
  │                 ├── Redis: cache, rate limits, short-lived locks
  │                 ├── PostgreSQL + pgvector: transactional and retrieval data
  │                 └── S3/RustFS: PDFs, derived artifacts, immutable manifests
  ▼
Durable job queue + worker: extract → chunk → embed → index → validate
  │
  └── Scheduler: retries, retention, backup verification, health probes
```

Keep web and worker as separate processes/images even if they remain in one
repository. Do not split into microservices until an independently scaled
boundary is proven necessary.

## Architecture principles

1. PostgreSQL is the transactional source of truth.
2. Object storage is the source of truth for binary documents and immutable
   generated artifacts.
3. Vectors and chunk metadata have explicit provenance and a rebuild path.
4. Redis is never the durable job ledger or authoritative data store.
5. Every data release is content-addressed, manifest-pinned, validated, and
   rollbackable.
6. Web requests do not perform OCR, embedding, bulk reindex, or restore loops.
7. UI state reflects durable backend state; animation never represents fake
   progress.
8. Environment, dataset, writer, and side-effect identity fail closed.

## Backend and product improvements

### Modular Django monolith first

Partition code into explicit modules such as `identity`, `catalog`,
`documents`, `ingestion`, `retrieval`, `answers`, `operations`, and `storage`.
Keep domain services behind interfaces so storage and vector-provider changes
do not leak into views. Preserve the current public search response while
versioning any future API change.

### Durable ingestion and retrieval

Use a transactional outbox or durable job table to publish ingestion work.
Workers claim jobs with leases, idempotency keys, bounded retries, and
dead-letter state. Emit structured events with request/job/document/generation
IDs. Use one active writer per dataset and explicit generation promotion.

### Grounded answer contract

Persist retrieval evidence and answer metadata separately from the answer text:
question, language, model, prompt/config hash, generation ID, source chunk IDs,
scores, refusal/error state, and timestamps. Do not call an answer “official”;
make source documents and human support the authoritative path.

### Access control at retrieval time

Apply category/document visibility filters before vector ranking and again
before citation serialization. Test anonymous, user, admin, and superadmin
scopes with adversarial cross-category fixtures.

## Deployment and operations

- Build one immutable web image and one worker image from pinned lockfiles.
- Run migrations as a one-shot release job with advisory locking, not inside
  every web replica startup.
- Use managed PostgreSQL backups/PITR, object-store versioning/retention, and
  Redis with persistence only for cache/locks as needed.
- Keep Dokploy/Traefik for the current scale, but pin image digests and record
  release evidence. Move to Kubernetes only if independent scaling,
  multi-node scheduling, or platform policy makes the complexity worthwhile.
- Use `/livez`, `/readyz`, data health, queue depth, backup age, object-store
  health, search latency, error rate, and answer/source coverage as alerts.
- Centralize logs with redacted structured JSON, request IDs, and no prompt,
  credential, or PDF-content leakage.

## Cost controls

- Start with one PostgreSQL instance plus pgvector rather than adding a vector
  database and search cluster.
- Store PDFs once by SHA-256 and deduplicate objects.
- Keep FAISS only when measured latency/cost justifies its operational cost.
- Apply object lifecycle policies to temporary extraction files and old
  generations while retaining legal/operational holds.
- Use Redis for hot answer/search caching with bounded TTL; never use cache as
  the only copy of a result or lock state.
- Defer CDN, OpenSearch, Kafka, and Kubernetes until measured traffic or
  availability requirements justify them.

## Security and compliance baseline

- Use a secret manager or Docker secrets rather than broad environment exposure.
- Enforce production `DEBUG=False`, secure cookies, explicit hosts, strict CORS,
  CSP review, rate limits, and upload malware/content validation.
- Encrypt PostgreSQL, object storage, backups, and transport.
- Define retention and deletion policy for PDFs, extracted text, embeddings,
  prompts, answers, audit events, and backups.
- Separate production, stage, and development datasets and credentials.
- Make restore/sanitization a tested workflow, not a manual file copy.

## Tooling and verification

- One local `make`/Taskfile entry point for check, unit tests, integration tests,
  Compose smoke, browser tests, and data-release validation.
- CI stages: formatting/static checks, Django checks, migrations, disposable
  PostgreSQL/S3 integration, browser/accessibility, image build, SBOM/scan,
  published-digest smoke, and release evidence.
- Add schema/data contract tests that fail when a model, embedding model,
  index format, or object-key contract changes without a migration record.
- Add synthetic canary queries for English, Marathi, mixed language, no-result,
  and source-link correctness after deployment.
- Use OpenTelemetry-compatible traces/metrics if the operational footprint
  justifies it; begin with Prometheus metrics already exposed by the app.

## Alternatives considered

| Direction | Verdict | Reason |
|---|---|---|
| Keep SQLite + local files indefinitely | Not recommended | Simple and cheap, but single-host durability and concurrency remain weak |
| PostgreSQL + pgvector + S3 | Recommended | Smallest architecture that improves durability, queryability, and retrieval provenance |
| PostgreSQL + external vector DB + S3 | Later option | Useful at larger scale, but adds network, cost, and operational failure modes now |
| Microservices immediately | Reject for now | Splits a still-coherent domain before scale or team boundaries demand it |
| Kubernetes immediately | Reject for now | Operational cost is disproportionate to current workload; retain Dokploy until evidence changes |

## Rollout order

1. Establish release/data evidence and restore drills.
2. Execute Plan 008 in isolated stage and migrate custody.
3. Execute Plan 009 with shadow retrieval and safe provider switching.
4. Add worker/outbox and observability improvements.
5. Remove legacy paths only after usage and rollback evidence.
6. Reassess managed services, CDN, vector infrastructure, and orchestration
   from measured load, RTO/RPO, cost, and team capacity.

## Done criteria

- A single documented architecture owns each state and artifact.
- Deployments are independent of local container filesystem state.
- Data recovery, index rebuild, and rollback are rehearsed.
- Search quality, source grounding, access control, latency, cost, and error
  budgets are measured in production-like tests.
- Operators can diagnose a failed release from immutable evidence without
  reading raw secrets or manually copying production files.
