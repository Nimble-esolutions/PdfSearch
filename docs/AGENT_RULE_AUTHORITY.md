Status: Active
Audience: Agent operators and maintainers
Owner: FlowDocs maintainers
Last verified: 2026-08-05
Canonical source: docs/AGENT_RULE_AUTHORITY.md
Supersedes: ad hoc Kilo/OpenCode/Codex rule copies when they conflict

# Agent Rule Authority
## Current-state pointer

Use the living [HANDOFF.md](HANDOFF.md) for current verified state, blockers,
and next actions. Dated `STATUS-*.md` pages remain historical evidence.


This document defines how agent-facing instructions are maintained for
PdfSearch. It exists because the local operations workspace also contains
`.kilo` and `.opencode` rule bundles that are useful but can drift from the
tracked application repository.

## Authority Order

1. Current user instructions and active runtime system/developer instructions.
2. Parent workspace `../AGENTS.md`, when this repository is inside the
   `sahakar-ind-01` operations workspace.
3. Repository-local `AGENTS.md`.
4. `docs/HANDOFF.md` for current observed state and next actions.
5. `docs/PRODUCTION_OPERATING_RULES.md`.
6. `docs/CODEX_OPERATIONS_GUIDE.md`.
7. `docs/OPERATIONS_RUNBOOK.md`, `docs/BUILD_AND_RELEASE_ROADMAP.md`, and
   release evidence records.
8. `.kilo` and `.opencode` adapters, only as compatibility views.

When two sources disagree on server identity, production domain, deployment
authority, image identity, data boundary, or rollback procedure, use the higher
authority and fix the lower adapter in a separate docs/rules PR.

## Current Canonical Facts

- Repository: `Nimble-esolutions/PdfSearch`.
- Default branch and deployment branch: `dev`.
- Production domains: `ai-sahakar.net` and `www.ai-sahakar.net`.
- Historical preview host: `2026.ai-sahakar.net`; use only as rollback or
  verification evidence.
- Production release identity: Git SHA, OCI image digest, Compose hash, Dokploy
  deployment ID, and data generation.
- Mutable tags such as `dev` and `latest` are compatibility aliases, not release
  identity.
- Application code is immutable in the image under `/app/flowdocs`.
- Active mutable application data belongs under `/app/data`.
- Maintenance work is queued through the Admin UI and executed by the single
  Compose `maintenance` worker; agents must not turn bulk indexing into a web
  request loop.
- S3/RustFS data is addressed by immutable generation IDs. Pull means staged
  and checksum-verified until an explicit promotion record exists. The
  `object_store_capabilities` module detects and verifies S3-compatible storage;
  the `restore_pipeline` and `restore_workspace` modules orchestrate isolated
  restore operations; the `activate` module performs atomic active-release
  pointer switches.
- Public search is intentionally enabled by default; do not reintroduce a
  login requirement without an approved product decision and test update.
- `/register/` is never public. Require an authenticated `admin` or
  `superadmin`; ordinary users and visitors must not reach the form or create
  accounts. Department-scoped admin roles are deferred to phase 2.
- Legacy data is recovery-only and must not be copied directly into active
  production.
- New core modules (PRs #42-#53, merged at `2e1ca38`): `environment`
  (APP_ENV, PRODUCTION_SOURCE_ID, AUTHORITATIVE_DATASET_ID, DATASET_ID,
  BACKUP_ROLE, EXTERNAL_SIDE_EFFECTS_MODE, DATA_MODE), `side_effects`
  (external side-effect safety gating), `ai_guard` (AI operation
  authorization), `activate` (atomic active-release pointer switch),
  `activation_journal` (activation audit trail), `restore_pipeline` and
  `restore_workspace` (isolated restore orchestration), `global_writer`
  (global writer fencing), `registration` (dataset registration), `backup_policy`
  (backup policy enforcement), `sanitize` (data sanitization), `rehearsal`
  (migration rehearsal), `lease` (writer lease management), `compatibility`
  (compatibility checks), `metrics` (metrics collection), `namespace`
  (namespace management), `object_store_capabilities` (object store
  capability detection).
- New health endpoints: `/health/data/`, `/health/lease/`, `/health/metrics/`.
- New management commands: `config_inspect`, `verify_object_store_capabilities`,
  `inventory_artifacts`, `validate_data_release`.
- New UI: `/dashboard/operations/`, `/dashboard/users/`, PDF lifecycle,
  generation lifecycle.
- Public/admin visual authority: [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md).
  It protects the Civic Knowledge Workbench and Operations Cockpit directions;
  agents may make evidence-backed enhancements but may not change the visual
  direction without an explicit human request.
- Post-reconciliation data: 253 PDF rows, 242 PDF files, 53 folders, 8 users,
  51 FAISS indexes, 8,753 vectors.
- COMPLETION_PLAN: the data release pipeline (environment identity, global
  writer fencing, dataset registration, restore pipeline, compatibility
  checks, sanitization, migration rehearsal, activation journal, writer
  lease, backup policy, object store capabilities, namespace, metrics) is
  implemented and merged. Remaining planned items: automatic artifact
  publishing and cross-environment synchronization.

## Mandatory impact-analysis gate

Before an agent edits or proposes a change to Compose, Dockerfiles, Dokploy,
volumes, environment contracts, deployment documentation, data custody,
DNS/proxy labels, image identity, or recovery behavior, it must record this
minimum analysis in the task notes or PR body:

| Required field | Required content |
| --- | --- |
| Scope | Exact files, branch, target environment, and remote service/project |
| Current contract | Existing Compose/env/image/volume behavior and source evidence |
| Environment matrix | Dev, stage, and production effect, including unchanged environments |
| Data risk | Whether databases, media, indexes, control state, secrets, or DNS can change |
| External state | Whether Docker, Dokploy, RustFS, GitHub, DNS, or running traffic changes |
| Reversibility | Exact rollback commit/config and volume/generation recovery path |
| Verification | Rendered config, tests, live checks, image digest, and volume identity evidence |
| Approval gate | Explicit user approval for destructive, remote, or environment-specific actions |

The agent must not implement a materially different alternative after the user
rejects an option. A renamed variable or Compose override is still the same
scope if it changes the same deployment boundary. When the impact analysis
reveals a new trade-off, pause and present the alternatives before editing.

Before pushing, the agent must report the exact changed-file list, commit SHA,
base branch, deployment impact, rollback path, and checks run. Local Compose
rendering does not prove that Dokploy used the intended Compose file, image,
project name, or volumes.

## Adapter Maintenance Rules

- Keep Kilo/OpenCode/Codex copies small and explicitly marked as adapters.
- Prefer links back to this document and `AGENTS.md` over duplicating full
  production facts.
- When a production audit changes server facts, update the canonical source
  first, then the adapters.
- Never put bootstrap credentials, secret values, production `.env` content,
  private PDFs, SQLite databases, FAISS indexes, or Chroma data into an adapter.
- If an adapter cannot be updated through this Git repository because it lives
  in a parent workspace, record that limitation in the PR body.

## PR Expectations

Agent-rule or operations-doc changes should include:

- A statement of which sources were checked for drift.
- Whether the change affects production deployment behavior.
- Whether any parent-workspace Kilo/OpenCode files need follow-up outside this
  repository.
- The validation commands used.

Release or deployment claims must cite a GitHub run, image digest, and explicit
promotion status. A successful `dev` image release is not the same thing as a
verified production deployment.
