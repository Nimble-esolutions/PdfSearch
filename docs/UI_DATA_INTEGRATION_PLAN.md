Status: Historical/Completed
Audience: Developer, Release, Operations
Owner: FlowDocs maintainers
Last verified: 2026-07-24
Canonical source: docs/UI_DATA_INTEGRATION_PLAN.md
Supersedes: None

# Historical UI And Data Integration Plan

## Scope

This plan covers the historical `24june2026` branch, current `dev`, the latest
reconciled data generation, fresh-instance initialization, and first-admin
bootstrap. It is a planning document. It does not authorize UI integration,
data replacement, DNS changes, or active-volume mutation.

Audited revisions:

- Historical UI branch: `24june2026` at `834b955`.
- Current release base: `dev` at `2e1ca38`.
- Current reconciled generation: 253 PDF rows, 242 PDF files, 53 folders,
  8 users, 51 FAISS indexes, 8,753 vectors, schema `core:0017`.

## Audit Findings

The historical branch does not contain a separate missing asset bundle. The
important differences are templates, route names, view behavior, translations,
settings, startup assumptions, and data paths.

Keep from current `dev`:

- `/app/flowdocs` immutable application code boundary.
- `/app/data` persistent data boundary.
- SQLite-safe migration chain through `core:0012`.
- `DATA_ROOT` settings and startup flow.
- `/livez`, `/readyz`, CSRF protection, and protected PDF delivery.
- Current upload validation and loopback/Dokploy deployment contract.

Do not port wholesale from `24june2026`:

- Historical `settings.py`, `start.sh`, Dockerfile, Compose, or URL configuration.
- Historical full `views.py`.
- Historical direct media links.
- Historical `@csrf_exempt` behavior or unsafe `innerHTML` rendering.
- Historical administrative access-control changes.
- Historical `flowdocs/pdf_cache` or tracked database files.

Known UI contracts to resolve before porting presentation changes:

- Templates reverse `dashboard` while current folder routes use `dashboard_folder`.
- `folder_files.html` references stale `upload_pdf` route names.
- `answer.html` references stale `ask_question` route names.
- Search references use `ref.url` while current search data may expose `pdf.file.url`.
- Nullable `uploaded_by` is not handled consistently in templates.
- Historical and current Marathi catalogs have diverged message IDs.
- Protected PDF access needs ownership/role authorization, not only login.

## Brainstorming — Implemented

All brainstorming items marked N≥8 V≥8 F≥8 have been implemented:

- `[N9 V9 F10]` Release manifest binding UI SHA, OCI digest, Compose hash, data generation, and rollback pointer. ✅
- `[N9 V9 F10]` Content-addressed PDF/FAISS generation attached to the image release. ✅
- `[N8 V9 F9]` Git release ledger mapping every UI revision to a compatible data generation. ✅
- `[N8 V9 F10]` Disposable Dokploy stack using the candidate UI and frozen current data. ✅
- `[N8 V9 F10]` Promotion escrow keeping DNS untouched until every gate passes. ✅
- `[N9 V9 F10]` Explicit data-generation restore command instead of shipping production SQLite in `init/`. ✅
- `[N8 V8 F9]` One-time bootstrap superadmin command with generated credentials and consumption record. ✅
- `[N8 V8 F9]` Sanitized non-production seed plus separately custody-controlled production generation. ✅
- `[N8 V9 F9]` Versioned adapter translating current view context into historical template context. ✅
- `[N8 V8 F9]` Startup verification of image/data/index compatibility before readiness. ✅
- `[N8 V8 F9]` Frozen search-query replay against current FAISS/Chroma generation. ✅

### New UI Features (Post-Integration)

- Operations dashboard at `/dashboard/operations/` with data, lease, and metrics sub-pages.
- Health endpoints: `/health/data/`, `/health/lease/`, `/health/metrics/` (Prometheus).
- `config_inspect` management command for runtime configuration audit.
- Object-store capability probing via `object_store_capabilities.py`.
- Global writer status and handover ceremony via `global_writer.py`.

### Traps Avoided

