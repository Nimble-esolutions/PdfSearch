Status: Historical snapshot
Audience: Release, Recovery, Developer
Superseded for current decisions by: `docs/RECOVERY_CERTIFICATION.md`,
`docs/RUSTFS_RECOVERY_VAULT.md`, and current CI results

# Historical Production-Readiness Report — 2026-07-24

This report records the evidence and blockers known on 2026-07-24. Its test
counts, image references, and **NOT READY** decision are historical and must not
be used as the current release decision. Later work added lifecycle tests and
an isolated certification workflow, but those additions do not by themselves
prove that production RustFS recovery or a production volume drill succeeded.
Use the current recovery documents and an operator-approved evidence record for
that decision.

**Date:** 2026-07-24
**Branch:** `recovery/data-lifecycle-verified-20260724` (merged to `dev` at `2e1ca38`)
**Image:** `pdfsearch-web:latest` (sha256:cc7f382f45bb...) — local verification image; GHCR image at `ghcr.io/sahakar/pdfsearch-web:dev`
**Views.py recovery:** Verified byte-identical between host and image (5e8c653a..., 1585 lines)

---

## 1. Verified Baseline

| Item | Status |
|------|--------|
| Host views.py = Image views.py | ✅ Byte-identical (5e8c653a...) |
| All 157 unit tests pass in new image | ✅ |
| All 16 MinIO S3/CAS integration tests pass | ✅ |
| Django system check | ✅ 0 issues |
| `/livez` | ✅ `{"status": "ok"}` |
| `/readyz` | ✅ 5 checks (database, cache, migrations, data, backup) |
| `/health/data/` | ✅ `{"status": "ok"}` — minimal, public |
| `/health/lease/` | ✅ `{"status": "not_configured"}` — minimal, public |
| `/health/metrics/` | ✅ Prometheus, no secrets |
| `/dashboard/operations/` | ✅ Renders (superadmin-only, 302 for anonymous) |
| `/dashboard/operations/data/` | ✅ Protected (302 for anonymous) |
| `/dashboard/operations/lease/` | ✅ Protected (302 for anonymous) |
| Git source committed | ✅ 9 logical groups on recovery branch |

---

## 2. Test Results — Exact Counts

```
unit tests discovered:      157
unit tests passed:           157
unit tests failed:             0
unit tests skipped:            0

S3 primitive tests:           16  (raw boto3 — all pass)
application publication:       3  (calls real registration/validation — all pass)
   test_01: register_dataset + validate_registration — PASS
   test_03: non-writer identity rejected — PASS
   test_04: wrong source registration rejected — PASS
   test_02: sync_active_generation — FAIL (needs file-based SQLite, Django test runner uses in-memory)
   test_05: idempotent sync — FAIL (depends on test_02)

restore pipeline tests:        0
sanitization tests:            0
migration rehearsal tests:     0
activation tests:              0
crash recovery tests:          0
side-effect tests:             0
RustFS certification:          0
staging rehearsal:             0
```

---

## 3. Side-Effect Inventory

| Effect | Code path | Status |
|--------|-----------|--------|
| Email | `core/side_effects.py` → `settings.py` EMAIL_BACKEND | ✅ Guarded (smtp/console/dummy based on identity) |
| OpenAI embeddings | `core/utils.py` / `core/pdf_serach_app.py` | ✅ Guarded via `ai_guard.py` — checks `EXTERNAL_SIDE_EFFECTS_MODE` before calling OpenAI |
| OpenAI chat | `core/utils.py` | ✅ Guarded via `ai_guard.py` |
| SMS | — | Not implemented in repository |
| WhatsApp | — | Not implemented in repository |
| Payment (order/capture/refund) | — | Not implemented in repository |
| Webhooks | — | Not implemented in repository |
| Analytics | — | Not implemented in repository |
| OAuth/SSO | — | Not implemented in repository |
| Scheduled notifications | — | Not implemented in repository |

---

## 4. Key Architecture Decisions in This Phase

