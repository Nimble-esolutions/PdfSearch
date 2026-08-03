---
Status: Active, dated operational change record
Audience: Maintainer, Operator, Reviewer, Security Owner
Owner: FlowDocs maintainers
Last verified: 2026-08-02
Canonical source: docs/OPERATIONS_CHANGELOG-2026-08-02.md
---

# Operations change record: 2026-07-30 → 2026-08-02

This page answers one question: what changed since the last documentation
verification date, and what evidence proves each change? It complements the
dated state record in STATUS-2026-08-02.md. Current evidence moved to
STATUS-2026-08-03.md.

## Executive summary

The work moved from a designed recovery contract to a populated, verified
stage quarantine:

    legacy production (unchanged)
      -> stable read-only snapshot
      -> new RustFS v2 source generation
      -> explicit clone/rebind into stage dataset
      -> quarantine restore and migration rehearsal
      -> local OCR fallback + embeddings + index reconciliation
      -> [pending] signed activation
      -> [pending] first stage backup
      -> [pending] isolated round trip

The stage route is reachable and its containers are healthy. It is not yet a
ready active data release. A 503 from /readyz is therefore an honest gate, not
an outage to bypass.

## Timeline

### 2026-07-30: documentation baseline

- Recovery, activation, custody, and Data Operations contracts were present.
- The pages still mixed older active-custody reconciliation evidence with the
  new 2026 source/clone/stage rehearsal.
- The vault was documented as operator-controlled rather than automatic.

### Release work carried forward

- PR #166 merged multilingual OCR support, durable queued PDF processing,
  provenance, and strict readiness behavior.
- PR #167 merged case-insensitive PDF suffix counting. This matters because
  seven source files use uppercase .PDF suffixes.
- PR #168 merged document-scoped indexing evidence. The readiness denominator is
  now searchable PDF rows, not the number of folder-level FAISS files.

### 2026-08-02: source and clone

- Legacy source boundary remained service
  sahakar-dev-frontend-dockerfile-1cubi5, volume prod_flowdocs, mounted at
  /app/flowdocs read-only for migration.
- Source inventory and SQLite integrity evidence passed: 242 PDFs, 46 folders,
  7 users, and 29 migrations.
- Source generation published to the new v2 bucket/dataset:
  legacy-20260802T085639Z-86288855,
  ai-sahakar-prod-flowdocs-artifact-vault-v2,
  ai-sahakar-prod-v2.
- Existing production RustFS bucket/pointer was not modified.
- Clone/rebind created:
  clone-legacy-20260802T085639Z-86288855,
  ai-sahakar-stage-2026-flowdocs-artifact-vault,
  ai-sahakar-stage-2026.
- Destination verification passed for 416 objects and 1,093,501,777 bytes.
- Parent dataset, parent generation, parent manifest digest, and
  clone-reason=stage-rehearsal lineage were retained.

### 2026-08-02: quarantine restore and search readiness

- Clone restored into a stage quarantine/new-generation workspace, not over the
  active runtime data root.
- Migrations were applied through 0027_pdffile_processing_evidence.
- Database, foreign-key, media/PDF, and restore reconciliation checks passed.
- 45 folder FAISS indexes were rebuilt from stored embeddings.
- Nine PDFs initially lacked searchable chunks; bounded local OCR/embedding
  processing completed them.
- OCR languages were English, Marathi, and Hindi (eng+mar+hin). One scanned
  example had 0 native-text pages and 21 OCR pages.
- Final quarantine verification found no missing or not-ready PDFs:
  242 ready, 242 indexed, 242 with extracted text/chunks/embeddings.
- Original PDFs remained the custody artifact; OCR text is derived state.

### 2026-08-02: route and environment

- The 2026 route progressed from the earlier 404 condition to a served HTTPS
  route at https://2026.ai-sahakar.net.
- Web, maintenance, and Redis are healthy on the existing stage deployment.
- The stage env file was backed up, normalized, mode 0600, and applied through
  the original Compose project name.
- Correct non-secret selectors are:
  DATAOPS_RESTORE_PROFILE=stage_2026 and
  DATAOPS_BACKUP_PROFILE=stage_2026.
- Generic image/source compatibility identity was corrected to the currently
  running b71 immutable image and the ai-sahakar-prod-v2 namespace.
- Activation flags remain zero. The running stage pointer remains empty.
- A first Compose attempt without the Dokploy project-name override created
  only temporary blank resources and failed on a static-files collision. Those
  exact temporary resources were removed; the original stage volumes were
  verified intact and the correct project was recreated.

### 2026-08-02: dependency security

- Trivy reported HIGH CVE-2026-26007 and GHSA-537c-gmf6-5ccf against
  cryptography 45.0.7.
