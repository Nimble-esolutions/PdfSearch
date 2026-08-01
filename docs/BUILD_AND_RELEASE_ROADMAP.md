Status: Active
Audience: Release
Owner: FlowDocs maintainers
Last verified: 2026-07-24
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

Current dev HEAD: `2e1ca38`.

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
- PRs #42 through #53 delivered 16 new core modules (environment, side_effects,
  ai_guard, activate, activation_journal, restore_pipeline, restore_workspace,
  global_writer, registration, backup_policy, sanitize, rehearsal, lease,
  compatibility, metrics, namespace, object_store_capabilities), new health
  endpoints (`/health/data/`, `/health/lease/`, `/health/metrics/`), new
  management commands (`config_inspect`, `verify_object_store_capabilities`,
  `inventory_artifacts`, `validate_data_release`), operations dashboard, user
  management, PDF lifecycle, and generation lifecycle UI. PR #53 merged the
  data release pipeline at `2e1ca38`.

## GitHub Actions Release Policy

- Pull requests run the bounded `PR contract` and never publish an image. That
  fast check is early feedback, not full certification.
- The protected `dev` merge queue runs full validation and publishes an
  immutable candidate for the exact merge-group SHA. `Pre-merge certification`
  fails unless both jobs succeed.
- Merge queue and the required `PR contract` / `Pre-merge certification`
  checks are repository settings; workflow files do not enforce those settings.
  Do not enable the fast-only PR path until those settings are active.
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
data custody, manual restore/promotion gates, environment identity in CI
(`APP_ENV`, `PRODUCTION_SOURCE_ID`, `AUTHORITATIVE_DATASET_ID`, `DATASET_ID`,
`BACKUP_ROLE`, `EXTERNAL_SIDE_EFFECTS_MODE`, `DATA_MODE`), global writer fencing,
dataset registration, restore pipeline with compatibility checks, sanitization,
migration rehearsal, activation journal, writer lease, backup policy, object
store capabilities, namespace, metrics, and the data release contract
(`inventory_artifacts`, `validate_data_release`). The Workbench and
maintenance worker now connect authoritative publication to isolated,
validated restore preparation and separately confirmed signed runtime
activation; CI proves one exact generation and manifest through readiness and
bilingual search.

Planned: separately published versioned data artifacts, automatic FAISS recovery,
and automatic cross-environment synchronization. The restore pipeline and
activation journal provide the foundation for these; full automation of artifact
publishing and cross-environment sync remains a future target. Startup restore
policies remain a fail-closed empty-database posture check rather than
automatic restore orchestration. Accumulated-volume redeploy, genuinely fresh
volume recovery, and production RustFS certification remain Plan 003
deployment proofs; see the audited vault guide.
