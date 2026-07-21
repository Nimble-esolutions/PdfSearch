# Build and Release Roadmap

## Current Baseline

The production image is multi-stage and cache-enabled, but it contains the
complete OCR/ML dependency graph and has been observed near 10 GB. This is
acceptable as a temporary reliability baseline, not the target architecture.

## Target Image Topology

```text
pdfsearch-web
  Django, Gunicorn, query/retrieval code, Redis client, index loader

pdfsearch-worker
  Celery, PDF extraction, OCR, Torch, spaCy, Chroma/FAISS indexing

artifacts
  versioned models, OCR language packs, FAISS/Chroma releases, manifests
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

## CI Gates

Every release should report:

- source Git SHA;
- image digest;
- image size;
- build duration;
- dependency lock digest;
- SBOM and vulnerability result;
- model/index artifact manifest;
- smoke-test result.

## Traps

- Do not delete Torch before proving OCR and indexing still work.
- Do not store generated indexes in Docker image layers.
- Do not deploy a mutable `latest` tag as the only release identifier.
- Do not promote an index generated with an incompatible embedding model.
- Do not use Redis as the durable job ledger.
