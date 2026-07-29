# PdfSearch implementation roadmap

Reviewed against commit `f7d0536` on 2026-07-28. These files are handoff
contracts for future developers and AI agents. Read the selected plan fully,
run its drift check, and stop when a stated assumption is false.

## Status vocabulary

- `DONE`: implemented decision or verified historical work; do not rebuild.
- `TODO`: approved work that has not been implemented.
- `RECONCILE`: implementation exists; verify it, close residual gaps, then mark
  `DONE`. Do not replace it wholesale.
- `ACTIVE GATE`: recurring verification contract applied to every relevant PR.
- `BLOCKED`: a named prerequisite or operator decision is missing.
- `REJECTED`: explicitly considered and not worth implementing now.

## Execution order and status

| Plan | Purpose | Priority | Effort | Depends on | Status |
|---|---|---:|---:|---|---|
| 001 | Registrar domain and visual identity record | P1 | S | — | DONE |
| 002 | Reconcile runtime configuration registry and settings center | P1 | S/M | — | RECONCILE |
| 003 | Reconcile vault health, generations, jobs, and operator actions | P1 | M | 006 gate | RECONCILE |
| 004 | Reconcile breadcrumbs, dashboard, and category workbench | P1 | S/M | 006 gate | RECONCILE |
| 005 | Reconcile and protect the Hallmark Civic Workbench | P1 | S/M | 001, 006 gate | RECONCILE |
| 006 | Cross-surface verification and release evidence | P1 | M | — | ACTIVE GATE |
| 007 | Reconcile public source references, sharing, and evidence UX | P1 | S/M | 005, 006 gate | RECONCILE |
| 011 | Establish immutable evidence packs and reconciliation | P1 | M | 006 gate | TODO |
| 012 | Add compatibility seams and agent-safe capabilities | P1 | M | 011 | TODO |
| 013 | Restore task-first Operations Cockpit hierarchy | P1 | M | — | DONE |
| 014 | Correct maintenance capability gates and filter validation | P1 | M | 013 | DONE |
| 015 | Make Workbench readiness failures actionable | P1 | M | 013, 014 | DONE |
| 016 | Bound maintenance read models and verify full workflow | P2 | M | 014, 015 | DONE |
| 017 | Human-centred operator evidence | P1 | M | 015, 016 | DONE |
| 018 | Complete the UI reason presentation registry | P1 | M | — | DONE |
| 019 | Redact operator technical evidence | P1 | M | 018 | DONE |
| 020 | Enforce Marathi operator-copy parity | P1 | M | 018 | DONE |
| 021 | Strengthen rendered/accessibility anti-slop gates | P1 | M | 018, 019, 020 | DONE |
| 022 | Reconcile roadmap and stacked-PR readiness | P2 | S | 018, 019, 020, 021 | DONE |
| 023 | Separate Documents & Search from advanced Vault & Recovery | P1 | M | 013–022 | RECONCILE |
| 008 | Separate object custody; adopt PostgreSQL only if its gate passes | P1 | L | 011, 012 | TODO |
| 009 | Normalize document/retrieval architecture and benchmark hybrid search | P1 | L | 011, 012; 008 if PostgreSQL wins | TODO |
| 010 | Evolve the modular platform after the preceding decisions | P2 | L | 008, 009, 011, 012 | TODO |

## Dependency graph

```text
001 domain/UI record ───────────────┐
                                    ├─> 005 UI reconciliation ─> 007 evidence UX
006 recurring verification gate ───┼─> 002/003/004 reconciliation
                                    └─> 011 evidence packs
                                           │
                                           v
                                    012 compatibility seams
                                           │
                                           v
                                013 cockpit hierarchy
                                           │
                                           v
                                014 maintenance gates
                                           │
                                           v
                                015 readiness remediation
                                           │
                                           v
                                016 bounded read model
                                      │              │
                                      v              v
                           008 custody/database    009 retrieval benchmark
                                      └──────┬───────┘
                                             v
                                      010 platform evolution

015 readiness remediation ─> 017 operator evidence ─> 018 registry
                                                     ├─> 019 redaction
                                                     └─> 020 Marathi parity
018 + 019 + 020 ─> 021 enforcement ─> 022 stack reconciliation
022 ─> 023 Documents & Search / Vault & Recovery journey boundary
```

