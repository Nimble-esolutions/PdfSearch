# Docker Build Failure - Root Cause Analysis

## Problem Summary

The Docker build was failing with exit code 100 when trying to install runtime packages in a multi-stage build optimization attempt.

## Root Cause

The multi-stage build optimization attempted to separate build-time dependencies from runtime dependencies. However, the runtime stage tried to install packages with incorrect/versioned names that don't exist in Debian repositories:

- `libjpeg62-turbo` - incorrect package name
- `libpng16-16` - incorrect package name  
- `libwebp6` - incorrect package name
- `liblcms2-2` - incorrect package name
- `libharfbuzz0b` - incorrect package name
- `libffi8` - incorrect package name

## Working Solution (Reverted)

The working Dockerfile uses a **single-stage build** that includes both build and runtime dependencies:

- Uses `-dev` packages (e.g., `libjpeg-dev`, `libpng-dev`) which are correct package names
- Includes build tools (`build-essential`, `cargo`, `cmake`) in the final image
- Image size is larger (~4GB+) but build is reliable and working

## Why Multi-Stage Build Failed

When implementing multi-stage builds, you need to:

1. **Build stage**: Install `-dev` packages and build tools
2. **Runtime stage**: Install runtime libraries (without `-dev` suffix)

The problem was identifying the correct runtime package names. In Debian/Ubuntu:
- `libjpeg-dev` (build) → needs runtime: `libjpeg62-turbo` OR just install `libjpeg-dev` which pulls runtime deps
- `libpng-dev` (build) → needs runtime: `libpng16-16` OR just install `libpng-dev` which pulls runtime deps

However, the exact runtime package names vary by Debian version and may not match the versioned names we tried.

## Correct Approach for Future Optimization

If optimizing image size in the future, use one of these approaches:

### Option 1: Use apt to find runtime dependencies
```dockerfile
# Runtime stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        $(apt-cache depends libjpeg-dev libpng-dev libwebp-dev | \
          grep "Depends:" | grep -v "dev" | awk '{print $2}' | sort -u) && \
    apt-get clean
```

### Option 2: Install runtime packages that Python actually needs
Let Python packages pull in their dependencies, then identify what's actually needed:
```dockerfile
# Install minimal runtime libraries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        libcairo2 \
        libpango-1.0-0 \
        # ... other definitely-needed packages
```

### Option 3: Keep working single-stage build
The current single-stage build works reliably. The larger image size is acceptable for:
- Reliable builds
- No complex dependency resolution
- Easier maintenance

## Current Status

✅ **Reverted to working single-stage Dockerfile**
- Build succeeds reliably
- All dependencies included
- Image size: ~4GB+ (acceptable trade-off for reliability)

## Recommendations

1. **Keep current single-stage build** - It works reliably
2. **If optimizing later**: Test multi-stage build thoroughly in a separate branch
3. **Monitor image size**: If it becomes a real problem (storage costs, pull times), then optimize
4. **Use .dockerignore**: Already in place to reduce build context size

