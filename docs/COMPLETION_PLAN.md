Status: Active
Owner: FlowDocs maintainers
Created: 2026-07-23

# PdfSearch Completion Plan — Closing All Partial / Plan-Only / Pending / Gaps

This document enumerates every incomplete work item identified in the
2026-07-23 audit, assigns each to a logically-named branch, breaks it into
cherry-pickable commits, and specifies the verification gates that must pass
before a PR is opened against `dev`.

## Universal Workflow (applies to every item below)

1. **Branch from `dev`:** `git checkout dev && git pull origin dev && git checkout -b <branch>`
2. **Commit locally first, in logical chunks** (app, tests, docs, migration
   each separate where possible). Never leave uncommitted work at the end of a
   session.
3. **Run the narrowest verification gate** (see per-item gates) before pushing.
4. **Push and open a PR into `dev`:** `gh pr create --base dev --head <branch>`
5. **Merge only when CI is green** and the PR has been reviewed.
6. **Update this plan** — mark the item `done` with the PR number and merge SHA.

Branch naming convention: `<type>/<scope>` where type is
`feat|fix|docs|refactor|test|chore`.

---

## Item 1 — Generation Promotion, Rollback, and Retention

**Branch:** `feat/generation-promotion-rollback`
**Priority:** High
**Status:** Partial — `ArtifactGeneration` model and `sync_active_generation` /
`stage_generation` exist, but there is no promote-to-active, rollback-to-prior,
or retention-policy mechanism. The cockpit only lists generations.

### Commits

1. `feat(models): add promoted_at index and retention fields to ArtifactGeneration`
   - Add `retention_policy` (CharField: `keep_all|keep_last_n|age_based`).
   - Add `expires_at` (DateTimeField, nullable).
   - Add `superseded_by` (self-FK, nullable).
   - Migration `0014_generation_retention_fields`.

2. `feat(maintenance): add promote_generation and rollback_generation job kinds`
   - New `MaintenanceJob.KIND_CHOICES`: `promote_generation`,
     `rollback_generation`, `purge_generation`.
   - `promote_active_generation(generation_id, requested_by)`: atomically mark
     the prior active generation `superseded_by=<new>`, set the new one to
     `active`, stamp `promoted_at`. Requires the generation to be `validated`.
   - `rollback_to_generation(generation_id, requested_by)`: re-stage the
     named generation and promote it, recording the rollback chain.
   - `purge_expired_generations()`: delete generations past their retention
     window that are not `active` and not the immediate predecessor.

3. `feat(views): add promote / rollback / purge endpoints`
   - `POST /dashboard/generation/<id>/promote/`
   - `POST /dashboard/generation/<id>/rollback/`
   - `POST /dashboard/generation/<id>/purge/`
   - All `superadmin_required`, all queue durable jobs (no sync work).

4. `feat(templates): generation lifecycle controls in cockpit`
   - Show promote / rollback buttons per generation row.
   - Show retention badge and expiry date.
   - Confirm-dialog for rollback (irreversible-ish).

5. `test(maintenance): cover promote, rollback, purge, and retention expiry`
   - Unit tests for state transitions and guards.
   - Integration test: sync → validate → promote → rollback → purge.

6. `docs: document generation lifecycle and retention policy`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.maintenance_tests'
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py makemigrations --check --dry-run'
```

---

## Item 2 — ArtifactValidation and MaintenanceAuditEvent Models

**Branch:** `feat/audit-validation-models`
**Priority:** High
**Status:** Plan only — neither model exists. `MaintenanceJob` tracks
per-item results but there is no structured validation record or audit trail
for who did what when.

### Commits

1. `feat(models): add ArtifactValidation model`
   - FK to `ArtifactGeneration`.
   - `validation_type` (CharField: `manifest|sha256|counts|faiss|search`).
   - `status` (CharField: `passed|failed|warned`).
   - `details` (JSONField).
   - `validated_by` (FK to user, nullable).
   - `created_at` (auto).

2. `feat(models): add MaintenanceAuditEvent model`
   - FK to `MaintenanceJob`.
   - `event_type` (CharField: `queued|claimed|item_completed|item_failed|
     cancelled|retried|promoted|rolled_back|purged`).
   - `actor` (FK to user, nullable — system events have null actor).
   - `payload` (JSONField).
   - `created_at` (auto).
   - Migration `0015_audit_validation_models`.

3. `feat(maintenance): emit audit events in run_job and lifecycle functions`
   - Wrap every state transition in `run_job`, `promote_active_generation`,
     `rollback_to_generation`, `purge_expired_generations` with
     `MaintenanceAuditEvent.objects.create(...)`.
   - Wrap validation calls in `sync_active_generation` and `stage_generation`
     with `ArtifactValidation` records.

4. `feat(views): expose audit trail in cockpit`
   - `GET /dashboard/maintenance/<job_id>/audit/` → JSON timeline.
   - `GET /dashboard/generation/<id>/validations/` → JSON validation list.

5. `test(audit): verify events fire on every state transition`
   - Assert event count and type for a full job lifecycle.
   - Assert validation records created on sync and stage.

6. `docs: document audit event schema and retention`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.audit_tests'
```

