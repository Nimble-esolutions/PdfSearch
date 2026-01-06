# Redis Image Mirror Setup

## Overview

To avoid Docker Hub rate limits, we mirror the Redis image to GitHub Container Registry (GHCR).

## Initial Setup

1. **Manually trigger the mirror workflow:**
   - Go to GitHub Actions tab
   - Select "Mirror Redis Image to GHCR" workflow
   - Click "Run workflow" → "Run workflow"
   - This will pull `redis:7-alpine` from Docker Hub and push it to GHCR

2. **Verify the image is available:**
   ```bash
   docker pull ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine
   ```

## Automatic Updates

The workflow runs automatically:
- **Weekly**: Every Sunday at midnight (UTC) via cron schedule
- **Manual**: Can be triggered anytime from GitHub Actions UI
- **On workflow change**: When the mirror-redis.yml file is updated

## Usage

After the initial mirror, your docker-compose files will automatically use:
```yaml
image: ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine
```

## Authentication

If pulling from production server, authenticate with GHCR:
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

Or use a GitHub Personal Access Token with `read:packages` permission.

## Troubleshooting

**If workflow fails:**
- Check GitHub Actions logs
- Ensure repository has `packages: write` permission
- Verify GITHUB_TOKEN has necessary permissions

**If image pull fails:**
- Ensure you're authenticated with GHCR
- Check image exists: `docker pull ghcr.io/nimble-esolutions/pdfsearch/redis:7-alpine`
- Verify image visibility settings in GitHub repository