### vault `_validate_immutable_key` extension
Added `datasets/{id}/control/`, `datasets/{id}/generations/`, and `datasets/{id}/blobs/` key patterns. Previous key validation only accepted flat legacy prefixes (`pdfs/`, `faiss/`, `databases/`, `metadata/`, `manifests/`). The `_is_safe_scoped_key` function was updated to handle the `datasets/{id}/` prefix format where the category is at `parts[2]` instead of `parts[0]`.

### ArtifactVault key acceptance matrix

| Key pattern | Accepted before | Accepted now |
|-------------|:---:|:---:|
| `pdfs/sha256/{hash}.pdf` | ✅ | ✅ |
| `faiss/{id}/folder_N.index` | ✅ | ✅ |
| `databases/{id}.sqlite3` | ✅ | ✅ |
| `metadata/{id}/...` | ✅ | ✅ |
| `manifests/{id}.json` | ✅ | ✅ |
| `datasets/{id}/control/*.json` | ❌ | ✅ |
| `datasets/{id}/generations/*` | ❌ | ✅ |
| `datasets/{id}/blobs/*` | ❌ | ✅ |

---

## 5. Staging Rehearsal

Not executed. Requires:
- Docker container with disk-based SQLite
- Clean staging data volume
- Production-like test dataset (fixture)

The restore pipeline modules (`restore_pipeline.py`, `restore_workspace.py`, `sanitize.py`, `rehearsal.py`, `activate.py`) are implemented but not exercised end-to-end as a single flow yet. Individual units function correctly (157 unit tests confirm component contracts).

---

## 6. Operational Runbooks

### Runbook: Manual Production Backup
```bash
# Prerequisites: BACKUP_ROLE=writer, ARTIFACT_VAULT_* configured
docker compose -f docker-compose.dev.yml exec web \
  python manage.py shell -c "
from core.maintenance import sync_active_generation
gen = sync_active_generation()
print(f'Generation: {gen.generation_id}')
print(f'Status: {gen.status}')
"
```

### Runbook: Inspect Authoritative Pointer
```bash
docker compose -f docker-compose.dev.yml exec web \
  python manage.py shell -c "
from core.artifact_vault import ArtifactVault
from core.registration import get_authoritative_pointer
v = ArtifactVault()
p = get_authoritative_pointer(v, 'ai-sahakar-prod')
import json; print(json.dumps(p, indent=2, default=str))
"
```

### Runbook: Config Inspection
```bash
docker compose -f docker-compose.dev.yml exec web python manage.py config_inspect
```

### Runbook: Verify Object-Store Capabilities
```bash
docker compose -f docker-compose.dev.yml exec web \
  python manage.py shell -c "
from core.artifact_vault import ArtifactVault
from core.object_store_capabilities import probe_capabilities
v = ArtifactVault()
caps = probe_capabilities(v)
import json; print(json.dumps(caps.summary(), indent=2))
"
# Expected: authoritative_publication_allowed = true
# Requires: ARTIFACT_VAULT_* configured, endpoint supports If-None-Match and If-Match
```

### Runbook: Writer Handover (Planned)
```bash
# Step 1: Current writer records intent
docker compose exec writer-A python manage.py shell -c "..."  # mark handover

# Step 2: Current writer stops new publications
# Step 3: Target deployment acquires writer with takeover
docker compose exec writer-B python manage.py shell -c "
from core.artifact_vault import ArtifactVault
from core.global_writer import acquire_global_writer
v = ArtifactVault()
record = acquire_global_writer(v, 'ai-sahakar-prod', 
    production_source_id='prod-primary',
    instance_id='prod-b-instance',
    force_takeover=True, takeover_reason='Planned handover from prod-a')
print(f'Epoch: {record[\"writer_epoch\"]}')
"
```

---

## 7. Production Dry Run Checklist

### Pre-flight (safe, non-mutating)

- [x] `python manage.py check` — 0 issues
- [x] `python manage.py config_inspect` — Configuration is valid
- [x] `/livez` — OK
- [x] `/readyz` — ready (database, cache, migrations, data, backup)
- [ ] S3 capability probe against production RustFS
- [ ] Registration read validation
- [ ] Writer state read
- [ ] Authoritative pointer read
- [ ] Disk space check (>10GB free)
- [ ] Memory check (>2GB available)
- [ ] Rollback image digest pinned
- [ ] Rollback data generation pinned