---

## Item 3 — Bulk Filter Workspace

**Branch:** `feat/bulk-filter-workspace`
**Priority:** High
**Status:** Partial — `bulk_maintenance` view accepts `folder_ids` but there
is no filter UI to select by category, subject, indexed status, keyword, or
date range. The operator must manually check folders.

### Commits

1. `feat(views): add filter_params parsing to bulk_maintenance`
   - Accept `category`, `subject`, `indexed` (true/false),
     `keywords` (comma list), `uploaded_after`, `uploaded_before`.
   - Build a `Q` object chain and apply to `PDFFile.objects.filter(...)`.

2. `feat(templates): bulk filter panel in dashboard`
   - Collapsible filter form above the folder list.
   - Multi-select for categories and subjects.
   - Checkbox for indexed/unindexed.
   - Date range inputs.
   - "Select all matching" button that checks folder checkboxes.

3. `feat(views): add filter preview endpoint`
   - `GET /dashboard/maintenance/preview/` → JSON count of matching PDFs
     for the current filter, so the operator sees impact before queuing.

4. `test(views): cover filter combinations and preview counts`

5. `docs: document bulk filter workspace`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.bulk_filter_tests'
```

---

## Item 4 — Live Job Drawer (SSE / Polling)

**Branch:** `feat/live-job-drawer`
**Priority:** High
**Status:** Plan only — the cockpit shows the last 8 jobs as a static list.
There is no live progress, no auto-refresh, no per-item breakdown.

### Commits

1. `feat(views): add job status JSON endpoint`
   - `GET /dashboard/maintenance/<job_id>/status/` → JSON with
     `status`, `total_items`, `completed_items`, `failed_items`,
     `error_summary`, `items` (per-item status array).

2. `feat(views): add active jobs list endpoint`
   - `GET /dashboard/maintenance/active/` → JSON array of all
     `queued|running|cancel_requested` jobs with progress percentages.

3. `feat(templates): slide-in job drawer component`
   - Fixed-position drawer on the right edge of the dashboard.
   - Toggle button (bell icon with active-job badge count).
   - Polls `/dashboard/maintenance/active/` every 5 seconds.
   - Expands a job to show per-item progress bars.
   - Cancel button per running job.

4. `feat(templates): SSE fallback for modern browsers`
   - Optional `EventSource` on `/dashboard/maintenance/stream/` that
     pushes job updates. Falls back to polling if SSE unsupported.
   - SSE view uses async streaming response with Redis pub/sub or
     simple polling-emit pattern.

5. `test(views): cover status and active endpoints`

6. `docs: document job drawer and SSE contract`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.job_drawer_tests'
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python /app/scripts/ci/admin_ui_smoke.py'
```

---

## Item 5 — Document Lifecycle Enum and Transitions

**Branch:** `feat/document-lifecycle`
**Priority:** Medium
**Status:** Gap — `PDFFile` has `indexed` (boolean) but no lifecycle state.
There is no way to distinguish "uploaded but not processed", "processing",
"processed", "deprecated", or "archived".

### Commits

1. `feat(models): add lifecycle field to PDFFile`
   - `lifecycle` (CharField: `uploaded|processing|ready|deprecated|archived`).
   - Default `uploaded` for existing rows (data migration).
   - Migration `0016_pdffile_lifecycle`.

2. `feat(maintenance): update lifecycle in run_job`
   - `reindex_*` sets `processing` at start, `ready` on success.
   - `validate` does not change lifecycle (read-only check).
   - Add `deprecate_pdf` and `archive_pdf` helper functions.

