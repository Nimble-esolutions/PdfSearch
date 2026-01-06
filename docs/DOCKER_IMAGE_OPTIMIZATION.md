# Docker Image Size Optimization

## Problem

The Docker image was 4GB+ in size, causing:
- Slow builds and pushes
- High storage costs
- Slow image pulls in production
- GitHub Actions timeouts

## Root Causes Identified

### 1. Build Tools Left in Final Image (~1-2GB)
- `build-essential` (~500MB)
- `cargo` (~800MB)
- `cmake` (~200MB)
- All `-dev` packages (headers and build tools)

### 2. Large Python ML Packages (~1-2GB)
- `sentence-transformers` (~500MB with models)
- `spacy` + `en-core-web-sm` (~400MB)
- `chromadb` (~300MB)
- `opencv-python-headless` (~200MB)
- `faiss-cpu` (~100MB)
- Other ML dependencies

### 3. No Multi-Stage Build
- All build dependencies stayed in final image
- No separation between build-time and runtime needs

### 4. Incomplete Cleanup
- Apt cache cleaned but build tools remained
- Python package caches not fully cleaned

## Solution: Multi-Stage Build

### Stage 1: Builder (Build Dependencies)
- Installs: `gcc`, `g++`, `build-essential`, `cargo`, `cmake`, all `-dev` packages
- Compiles Python packages with native extensions
- **This stage is discarded** - not included in final image

### Stage 2: Runtime (Final Image)
- Only runtime libraries (no build tools)
- Copies compiled Python packages from builder
- Minimal system dependencies
- **This is the final image** - much smaller

## Size Reduction

**Before:** 4GB+  
**After:** ~1-1.5GB  
**Reduction:** 60-75% smaller

## Key Changes

1. **Multi-stage Dockerfile**
   ```dockerfile
   FROM python:3.10-slim as builder
   # ... build stage ...
   
   FROM python:3.10-slim
   # ... runtime stage ...
   COPY --from=builder /root/.local /home/appuser/.local
   ```

2. **Build tools removed from runtime**
   - ❌ `build-essential`, `cargo`, `cmake` (removed)
   - ❌ All `-dev` packages (removed)
   - ✅ Only runtime libraries (kept)

3. **Enhanced .dockerignore**
   - Excludes `flowdocs/pdf_cache/`
   - Excludes `flowdocs/faiss_indexes/`
   - Excludes `*.pkl` files
   - Excludes backup files

4. **Better cleanup**
   - `apt-get clean` added
   - Removes `/tmp/*` and `/var/tmp/*`
   - More aggressive cache removal

## Verification

After rebuilding, check image size:
```bash
docker images ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend:latest
```

Expected: ~1-1.5GB instead of 4GB+

## Benefits

- ✅ Faster builds (smaller context, better caching)
- ✅ Faster pushes/pulls (less data to transfer)
- ✅ Lower storage costs
- ✅ Faster container startup
- ✅ Better GitHub Actions performance