- PR #169 constrains cryptography to >=48.0.1,<49.0.0 and refreshes the
  hash-locked entry. The hash-enforced resolver passed.
- PR #169 source/deployment, contract, and pre-merge checks passed.
- Candidate image build and Trivy are intentionally skipped on pull requests;
  they must run after merge to dev.
- The current stage remains on b71 as its rollback reference. The security
  image has not been deployed or treated as certified.

### 2026-08-02: documentation

- PR #170 adds the canonical status page, this change record, current-state
  pointers across operational docs, environment examples, runbook notes, and
  Mermaid sources for custody, recovery state, and OCR/index lifecycle.
- Generated SVGs were not committed because Kroki timed out and local Mermaid
  CLI lacked Chrome. Source diagrams remain reviewable and reproducible.
- README badges were limited to Docker CI/release and documentation contract;
  the README architecture and repository tree were refreshed.
- ARCHITECTURE_OVERVIEW.md now documents the four-plane topology, current
  quarantine handoff, and repository structure.
- LEGACY_VS_CURRENT_STATE.md was added as the living old-versus-new comparison
  and future update contract.

## Backup size: what was actually created?

The source and clone are not a single opaque database dump. They are immutable
content-addressed recovery generations:

| Layer | Included | Why it matters |
| --- | --- | --- |
| SQLite | online-consistent database artifact | users, folders, PDF rows, migrations, evidence |
| PDF/media | content-addressed objects | original source documents and media |
| FAISS | folder-level derived indexes | fast retrieval compatibility evidence |
| Chroma | included when present | derived vector/storage compatibility |
| PDF cache/index metadata | included when present | reproducibility and reconciliation |
| Manifest | counts, paths, sizes, hashes, schema, lineage | restore trust contract |
| Static files/source code | excluded by default | rebuilt from the immutable application image |
| Secrets, logs, Redis state, local history | excluded | avoid leaking or confusing runtime state |

The destination size is 1,093,501,777 bytes across 416 objects. It is larger
than a bare SQLite file because it preserves the complete recovery set,
including PDFs and derived search artifacts. Content addressing permits safe
deduplication across generations, but deduplication does not make the first
complete generation small.

## Current-versus-historical matrix

| Topic | Current source of truth | Historical material to interpret carefully |
| --- | --- | --- |
| Migration/clone | STATUS-2026-08-02.md | older port section in INTERNAL_VAULT_MIGRATION.md |
| Stage readiness | STATUS-2026-08-02.md and /readyz | July active-custody counts |
| Runtime activation | STAGING_RUNTIME_ACTIVATION.md | no signed pointer exists yet |
| First backup | dataops/ROLLOUT.md checklist | no receipt exists yet |
| Round trip | RECOVERY_CERTIFICATION.md checklist | preparation is not certification |
| OCR/indexing | INDEX_MAINTENANCE_RUNBOOK.md | folder-index count is not document ratio |
| Image security | SECURITY_SCAN.md and PR #169 | previous b71 scan is not the new image scan |
| Environment | ENVIRONMENT_CONTRACT.md and stage env provider | never copy secret values from host |
| Custody | DATA_CUSTODY_AND_PROMOTION.md | older active-custody inventory is historical |

## Readiness interpretation guide

| Observation | Correct interpretation | Action |
| --- | --- | --- |
| Root route 200 | proxy and web route answer | continue to /readyz and image checks |
| /readyz 503, no generation | fail-closed before activation | do not copy quarantine over active data |
| 242/242 quarantine ready | restore/search preparation passed | prepare signed control-plane evidence |
| stage_2026 profile configured | backup destination is selected | still publish and verify first receipt |
| healthy containers | process health passed | not proof of data readiness or recovery |
| PR #169 green | code contract passed | merge, build, scan immutable image |

## Documentation coverage checklist

- [x] Current status and scope boundary
- [x] Legacy source inventory and immutability
- [x] RustFS buckets, datasets, roles, and generations
- [x] Clone/rebind lineage and object verification
- [x] Restore workspace, migration, OCR, embeddings, and index evidence
- [x] Stage route and readiness semantics
- [x] Stage env selectors and project-name storage safety
- [x] Backup type and size explanation
- [x] Security finding, remediation, and scan timing
- [x] Activation, first backup, round trip, and public-auth gates
- [x] Mermaid source diagrams
- [ ] Post-merge image digest and Trivy result
- [ ] Signed active generation and manifest digest
- [ ] First stage backup receipt
- [ ] Isolated round-trip evidence

## Evidence rules

Use the audit ledger and host evidence paths for exact full digests. This page
intentionally uses placeholders where a full manifest digest would be
operationally sensitive. Do not add secrets, document names, document text,
object bodies, passwords, API keys, or private credential references.