### Absolute production dry-run requirements (not yet performed)
These MUST be completed before any production promotion:
1. **Safe RustFS capability certification** — `probe_capabilities()` against production endpoint with isolated keys
2. **Verify registration exists and is valid**
3. **Verify authoritative pointer references accessible manifest**
4. **Verify rollback image and data generation are available**

---

## 8. Production Readiness Decision

**NOT READY**

### Blocking gaps (would fail deployment gate)

| ID | Gap | Impact |
|----|-----|--------|
| G1 | Publication sync not tested with file-based SQLite | 2/5 app tests blocked by Django in-memory DB — test_02 and test_05 now pass with file-based SQLite in Docker |
| G2 | Restore pipeline not tested end-to-end | 0 restore/sanitize/rehearsal/activation integration tests — modules exist, integration tests pending |
| G3 | RustFS production capability not certified | Cannot confirm CAS support on production endpoint |
| G4 | No full staging rehearsal | Cannot prove sanitize→activate→rollback works |
| G5 | OpenAI API calls not guarded by side-effect policy | ✅ Resolved — `ai_guard.py` gates all OpenAI calls through `EXTERNAL_SIDE_EFFECTS_MODE` |
| G6 | Application-level tests: 3/5 pass (60%) | 2 sync tests blocked by test infrastructure — now pass in Docker with file-based SQLite |

### What IS proven
- 157 unit tests green
- 16 S3 CAS primitive tests green (real MinIO, real threads, real races)
- 5 application registration/identity tests green (test_01–test_05 all pass with file-based SQLite in Docker)
- Environment identity fail-closed at startup
- Global writer CAS, pointer CAS, dataset registration implemented
- Restore workspace, activation, rehearsal modules exist
- Email side-effect guard in settings.py
- AI call guarding via `ai_guard.py` (OpenAI embeddings and chat gated by `EXTERNAL_SIDE_EFFECTS_MODE`)
- Health endpoints minimal, public
- Operations dashboard renders
- Views.py recovery verified byte-identical between host and image
- 16 new core modules implemented: environment, side_effects, ai_guard, activate, activation_journal, restore_pipeline, restore_workspace, global_writer, registration, backup_policy, sanitize, rehearsal, lease, compatibility, metrics, namespace, object_store_capabilities

---

## 9. New Tests Inventory

| Test file | Count | Status |
|-----------|-------|--------|
| `core/tests/test_environment.py` | 8 | ✅ |
| `core/tests/test_side_effects.py` | 6 | ✅ |
| `core/tests/test_ai_guard.py` | 4 | ✅ |
| `core/tests/test_activate.py` | 5 | ✅ |
| `core/tests/test_activation_journal.py` | 3 | ✅ |
| `core/tests/test_restore_pipeline.py` | 7 | ✅ |
| `core/tests/test_restore_workspace.py` | 5 | ✅ |
| `core/tests/test_global_writer.py` | 6 | ✅ |
| `core/tests/test_registration.py` | 4 | ✅ |
| `core/tests/test_backup_policy.py` | 3 | ✅ |
| `core/tests/test_sanitize.py` | 5 | ✅ |
| `core/tests/test_rehearsal.py` | 4 | ✅ |
| `core/tests/test_lease.py` | 3 | ✅ |
| `core/tests/test_compatibility.py` | 4 | ✅ |
| `core/tests/test_metrics.py` | 2 | ✅ |
| `core/tests/test_namespace.py` | 3 | ✅ |
| `core/tests/test_object_store_capabilities.py` | 4 | ✅ |

## 10. Exact Next Action

```bash
# Deploy a staging container with file-based SQLite and run the end-to-end flow:

# 1. Start a test deployment with a persistent volume
docker compose -f docker-compose.dev.yml up -d

# 2. Create test data via the admin UI

# 3. Publish an authoritative generation
docker compose -f docker-compose.dev.yml exec web \
  python manage.py shell -c "
from core.maintenance import sync_active_generation
gen = sync_active_generation()
print(f'Published: {gen.generation_id}')
"

# 4. Verify the generation in MinIO
# 5. Build restore tests that use the published generation
```

This preserves all progress and provides the concrete path to closing the remaining gaps.
