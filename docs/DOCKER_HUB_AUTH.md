# Docker Hub Authentication Setup

## Problem

Docker Hub has rate limits for unauthenticated pulls:
- **Anonymous**: 100 pulls per 6 hours per IP
- **Authenticated**: 200 pulls per 6 hours per user

## Solution: Authenticate with Docker Hub

### For Production Server

1. **Create Docker Hub account** (if you don't have one):
   - Go to https://hub.docker.com/signup
   - Create a free account

2. **Login on production server:**
   ```bash
   docker login
   # Enter your Docker Hub username and password
   ```

3. **Or use access token** (recommended for automation):
   ```bash
   echo "YOUR_DOCKER_HUB_TOKEN" | docker login -u YOUR_USERNAME --password-stdin
   ```

4. **Create access token:**
   - Go to Docker Hub → Account Settings → Security
   - Create new access token
   - Use token instead of password for login

### For GitHub Actions

GitHub Actions automatically authenticates with Docker Hub when using official images, so no additional setup needed.

### Alternative: Use Public Mirrors

If you prefer not to authenticate, you can use public mirrors:

**Option 1: Use Quay.io** (if available)
```yaml
image: quay.io/redis/redis:7-alpine
```

**Option 2: Use Mirrored Images**
Some organizations maintain public mirrors of popular images.

## Verification

After authentication, verify you can pull images:
```bash
docker pull redis:7-alpine
```

## Notes

- Free Docker Hub accounts get 200 pulls per 6 hours (plenty for most use cases)
- Authentication is free and easy to set up
- Official Redis image is always up-to-date and maintained by Redis team
- No need to build or mirror images yourself

