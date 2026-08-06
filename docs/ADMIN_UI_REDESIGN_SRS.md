# Admin UI Redesign — SRS & DRS

**Status**: Historical
**Created**: 2026-07-24  
**Last reviewed**: 2026-07-25
**Audit base**: `dev` at `a5b405e` (PR #56 merged)
**Current replacement**: [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md) and [`AI_SAHAKAR_ADMIN_USER_GUIDE.md`](AI_SAHAKAR_ADMIN_USER_GUIDE.md)

> This is a historical planning record, not an active implementation plan.
> Several items below were delivered or superseded by the Operations Cockpit.
> Do not repeat its conflict-marker or dead-file cleanup instructions without
> rechecking the target branch. Use the current replacement above.

---

## Phase 0 — Critical Fixes (1 branch, 1 commit)

### CRITICAL: Git conflict marker in production template

`dashboard.html:622` has an unmerged `>>>>>>>` conflict marker. This is a JavaScript syntax error.

**File**: `flowdocs/core/templates/dashboard.html`
**Fix**: Remove conflict marker, verify IIFE closure.

### Dead code removal

| File | Status | Action |
|------|--------|--------|
| `home.html` | View has NO URL route | Delete |
| `folder_files.html` | No view references it | Delete |
| `home_view` in `views.py:843` | No URL route | Delete or comment out |

---

## Phase 1 — Settings & Configuration Page (branch: `feat/settings-page`)

### 1.1 New template: `dashboard_settings.html`

**Route**: `GET /dashboard/settings/` → `settings_view`
**Auth**: `@superadmin_required`
**Extends**: `base.html`

**Features**:

1. **Read-only environment identity viewer** (moved from `dashboard_operations.html`):
   - APP_ENV, Dataset, Authoritative, Restore Source, Production Source
   - Deployment ID, Instance ID, Build Digest, Release
   - Backup Role, Sync Mode, Data Mode, Side Effects, Scheduler

2. **S3 vault status panel**:
   - Vault enabled/disabled
   - Endpoint reachable (live probe)
   - Bucket exists (live probe)
   - Object count, last sync timestamp
   - Conditional ops supported (from `verify_object_store_capabilities`)

3. **Editable feature flags** (requires `SETTINGS_EDIT_ENABLED=1` env flag):
   - PUBLIC_SEARCH_ENABLED (toggle)
   - DISPLAY_SERVICE_FOOTER (toggle)
   - PUBLIC_SEARCH_RATE_LIMIT (number input)
   - PUBLIC_SEARCH_RATE_WINDOW (number input)
   - PUBLIC_SEARCH_MAX_WORDS (number input)
   - MAINTENANCE_SCHEDULER_ENABLED (toggle)
   - BACKUP_SYNC_MODE (select)
   - DATA_MODE (select, readonly in production)
   - EXTERNAL_SIDE_EFFECTS_MODE (select)

4. **Save mechanism**:
   - Settings stored in Django database model `SiteSetting(key, value, updated_by, updated_at)`
   - `SETTINGS_EDIT_ENABLED=0` (default): all controls disabled, read-only display
   - `SETTINGS_EDIT_ENABLED=1`: controls enabled, save button active
   - On save: writes to DB, triggers `django.core.cache` invalidation
   - Views read from cache, fall back to DB, fall back to env var

### 1.2 New model: `SiteSetting`

```python
class SiteSetting(models.Model):
    key = models.CharField(max_length=128, unique=True)
    value = models.TextField()
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 1.3 Auto-detection of changes

- `settings.py` reads SiteSetting from DB on startup (after env vars)
- `SiteSetting` overrides take precedence over env vars when set
- Each view that reads a feature flag checks: DB → cache → env var
- A `post_save` signal on SiteSetting invalidates the cache
- New health endpoint: `GET /health/config/` → `{"config_hash": "...", "last_updated": "..."}`

### 1.4 Migration

- `0019_sitesetting.py` → creates SiteSetting table

### 1.5 URL additions

```python
path("dashboard/settings/", settings_view, name="settings"),
path("dashboard/settings/save/", save_settings, name="save_settings"),
path("health/config/", config_health, name="health_config"),
```

---

> Historical design only: the dedicated S3 page and direct bulk mutations are
> retired. Current operators use DataOps v3 **Data protection**, with local
> work under **Search maintenance**. The `vaultops` package remains internal
> compatibility infrastructure, not an operator product.

## Phase 2 — S3 & Bulk Operations Dashboard (historical)

### 2.1 New template: `dashboard_s3ops.html`

**Route**: `GET /dashboard/operations/s3/` → `s3_operations_view`
**Auth**: `@superadmin_required`
**Extends**: `base.html`

**Sections**:

1. **Vault Health Panel**:
   - Endpoint reachability (green/red indicator)
   - Bucket exists (yes/no)
   - Object count and total size
   - Conditional ops supported (checkmarks)
   - Last sync time ago

2. **Sync Operations Panel**:
   - "Sync Active to S3" button with confirmation dialog
   - Preview: PDF count, FAISS count, estimated size before sync
   - Progress bar during sync (poll job status)
   - Last sync result (success/fail with timestamp)

3. **Restore Operations Panel**:
   - Available generations dropdown (from S3 manifests + local DB)
   - Generation metadata (created, PDF count, FAISS count, size)
   - "Pull to Staging" button
   - Progress during restore (download, verify, sanitize, rehearse)
   - Staged generations list with age

4. **Generation Lifecycle Table** (moved from dashboard.html):
   - All generations with status badges
   - Promote, Rollback, Purge actions per generation
   - Purge Expired bulk button
   - Validation details expandable per generation

5. **Writer Lease Panel**:
   - Current lease status (held/not_held)
   - Lease holder instance ID
   - Lease expiry countdown
   - Force-release button (with confirmation dialog)

6. **Manifest Comparison** (optional, can be Phase 2b):
   - Select two generations
   - Side-by-side file list
   - PDFs added/removed/changed
   - Size delta

### 2.2 URL additions

```python
path("dashboard/operations/s3/", s3_operations_view, name="s3_operations"),
path("dashboard/operations/s3/probe/", s3_probe, name="s3_probe"),
path("dashboard/operations/s3/sync/", s3_sync, name="s3_sync"),
path("dashboard/operations/s3/restore/", s3_restore, name="s3_restore"),
path("dashboard/operations/s3/release-lease/", s3_release_lease, name="s3_release_lease"),
```

### 2.3 Remove from dashboard.html

- Generation Lifecycle table → moved to S3 dashboard
- Bulk Data Operations panel → moved to S3 dashboard  
- Pull Generation to Staging → moved to S3 dashboard
- Active Jobs sidebar → keep on dashboard (reduced)

---

## Phase 3 — Admin UI Declutter & Restructure (branch: `feat/admin-ui-declutter`)

### 3.1 Split dashboard.html into includes

**Current**: 625-line monolithic template
**Target**: Main template ~200 lines + 7 includes

```django
{# dashboard.html — main structure #}
{% extends "base.html" %}
{% include "includes/_cockpit_metrics.html" %}
{% include "includes/_category_yard.html" %}
{% include "includes/_recent_intake.html" %}
{% include "includes/_cockpit_sidebar.html" %}  {# maintenance jobs, dispatch, index debt #}
{% include "includes/_job_drawer.html" %}
```

### 3.2 Add sidebar navigation to base.html

Replace flat horizontal navbar with a collapsible left sidebar:

```
┌─ Sidebar ──────────────────────────┐
│ 📊 Dashboard                       │
│ 🔍 Search                          │
│ ───────────────────                │
│ ⚙️ Operations                       │
│   ├─ Settings                      │
│   ├─ Environment                   │
│   └─ S3 / Vault                    │
│ ───────────────────                │
│ 👥 Users & Access                  │
│   ├─ User List                     │
│   └─ Add User                      │
│ ───────────────────                │
│ 🌐 EN / मराठी                      │
│ 👤 username (role)                 │
│ 🚪 Logout                          │
└────────────────────────────────────┘
```

### 3.3 Add breadcrumbs to base.html

```django
{% block breadcrumbs %}
  <nav aria-label="breadcrumb">
    <ol class="breadcrumb">
      <li><a href="{% url 'dashboard' %}">Dashboard</a></li>
      {% block breadcrumb_items %}{% endblock %}
    </ol>
  </nav>
{% endblock %}
```

### 3.4 Unify shell: search.html extends base.html

- Make `search.html` extend `base.html` with `{% block body_class %}public-search{% endblock %}`
- Move inline CSS to `static/main/css/search.css`
- Move inline JS to `static/main/js/search.js`
- Make search auth-aware: hide "Login" when `user.is_authenticated`
- Remove standalone header/footer duplication

### 3.5 Extract dashboard JS

- Move 152 lines of inline JS from `dashboard.html` to `static/main/js/dashboard.js`
- Move filter sync, preview count, job drawer poll to dedicated modules

### 3.6 Fix modal pattern

- Replace per-PDF inline modals with single reusable `#actionModal` driven by `data-*` attributes
- 50 PDFs = 1 modal div, not 150 modal divs in DOM

### 3.7 Consistent card styling

- `dashboard_operations.html`: use single `.card-header--diagnostic` class
- Remove 5 different bg-* classes from adjacent cards

---

## Phase 4 — DPDA Compliance & Legal Pages (branch: `feat/dpda-compliance`)

### 4.1 New templates

| Template | Route | Content |
|----------|-------|---------|
| `privacy.html` | `GET /privacy/` | Privacy policy per DPDA 2023 |
| `terms.html` | `GET /terms/` | Terms of service |
| `data_policy.html` | `GET /data-policy/` | Data handling & retention policy |
| `cookie_consent.html` | Include in base | Cookie consent banner |

### 4.2 DPDA Compliance Checklist

- [ ] Privacy notice: what data is collected, purpose, legal basis
- [ ] Consent mechanism: explicit consent for data processing
- [ ] Data retention policy: how long search queries, user accounts, uploaded PDFs are retained
- [ ] Data subject rights: access, correction, erasure, grievance redressal
- [ ] Grievance officer contact: name, email, phone
- [ ] Third-party disclosure: OpenAI API usage disclosure
- [ ] Cross-border data transfer: OpenAI servers (US) disclosure
- [ ] Security safeguards: encryption, access controls
- [ ] Cookie policy: types of cookies, purpose, consent

### 4.3 UI Integration

- **Footer links**: Privacy Policy | Terms of Service | Data Policy | Grievance
- **Login page**: "By logging in, you agree to our Terms of Service and Privacy Policy"
- **Register page**: Consent checkbox "I agree to the Terms of Service and Privacy Policy"
- **Search page (public)**: "By using this service, you agree to our Privacy Policy" banner
- **Cookie consent banner**: "This site uses cookies for authentication and security" with Accept/Decline

### 4.4 URL additions

```python
path("privacy/", privacy_view, name="privacy"),
path("terms/", terms_view, name="terms"),
path("data-policy/", data_policy_view, name="data_policy"),
path("grievance/", grievance_view, name="grievance"),
```

---

## Phase 5 — Search Page Improvements (branch: `feat/search-ux-improvements`)

### 5.1 CSS/JS extraction
- Move 180 lines inline CSS → `static/main/css/search.css`
- Move 250 lines inline JS → `static/main/js/search.js`

### 5.2 Auth awareness
- Show admin navbar when `user.is_authenticated`
- Hide "Admin Login" link when already logged in
- Add "Back to Dashboard" link for authenticated users

### 5.3 DPDA integration
- Privacy policy link in footer
- Disclaimer banner about AI-generated answers
- Data handling notice

---

## Phase 6 — Follow-up Fixes (branch: `fix/activation-lock-pid-g12` and `fix/s3-orphan-cleanup-g13`)

### 6.1 G12: Activation lock PID in containerized env

- Make Redis heartbeat PRIMARY authority for liveness
- PID check becomes advisory-only (logged, not gating)
- Add absolute maximum lock duration (10 min) regardless of process state
- File: `flowdocs/core/activation_journal.py`

### 6.2 G13: Partial S3 upload cleanup on crash recovery

- On maintenance worker recovery, detect orphaned `sync_generation` jobs
- Release held writer lock before re-queueing
- List and optionally clean up orphaned S3 objects from partial uploads
- File: `flowdocs/core/management/commands/run_maintenance_jobs.py`

---

## Branch Map & Execution Order

```
dev
├── fix/critical-git-conflict-dead-code   (Phase 0 — merge FIRST)
├── feat/settings-page                     (Phase 1 — depends on Phase 0)
├── feat/s3-operations-dashboard          (Phase 2 — depends on Phase 1)
├── feat/admin-ui-declutter                (Phase 3 — depends on Phase 2)
├── feat/dpda-compliance                   (Phase 4 — independent, can parallel)
├── feat/search-ux-improvements            (Phase 5 — depends on Phase 3)
├── fix/activation-lock-pid-g12            (Phase 6a — independent)
└── fix/s3-orphan-cleanup-g13              (Phase 6b — independent)
```

## Naming Conventions

- Branches: `feat/<feature-name>`, `fix/<fix-name>`, `docs/<doc-name>`
- Commits: `<type>(<scope>): <description>` per conventional commits
- PRs: Single-purpose, self-contained, green CI before merge
- Each PR includes its own migration if model changes exist

## Acceptance Criteria Per Phase

| Phase | Criteria |
|-------|----------|
| 0 | Git conflict removed, dead templates deleted, CI passes |
| 1 | Settings page loads, shows env identity, feature toggles work with SETTINGS_EDIT_ENABLED flag |
| 2 | S3 vault health displays live, sync/restore work from new page, old dashboard sections removed |
| 3 | Sidebar navigation works, includes rendered, modals single-instance, search page unified, breadcrumbs shown |
| 4 | Privacy/Terms/Data pages accessible, cookie consent banner shows, footer links work |
| 5 | search.css and search.js extracted, auth-aware nav, DPDA links in footer |
| 6a | Activation lock uses Redis-primary liveness, absolute max duration enforced |
| 6b | Orphaned sync jobs release writer lock on recovery, orphaned S3 objects listed |
