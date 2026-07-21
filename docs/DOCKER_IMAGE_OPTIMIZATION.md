Status: Historical
Audience: Release
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/BUILD_AND_RELEASE_ROADMAP.md
Supersedes: None

> Historical design notes. Use [`BUILD_AND_RELEASE_ROADMAP.md`](BUILD_AND_RELEASE_ROADMAP.md)
> for the current release contract. The target web/worker split described here
> is not deployed by the current Compose file.

# Docker Image Optimization

## Current State

The web image is multi-stage and uses BuildKit cache mounts. It now installs
the dependency set imported by the live Django query path through
`requirements-web.txt`. The complete OCR/indexing/demo set remains in
`requirements.txt` for the future worker image.

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
2. Build and measure the lean `pdfsearch-web` image for Gunicorn and query serving.
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
