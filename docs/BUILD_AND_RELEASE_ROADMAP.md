Status: Active
Audience: Release
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: docs/BUILD_AND_RELEASE_ROADMAP.md
Supersedes: docs/DOCKER_IMAGE_OPTIMIZATION.md

# Build and Release Roadmap
## Current-state pointer

Use the living [HANDOFF.md](HANDOFF.md) for current verified state.
Earlier dated sections remain historical evidence and must be reconciled to
the living handoff before use.


## Current Baseline

The current application image is multi-stage, cache-enabled, and built from the
hashed `requirements-web.lock` dependency set. Web and maintenance run the same
immutable image through different entrypoints so application, migration, OCR,
embedding, and index contracts cannot drift between processes. Historical
image-size observations belong in dated release evidence, not this roadmap.

Legacy production remains `https://www.ai-sahakar.net`.
`https://2026.ai-sahakar.net` is the current non-production stage/rehearsal
host, not a production cutover. Future Dokploy production promotion requires
exact application and infrastructure image digests and
`pull_policy: always`; compatibility tags are not release identity.

Use [HANDOFF.md](HANDOFF.md) for the current `dev` merge and deployed artifact;
stable architecture documents do not hard-code a moving branch head.

## Current image and artifact topology

```text
one immutable application image
  web entrypoint: Django, Gunicorn, retrieval, public/admin UI
  maintenance entrypoint: queued intake, extraction, local OCR, embedding, indexing

runtime data volume
  SQLite, uploaded media, PDF cache, FAISS/Chroma, projected generations

paired control volume
  control SQLite, leases, signed activation intent/results, active/previous pointers

RustFS recovery storage
  complete DataOps v3 manifests and content-addressed objects
```

Splitting web and maintenance into different images is an optional future
optimization, not current architecture. It requires measured image/startup
benefit plus compatibility proof for OCR, migrations, embedding dimensions,
index formats, and activation before adoption.

## Optimization backlog

### Measure first

- Produce an import graph for web startup and worker paths.
- Record image layer sizes and build timings.
- Identify CPU-only versus CUDA dependencies.
- Record current model and index formats.

### Reduce runtime weight safely

- Test CPU-only Torch/EasyOCR wheels.
- Remove CUDA/NVIDIA packages from the web runtime.
- Keep web and maintenance on the same image until split-image compatibility
  and rollback are certified.
- Enforce a temporary image budget in CI.

### Bound asynchronous work

- Keep upload/OCR/embedding work behind the existing durable maintenance queue.
- Keep web requests bounded and responsive.
- Add retries, idempotency, and task status to the database.

### Strengthen versioned artifacts

- Keep DataOps recovery manifests and content-addressed objects separate from
  application images.
- Extend compatibility metadata only when restore/rebuild decisions need it.
- Validate artifacts before activation.
- Keep the active and previous known-good release.

### Certify promotion

- Test web and maintenance entrypoints from the same exact image digest.
- Run representative PDF ingestion, OCR, retrieval, and search tests.
- Promote the exact image-generation-manifest tuple together.
- Roll back both when compatibility is broken.

## Current CI and Release Evidence

The current workflow reports or verifies, depending on event and changed paths:

- source Git SHA;
- image digest;
- image size;
- short SHA, `dev`, and `latest` compatibility image tags;
- SBOM and max provenance settings;
- source, documentation, Compose, release-integrity, startup, browser, recovery,
  and runtime contracts;
- image smoke tests, `pip check`, non-blocking Trivy reporting, and image-size budget.

The release workflow does not make a mutable tag authoritative and does not
turn a successful image build into a data promotion. DataOps v3 separately
records recovery manifests, object digests, candidates, and activation
evidence. Exact historical PRs, revisions, image digests, and rollout results
belong in dated release records and [HANDOFF.md](HANDOFF.md), not this active
roadmap.

## GitHub Actions Release Policy

- Pull requests receive the bounded `PR contract`; it is supplemental feedback,
  not full release certification.
- Merge-group validation certifies the exact proposed `dev` merge without
  package-write permission or GHCR publication.
- The protected final `dev` push rebuilds, publishes, and certifies its exact
  image SHA. This rebuild is intentionally unavoidable until a separately
  reviewed candidate-reuse design proves merge-group and final SHA identity.
- Required-check and merge-queue enforcement lives in repository settings;
  workflow files alone cannot prove those controls are enabled.
- Require CODEOWNER approval for workflows, Docker entrypoints, dependency
  locks, and CI scripts; dismiss stale approvals and restrict bypasses.
- Do not universally require the path-filtered documentation workflow context;
  require the always-materialized rollup contexts instead.
- A push to `dev` after merge is the release trigger.
- Manual publishing is allowed only from `dev` with explicit approval/input.
- Docker/Checkout Actions are pinned to verified Node 24 commit SHAs.
- Release images publish a short SHA, `dev`, and compatibility `latest` tag.
- Dokploy should consume the recorded image digest, not `latest` as the only
  release identifier.
- Release jobs use a protected `production` environment when configured.
- SBOM and max provenance are enabled for published images.

## Traps

- Do not delete Torch before proving OCR and indexing still work.
- Do not store generated indexes in Docker image layers.
- Do not deploy a mutable `latest` tag as the only release identifier.
- Do not promote an index generated with an incompatible embedding model.
- Do not use Redis as the durable job ledger.

## Dependency and Digest Policy

- `requirements-web.txt` is the human-maintained input.
- `requirements-web.lock` is the hashed build input used by Docker.
- Lockfile refreshes are deliberate dependency changes, not release side effects.
- Release smoke tests pull the exact published `repo@sha256:digest`, not `dev`
  or `latest` tags.
- `dev` and `latest` remain compatibility aliases for the current Dokploy
  Compose contract; they are not deployment identity.

## Current Versus Planned

Current: immutable application release, Redis-enabled runtime, DataOps v3
recovery manifests/receipts, signed restore/activation gates, environment identity in CI
(`APP_ENV`, `PRODUCTION_SOURCE_ID`, `AUTHORITATIVE_DATASET_ID`, `DATASET_ID`,
`BACKUP_ROLE`, `EXTERNAL_SIDE_EFFECTS_MODE`, `DATA_MODE`), global writer fencing,
dataset registration, restore pipeline with compatibility checks, sanitization,
migration rehearsal, activation journal, writer lease, backup policy, object
store capabilities, namespace, metrics, and the data release contract
(`inventory_artifacts`, `validate_data_release`). The Data protection workbench
and maintenance worker connect immutable publication to isolated, validated
candidate preparation and separately confirmed signed stage activation.
Representative CI gates prove exact generation/manifest readiness and
multilingual search; the workflow remains the exhaustive gate inventory.

Future candidates: measured image/runtime reductions, broader derived-index
rebuild automation, and production RustFS/deployment certification. Automatic
cross-environment synchronization is intentionally not a target operator
workflow. Startup restore policies remain fail-closed posture checks rather
than automatic restore orchestration.
