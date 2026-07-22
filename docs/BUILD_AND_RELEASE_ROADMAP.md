Status: Active
Audience: Release
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/BUILD_AND_RELEASE_ROADMAP.md
Supersedes: docs/DOCKER_IMAGE_OPTIMIZATION.md

# Build and Release Roadmap

## Current Baseline

The current production web image is multi-stage, cache-enabled, and built from
the hashed `requirements-web.lock` dependency set. The complete OCR/ML graph
remains in `requirements.txt` for the future worker image. The earlier complete
image was observed near 10 GB; that measurement is historical, not the current
web-image size target.

The canonical production domains are `https://ai-sahakar.net` and
`https://www.ai-sahakar.net`. The verified preview baseline was
`https://2026.ai-sahakar.net`; it is retained only as historical rollback
evidence. Dokploy production promotion requires exact web/Redis digests and
`pull_policy: always`; compatibility tags are not release identity.

## Target Image Topology

```text
pdfsearch-web
  Django, Gunicorn, query/retrieval code, Redis client, index loader

pdfsearch-worker
  Celery, PDF extraction, OCR, Torch, spaCy, Chroma/FAISS indexing

artifacts
  versioned models, OCR language packs, FAISS/Chroma releases, planned manifests
```

## Delivery Phases

### Phase A — Measure

- Produce an import graph for web startup and worker paths.
- Record image layer sizes and build timings.
- Identify CPU-only versus CUDA dependencies.
- Record current model and index formats.

### Phase B — CPU Runtime

- Test CPU-only Torch/EasyOCR wheels.
- Remove CUDA/NVIDIA packages from the web runtime.
- Keep a complete worker image until all indexing tests pass.
- Enforce a temporary image budget in CI.

### Phase C — Split Services

- Add a Dokploy worker service.
- Move upload/OCR/embedding work behind a durable task queue.
- Keep web requests bounded and responsive.
- Add retries, idempotency, and task status to the database.

### Phase D — Versioned Artifacts

- Publish model and index artifacts separately from application images.
- Attach SHA-256 checksums and compatibility metadata.
- Validate artifacts before activation.
- Keep the active and previous known-good release.

### Phase E — Promotion

- Build and test web and worker images independently.
- Run synthetic PDF ingestion and search tests.
- Promote image digest plus artifact release together.
- Roll back both when compatibility is broken.

## Current CI and Release Evidence

The current workflow reports or verifies:

- source Git SHA;
- image digest;
- image size;
- short SHA, `dev`, and `latest` compatibility image tags;
- SBOM and max provenance settings;
  - image smoke tests, `pip check`, non-blocking Trivy scan/report, and image-size budget.

The current workflow does not emit a dependency lock digest, model/index
manifest, persistent-data manifest, or data-release artifact. Treat those as
operator-recorded evidence or future targets, not as generated release output.

Recent release records:

- [`releases/2026-07-22-admin-operations-cockpit.md`](releases/2026-07-22-admin-operations-cockpit.md)
  records PR #37, merge commit `1962e127e1ebcff0b8b0ba08622656d8eeaacaae`,
  and the published `dev` image digest for the Admin Operations Cockpit work.

## GitHub Actions Release Policy

- Pull requests run validation only and never publish an image.
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

Current: immutable application release, Redis-enabled runtime, operator-recorded
data custody, and manual restore/promotion gates.

Planned: separately published versioned data artifacts, generated manifests,
automatic FAISS recovery, and automatic cross-environment synchronization. None
of those capabilities is emitted or enforced by the current workflow.
