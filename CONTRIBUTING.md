# Contributing to PdfSearch

**Status:** Active
**Audience:** Contributors, reviewers, and agents
**Last audited:** 2026-07-25

## Before starting

Read [`AGENTS.md`](AGENTS.md), [`docs/AGENT_RULE_AUTHORITY.md`](docs/AGENT_RULE_AUTHORITY.md), and the relevant product contract. For public/admin UI work, read [`docs/design/AI_SAHAKAR_UI_CONTRACT.md`](docs/design/AI_SAHAKAR_UI_CONTRACT.md). Never copy production secrets, PDFs, SQLite files, FAISS indexes, or `.env` values into a branch.

## Branch and commit workflow

```text
update local dev → create feature branch → implement small change
→ run focused checks → commit logical chunks → push branch
→ open PR into dev → wait for CI/review → human merges
```

Never commit directly to `dev` or `main`, force-push without explicit approval,
or leave uncommitted work at the end of a task. Use commit subjects such as:

```text
fix(config): make upload size operator-readable
docs(env): audit stage and production examples
test(config): cover MB upload limits
```

## Pull request checklist

- Explain what changed, why, impact, rollback, and data/migration behavior.
- Identify environment variables added, removed, renamed, or deprecated.
- Include exact verification commands and honest failures/gaps.
- Run `git diff --check`, Django checks, migration checks, relevant tests,
  Compose validation, and browser/smoke gates for affected surfaces.
- For release-impacting changes, record Git SHA, GitHub Actions run, immutable
  image digest, published-image smoke, and promotion status.
- For environment changes, update the contract and reviewed examples; do not
  embed secrets.

## Dev-to-release map

1. Open the PR against `dev`.
2. The required `PR contract` gives bounded static and Compose feedback within
   two minutes. It is not full runtime certification and never publishes an
   image.
3. Add the approved PR to the protected `dev` merge queue. The queue's exact
   synthetic merge SHA must pass the full source/runtime suite, immutable-image
   candidate smoke, and `Pre-merge certification` rollup before GitHub merges it.
   Never bypass the queue merely because the fast PR check is green.
4. After GitHub merges into `dev`, the Docker workflow builds and publishes
   the immutable GHCR image, runs the published-image smoke gate, and promotes
   compatibility tags only after the digest gate.
5. Dokploy must then be configured to pull that exact digest, recreate the
   intended service, and route the intended hostname. A successful GitHub
   release is not deployment evidence.
6. Verify `/livez`, `/readyz`, static assets, rendered HTML, representative
   search, admin access, image revision, route/TLS, and rollback identity.
7. Production promotion is a separate approved operation with data, backup,
   digest, Compose, and rollback evidence.

## Configuration changes

Use [`docs/ENVIRONMENT_CONFIGURATION_GUIDE.md`](docs/ENVIRONMENT_CONFIGURATION_GUIDE.md)
and the environment-specific examples. For `MAX_FILE_SIZE_MB`, remove the
deprecated byte alias after migration and restart/recreate the service.