3. `feat(views): add deprecate and archive endpoints`
   - `POST /pdf/<id>/deprecate/` — sets `deprecated`, hides from search.
   - `POST /pdf/<id>/archive/` — sets `archived`, hides from search and dashboard.
   - `POST /pdf/<id>/restore/` — sets `uploaded`, requeues reindex.

4. `feat(search): exclude deprecated and archived from search results`
   - Update `search_pdfs_fast` and folder listing queries.

5. `feat(templates): lifecycle badge and actions in folder view`
   - Color-coded badge per PDF row.
   - Deprecate / Archive / Restore buttons (role-gated).

6. `test(models): cover lifecycle transitions and search exclusion`

7. `docs: document document lifecycle state machine`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.lifecycle_tests'
```

---

## Item 6 — SEO / AEO Optimization Plan Implementation

**Branch:** `feat/seo-aeo-implementation`
**Priority:** Medium
**Status:** Plan only — `docs/SEO_AEO_OPTIMIZATION_PLAN.md` is a draft.
No metadata, structured data, robots.txt, sitemap.xml, or static copy has
been implemented. Four approval questions remain open.

### Prerequisite

Resolve the four approval questions in the plan document before implementing.
Mark each answer in the plan and change status from `Draft for approval` to
`Approved`.

### Commits

1. `feat(templates): add metadata baseline to search.html and base.html`
   - `<title>`, meta description, canonical URL, OG tags, Twitter card.
   - Legal disclaimer in metadata-safe form.

2. `feat(views): add robots.txt and sitemap.xml routes`
   - `GET /robots.txt` — allow `/`, `/search/`, `/livez`, `/readyz`;
     disallow `/dashboard/`, `/register/`, `/login/`, `/pdf/*/view/`.
   - `GET /sitemap.xml` — static list of public URLs with lastmod.

3. `feat(templates): add JSON-LD structured data`
   - `WebSite`, `Organization`, `SearchAction` (if approved),
     `FAQPage` (static guidance only).

4. `feat(templates): add static "What this service does" section`
   - HTML (not JS-only) explanation of corpus, disclaimer, references.

5. `feat(templates): improve content quality and accessibility`
   - Replace vague alt text with descriptive labels.
   - Add `rel="noopener noreferrer"` to external `target="_blank"` links.
   - Optimize hero image dimensions and alt text.

6. `feat(ci): add validate_public_html script`
   - Checks title, description, canonical, OG, JSON-LD parse, no generated
     answer in initial HTML, external link rel, disclaimer presence.

7. `feat(views): add hreflang alternates if Marathi/English canonical approved`

8. `test(ci): cover validate_public_html checks`

9. `docs: update SEO_AEO_OPTIMIZATION_PLAN.md status to Implemented`

### Gate

```bash
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py check'
docker compose -f docker-compose.dev.yml exec -T web sh -lc \
  'cd /app/flowdocs && python manage.py test core.tests.seo_tests'
curl -fsS http://localhost:8000/ | python /app/scripts/ci/validate_public_html.py
```

---

## Dependency Order

Items 1 and 2 share the `ArtifactGeneration` model and should be done in
sequence (1 before 2, since 2's `ArtifactValidation` FKs to generations and
the promote/rollback events feed the audit trail).

Items 3, 4, 5, and 6 are independent and can be parallelized across
worktrees or agents once their base is `origin/dev`.

Recommended merge order:

```
Item 2 (audit models)        ← after Item 1
Item 1 (promotion/rollback)  ← first
Item 5 (lifecycle)           ← independent
Item 3 (bulk filter)         ← independent
Item 4 (job drawer)          ← independent
Item 6 (SEO/AEO)             ← independent, after approval questions resolved
```

---

## Completion Tracking

| Item | Branch | PR | Merge SHA | Status |
|------|--------|----|-----------|--------|
| 1 | feat/generation-promotion-rollback | — | — | pending |
| 2 | feat/audit-validation-models | — | — | pending |
| 3 | feat/bulk-filter-workspace | — | — | pending |
| 4 | feat/live-job-drawer | — | — | pending |
| 5 | feat/document-lifecycle | — | — | pending |
| 6 | feat/seo-aeo-implementation | — | — | pending (blocked on approval) |