Plan 011 comes before database replacement because recovery must not depend on
the migration succeeding. Plan 012 comes before provider changes because the
current public API needs a compatibility adapter. Plans 008 and 009 are
separate decisions: object custody can change without PostgreSQL, and retrieval
can improve without changing the relational database.

## Universal execution contract

Every executor must:

1. Read `AGENTS.md`, `CONTRIBUTING.md`, and
   `docs/design/AI_SAHAKAR_UI_CONTRACT.md` when UI is in scope.
2. Create a feature branch from current `dev`; never commit directly to
   `dev` or `main`.
3. Run the plan's drift check before editing.
4. Preserve unrelated user changes and the protected backend files named in
   `AGENTS.md` and the project brief.
5. Commit logical, cherry-pickable changes, verify before push, open a PR into
   `dev`, and never merge without operator authorization.
6. Record exact commands, failures, migration/data impact, and rollback in the
   PR. Do not claim browser, CI, or deployment validation from screenshots.

Baseline checks used by the plans:

```bash
git diff --check
python manage.py check
python manage.py makemigrations --check --dry-run
node --check flowdocs/core/static/main/js/search.js
npx playwright test
```

For image/runtime changes:

```bash
docker build -t pdfsearch-ci:source .
PDFSEARCH_IMAGE=pdfsearch-ci:source REDIS_IMAGE=redis:7-alpine \
  bash scripts/ci/run_compose_smoke.sh
```

Use focused tests first. The Compose smoke gate is required when entrypoints,
dependencies, migrations, runtime data, static assets, or image behavior
changes.

## Non-negotiable product and safety constraints

- Keep Django and the Civic Knowledge Workbench visual language unless a human
  explicitly approves a product-direction change.
- Preserve authentication, authorization, CSRF, query limits, public folder
  scope, protected PDF access, English/Marathi behavior, and source visibility.
- Preserve public response behavior through a compatibility adapter while
  allowing internal schemas and providers to evolve.
- Never display or commit secrets, raw environment values, storage keys,
  production PDFs, databases, embeddings, or indexes.
- Animation is never evidence of backend progress or success.
- Every data/index generation is immutable, validated, promotable, and
  rollbackable; partial generations remain invisible.
- Database, object storage, index, model provider, queue, and orchestration
  choices require measured gates and an owner.

## Considered and rejected for the current scale

- Immediate microservices, Rust/Go gateway, Kafka, or Kubernetes: operational
  complexity is not justified by measured traffic or team boundaries.
- Adopting Django Ninja and Django REST Framework together: overlapping API
  abstractions would create drift; select one only after Plan 012's spike.
- Adding Celery because it is present in dependencies: measure the existing
  maintenance-job runner first.
- Treating Redis as durable queue, audit ledger, or custody store: rejected;
  Redis remains cache, rate-limit, and short-lived coordination infrastructure.
- Semantic-only retrieval or an external vector database by default: legal and
  civic queries require a measured lexical/semantic hybrid benchmark first.
- Replacing the server-rendered UI with a SPA: no demonstrated user or
  operational benefit; preserve small, progressively enhanced JavaScript.

## 2026-07-28 local visual audit findings

- Repeated maintenance CTAs and a large pre-task chrome area delay the actual
  Dashboard/Workbench work surface; captured in Plan 013.
- `capability_reasons()` uses an `if/elif` precedence chain, so independent
  embedding and force-reindex prerequisites are not evaluated as a matrix;
  inverted date ranges are also accepted; captured in Plan 014.
- The local Workbench exposes `profile_unavailable`, unknown authority, and
  disabled flags without a direct, typed remediation destination; captured in
  Plan 015.
- The maintenance read model scans local artifacts during render and stores
  unbounded matching PDF IDs in previews; end-to-end workflow coverage does not
  prove bounded behavior; captured in Plan 016.

These findings were observed against the local authenticated deployment at
`http://127.0.0.1:8000` and verified against source at commit `6c9a262`. The
visual audit did not modify application source or enable production mutations.

Plan 016 is `DONE`: PR #102 added the required disposable lifecycle covering
filtered preview, repair and reindex queueing, progress and retry, candidate
preparation, signed activation, English and Marathi search, rollback, and
post-rollback content custody.

Plan 017 is `DONE`. Plans 018–022 closed the 2026-07-28 completion-audit gaps:
all inventoried UI reasons resolve to authored guidance, browser projections
redact raw exception and audit messages, Marathi has reviewed copy for every
active registry string, and the anti-slop gate proves visible and accessible
machine-token containment across the Dashboard and every Workbench section.
