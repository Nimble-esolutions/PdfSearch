# Docker Image Optimization

## Current State

The current Python image is multi-stage and uses BuildKit cache mounts, but it
still contains the complete OCR/ML dependency graph. Production image size has
been observed near 10 GB because CPU deployment paths still pull heavy Torch,
EasyOCR, spaCy, Chroma, and related packages.

The current image is reliable, but image size and pull time remain open work.

## Current Build Guarantees

- Build dependencies are isolated in the builder stage.
- Runtime dependencies are installed in the runtime stage.
- apt and pip caches use BuildKit cache mounts.
- runtime pip installation uses `--no-compile`.
- image builds target `linux/amd64` for the production server.
- CI publishes a SHA tag in addition to the mutable convenience tag.

## Next Optimization Plan

1. Inventory imports used by the web/query path versus indexing/OCR.
2. Build a lean `pdfsearch-web` image for Gunicorn and query serving.
3. Build a separate `pdfsearch-worker` image for OCR, Torch, spaCy, and index generation.
4. Publish model and index artifacts independently with checksums.
5. Deploy the web and worker services separately in Dokploy.
6. Promote only compatible image/artifact pairs.

Do not remove ML dependencies without running PDF upload, OCR, indexing, and
search smoke tests. A smaller image that fails on a rare document type is not
an optimization.

## Build Verification

```bash
docker build --check -f Dockerfile .
docker compose -f docker-compose.yml config
```

CI must also enforce image-size budgets, SBOM generation, dependency scanning,
and a container smoke test before promotion.