- Cherry-picking the historical branch wholesale: incompatible settings, paths, routes, and security behavior.
- Replacing `init/db.sqlite3` with the latest production DB: secrets, PII, stale IDs, and non-reproducible deployment.
- Enabling `IMPORT_LEGACY_DATA=1`: bypasses reconciliation and index compatibility checks.
- Copying FAISS by filename: folder IDs and chunk ordering may differ.
- Creating multiple ad-hoc superadmins: privilege sprawl and unclear ownership.
- Embedding all production PDFs/indexes in the application image: oversized image and unsafe release coupling.

## Recommended Direction

The non-obvious but viable choice is a **release capsule plus blue-green validation**:

1. Keep current `dev` as the code base.
2. Port only reviewed historical presentation changes through current contexts and routes.
3. Generate a separate immutable data-generation artifact with checksums and compatibility metadata.
4. Validate the exact image plus exact data generation in an isolated Dokploy stack.
5. Promote the image/data pair only after route, UI, auth, PDF, FAISS, and search gates pass.
6. Leave DNS and canonical host routing as an independent later operation.

The load-bearing risk is schema, embedding, and chunk-order compatibility. The
first builder step is to freeze the current context/route contract and produce a
candidate manifest before changing templates.

## Fleet Work Plan

All lanes completed and merged to `dev` at `2e1ca38`:

1. UI lane: ✅ Dashboard/search/toast/translation presentation ported.
2. Route/security lane: ✅ URL reversals, stale links, PDF ownership, CSRF, and escaping fixed.
3. Data lane: ✅ Reconciled data manifest generated; DB/media/FAISS/Chroma validated.
4. Init lane: ✅ Production-like `init/` data replaced with sanitized seed; explicit restore tooling added.
5. CI lane: ✅ Entrypoint boot, seed-copy, migration, index, search, and OCI revision gates added.
6. Dokploy lane: ✅ Branch trigger, checkout SHA, immutable digest handoff, and rendered-config parity aligned.
7. Docs lane: ✅ This plan, release manifest contract, bootstrap policy, and rollback instructions maintained.

No lane modified the active production volume. Each lane returned a commit,
test evidence, compatibility notes, and rollback impact.

## Data Generation Contract

Every generation must record:

- `release_id`, Git SHA, image digest, Compose hash, and generation command.
- Database hash, schema leaf, migration list, table counts, integrity result.
- PDF count, byte total, path, SHA-256, and missing-file classification.
- FAISS path, SHA-256, dimension, vector count, folder mapping, and embedding model.
- Chroma collection/model metadata when used.
- Source custody snapshots, conflict decisions, compatible app/schema/index range.
- Previous known-good generation and rollback reference.

`init/` must contain only sanitized, reproducible bootstrap data. Production
data belongs in the explicit release-generation vault and must never be copied
into Git or the image.

## Superadmin Bootstrap Policy

The production database already contained elevated identities. A one-time
bootstrap identity was provisioned separately with a random password and is not
part of the data generation or this repository.

Rules:

- Inspect existing admin/superadmin identities before creating another.
- Create only through an explicit, audited one-time command.
- Never place the password in Git, fixtures, logs, audit ledgers, or image layers.
- Deliver credentials only to the operator through the approved secure channel.
- Require immediate password rotation and disable unused elevated accounts.
- Keep `CREATE_SUPERUSER=0` after bootstrap.

## Acceptance Gates — Completed

All acceptance gates passed:

- Historical UI changes reviewed file-by-file against current `dev`. ✅
- Current route names and protected PDF links resolve in every affected template. ✅
- Authenticated and unauthenticated PDF access tests pass. ✅
- HTML escaping, CSRF, role authorization, and null ownership tests pass. ✅
- Fresh empty-volume boot passes migrations, seed behavior, and readiness. ✅
- Latest data-generation restore passes SQLite, media, FAISS, and search checks. ✅
- Exact candidate image has matching Git SHA/OCI revision and locked dependencies. ✅
- Dokploy checkout/image/Compose/data parity passes. ✅
- Preview blue-green smoke passes without DNS changes. ✅
- Rollback to the previous image/data generation passes in an isolated test. ✅
