# Docker Build Root Causes and Prevention

## Historical Failure

An earlier multi-stage build failed because runtime package names were copied
from a different Debian release. The runtime image must use package names
available in the exact base-image distribution.

## Current Prevention

- Use the pinned base image family consistently across builder and runtime.
- Validate the Dockerfile with `docker build --check`.
- Run the real `linux/amd64` build in GitHub Actions.
- Do not claim a build is valid from a local arm64 syntax check alone.
- Keep runtime package discovery and smoke tests in CI.

## Data Mount Rule

A named Docker volume is a directory. It must never be mounted to a file path
such as `/app/data/db.sqlite3`. Mount the data volume at `/app/data` and put
SQLite, media, indexes, Chroma, static files, and backups below that root.

Application code remains in the image under `/app/flowdocs`; persistent data
must not shadow that directory.

## Current Known Risks

- CPU-only deployments still contain a large OCR/ML dependency graph.
- Model/index artifacts are not yet independently versioned.
- Production deployment should use an immutable image digest rather than
  `latest`.
