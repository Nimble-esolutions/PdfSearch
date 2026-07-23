Status: Active
Audience: Agent operators and maintainers
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/AGENT_RULE_AUTHORITY.md
Supersedes: ad hoc Kilo/OpenCode/Codex rule copies when they conflict

# Agent Rule Authority

This document defines how agent-facing instructions are maintained for
PdfSearch. It exists because the local operations workspace also contains
`.kilo` and `.opencode` rule bundles that are useful but can drift from the
tracked application repository.

## Authority Order

1. Current user instructions and active runtime system/developer instructions.
2. Parent workspace `../AGENTS.md`, when this repository is inside the
   `sahakar-ind-01` operations workspace.
3. Repository-local `AGENTS.md`.
4. `docs/PRODUCTION_OPERATING_RULES.md`.
5. `docs/CODEX_OPERATIONS_GUIDE.md`.
6. `docs/OPERATIONS_RUNBOOK.md`, `docs/BUILD_AND_RELEASE_ROADMAP.md`, and
   release evidence records.
7. `.kilo` and `.opencode` adapters, only as compatibility views.

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
  and checksum-verified until an explicit promotion record exists.
- Public search is intentionally enabled by default; do not reintroduce a
  login requirement without an approved product decision and test update.
- `/register/` is never public. Require an authenticated `admin` or
  `superadmin`; ordinary users and visitors must not reach the form or create
  accounts. Department-scoped admin roles are deferred to phase 2.
- Legacy data is recovery-only and must not be copied directly into active
  production.

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
