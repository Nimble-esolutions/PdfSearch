# Redis Image Mirror Setup (GHCR)

## Overview

We mirror the official Redis image from Docker Hub to GitHub Container Registry (GHCR) to avoid rate limits and ensure always up-to-date images.

## How It Works

1. **GitHub Actions workflow** automatically pulls the latest Redis image from Docker Hub
2. **Mirrors it to GHCR** as `ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine`
3. **Runs automatically** daily at 2 AM UTC to keep images updated
4. **Can be triggered manually** from GitHub Actions UI

## Initial Setup

1. **Trigger the mirror workflow manually:**
   - Go to GitHub → Actions tab
   - Select "Mirror Redis Image to GHCR" workflow
   - Click "Run workflow" → "Run workflow"
   - Wait for it to complete (takes ~2-3 minutes)

2. **Verify the image is available:**
   ```bash
   docker pull ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine
   ```

## Automatic Updates

The workflow runs automatically:
- **Daily**: Every day at 2 AM UTC via cron schedule
- **Manual**: Can be triggered anytime from GitHub Actions UI
- **On workflow change**: When mirror-redis.yml file is updated

## Usage

Your docker-compose files automatically use:
```yaml
image: ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine
```

## Authentication

### For Production Server

If pulling from production server, authenticate with GHCR:

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

Or use a GitHub Personal Access Token with `read:packages` permission:

```bash
echo "YOUR_GITHUB_TOKEN" | docker login ghcr.io -u YOUR_USERNAME --password-stdin
```

### For GitHub Actions

No authentication needed - uses `GITHUB_TOKEN` automatically.

## Available Image Tags

- `ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine` - Alpine-based Redis 7 (recommended)
- `ghcr.io/nimble-esolutions/pdfsearch/redis:latest` - Same as 7-alpine
- `ghcr.io/nimble-esolutions/pdfsearch/redis:latest-full` - Full Redis image

## Troubleshooting

**If workflow fails:**
- Check GitHub Actions logs
- Ensure repository has `packages: write` permission
- Verify GITHUB_TOKEN has necessary permissions

**If image pull fails:**
- Ensure you're authenticated with GHCR
- Check image exists: `docker pull ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine`
- Verify image visibility settings in GitHub repository (should be public or accessible)

**If rate limit error:**
- The workflow runs in GitHub Actions which has authenticated access to Docker Hub
- No rate limits when pulling from Docker Hub in GitHub Actions
- GHCR has no rate limits for authenticated users

## Benefits

- ✅ No Docker Hub rate limits (workflow has authenticated access)
- ✅ No GHCR rate limits (authenticated access)
- ✅ Always latest Redis version (daily updates)
- ✅ Reliable and fast (GHCR is fast and reliable)
- ✅ Free (GitHub Actions free tier is sufficient)

