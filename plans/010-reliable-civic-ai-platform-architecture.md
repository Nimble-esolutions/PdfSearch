# Plan 010: Evolve PdfSearch into a reliable, cost-controlled civic AI platform

> **Executor instructions**: This is a target architecture plan, not permission
> to replace the current deployment wholesale. Prefer a modular monolith and
> incremental boundaries; preserve the existing search behavior, UI contract,
> and authentication while allowing implementation-level data custody to move
> behind the Plan 011/012 compatibility seams.
>
> **Drift check (run first)**:
>
> ```bash
> git diff --stat f742b59..HEAD -- \
>   flowdocs/core flowdocs/flowdocs/settings.py \
>   requirements*.txt docker-compose*.yml scripts/ci .github/workflows
> ```
>
> This plan is a direction record. Do not implement it as one PR.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED/HIGH
- **Depends on**: Plans 011, 008, and 009
- **Category**: direction / architecture
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: TODO, executed only as approved child plans

## Recommended target topology

```text
Users
  │ HTTPS / Traefik / CDN for static assets
  ▼
Django web: auth, admin, public search API, civic workbench
  │                 │
  │                 ├── Redis: cache, rate limits, short-lived locks (not durable jobs)
  │                 ├── Selected relational/search backend: transactional and retrieval data
  │                 └── S3/RustFS: PDFs, derived artifacts, immutable manifests
  ▼
Durable job ledger + worker: extract → chunk → embed → index → validate
  │
  └── Scheduler: retries, retention, backup verification, health probes
```

Keep web and worker as separate processes/images even if they remain in one
repository. Do not split into microservices until an independently scaled
boundary is proven necessary.

## Architecture principles

1. The selected relational backend is the transactional source of truth;
   PostgreSQL becomes that source only after the Plan 008 gate passes.
2. Object storage is the source of truth for binary documents and immutable
   generated artifacts.
3. Vectors and chunk metadata have explicit provenance and a rebuild path.
4. Redis is never the durable job ledger or authoritative data store.
5. Every data release is content-addressed, manifest-pinned, validated, and
   rollbackable.
6. Web requests do not perform OCR, embedding, bulk reindex, or restore loops;
   user-facing search remains synchronous until measured latency requires an
   asynchronous interaction.
7. UI state reflects durable backend state; animation never represents fake
   progress.
8. Environment, dataset, writer, and side-effect identity fail closed.

## Backend choices explicitly challenged

| Choice | Default decision | Evidence required to change it |
|---|---|---|
| SQLite | Keep as an interim writer if its single-writer envelope is sufficient | Concurrent-write errors, recovery objectives, or operational load that exceed the envelope |
| PostgreSQL | Adopt only through Plan 008's gate | Measured concurrency, PITR/RPO/RTO, and query/filter needs |
| FAISS | Keep only as a verified rebuildable snapshot or cache | Recall, p95 latency, memory, rebuild time, and access-filter evidence |
| `pgvector` | Candidate, not an assumption | Hybrid retrieval benchmark against the snapshot path |
| Chroma/external vector DB | Defer | Independent scale requirement and an owner for extra failure modes |
| Redis | Cache, rate limits, and ephemeral coordination | Never use it as custody, queue, or audit truth |
| Celery | Do not introduce by dependency presence alone | Measured job throughput need after current durable maintenance semantics are tested |
| Synchronous search | Preserve current UX | Measured p95 or timeout rate requiring an asynchronous user workflow |
| Django modular monolith | Keep | A team or scaling boundary that can be independently operated and tested |
| Rust/Go gateway, Kafka, Kubernetes | Defer | Proven bottleneck, availability requirement, or organizational need that offsets complexity |
| Same-host RustFS | Useful stage/interim target only | Off-host replication, restore drills, and a documented RPO/RTO |
| OpenAI embedding/chat providers | Wrap behind versioned provider contracts | Quality, price, residency, outage, and migration benchmarks; never silently mix models |

This table is a guardrail against architecture-by-fashion. A new dependency or
service must earn its place with a benchmark, failure-mode analysis, owner, and
rollback path.

## Backend and product improvements

### Modular Django monolith first

Partition code into explicit modules such as `identity`, `catalog`,
`documents`, `ingestion`, `retrieval`, `answers`, `operations`, and `storage`.
Keep domain services behind interfaces so storage and vector-provider changes
do not leak into views. Preserve the current public search response while
versioning any future API change.

### Durable ingestion and retrieval

Use the existing durable `MaintenanceJob`/item semantics first; do not add
Celery merely because it is already listed as a dependency. A transactional
outbox or PostgreSQL `SKIP LOCKED` job table is a later implementation option.
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
- Run migrations as a one-shot release job with advisory locking once
  PostgreSQL is adopted; preserve the current startup behavior until that
  release process has been proven in rehearsal.
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

- Start with evidence packs, measured hybrid retrieval, and the smallest
  relational backend that meets the gate; add PostgreSQL plus pgvector only
  when its recovery, concurrency, or filter benefits are demonstrated.
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

## Architecture decision process

Before creating a child implementation plan, record:

1. the measured problem and baseline;
2. at least two viable options, including “keep current”;
3. security, custody, failure, staffing, and monthly-cost impact;
4. migration and rollback;
5. owner and operating runbook;
6. acceptance/error budget and reassessment date.

Each child plan must use Plan 006 gates and include exact files/commands. This
direction plan does not authorize framework, database, provider, queue, or
orchestrator installation by itself.

## Alternatives considered

| Direction | Verdict | Reason |
|---|---|---|
| Keep SQLite + local files indefinitely | Not recommended | Simple and cheap, but single-host durability and concurrency remain weak |
| SQLite + immutable packs + read-only search snapshots | Recommended interim | Separates recovery custody immediately while preserving low operating cost |
| PostgreSQL + pgvector + S3 | Recommended medium-term | Improves durability, queryability, and retrieval provenance when the gate is met |
| PostgreSQL + external vector DB + S3 | Later option | Useful at larger scale, but adds network, cost, and operational failure modes now |
| Microservices immediately | Reject for now | Splits a still-coherent domain before scale or team boundaries demand it |
| Kubernetes immediately | Reject for now | Operational cost is disproportionate to current workload; retain Dokploy until evidence changes |

## Rollout order

1. Establish immutable evidence packs, reconciliation, and restore drills.
2. Add compatibility schemas and agent-safe capabilities while retaining the
   current UI/API adapters.
3. Move binary custody to object storage while retaining SQLite if the gate is
   not yet met.
4. Execute Plan 009 with hybrid shadow retrieval and safe provider switching.
5. Adopt PostgreSQL/pgvector only if Plan 008's evidence gate passes.
6. Add worker/outbox and observability improvements.
7. Remove legacy paths only after usage and rollback evidence.
8. Reassess managed services, CDN, vector infrastructure, and orchestration
   from measured load, RTO/RPO, cost, and team capacity.

## Done criteria

- A single documented architecture owns each state and artifact.
- Deployments are independent of local container filesystem state.
- Data recovery, index rebuild, and rollback are rehearsed.
- Search quality, source grounding, access control, latency, cost, and error
  budgets are measured in production-like tests.
- Operators can diagnose a failed release from immutable evidence without
  reading raw secrets or manually copying production files.

## STOP conditions and maintenance

Stop if a proposal has no measured bottleneck, no owner, no rollback, weakens
tenant/public access controls, or requires simultaneous database/storage/index/
API/UI replacement. Review this direction after Plans 008/009/011/012 produce
evidence, or when traffic, corpus, team size, RPO/RTO, provider requirements, or
regulation materially changes.
