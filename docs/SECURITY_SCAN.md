Status: Active
Audience: Developer, Release
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/SECURITY_SCAN.md
Supersedes: None

# Image Security Scanning

## Current CI Behavior

The release workflow scans the exact published image digest with Trivy v0.72.0
after image smoke tests. Debian, Python, and library findings are written to the
GitHub Actions job summary. HIGH and CRITICAL findings are currently reported as
a non-blocking warning so the `dev` image build and compatibility-tag workflow
can continue; this is not an approval for production deployment.

Before production promotion, every HIGH or CRITICAL finding must be fixed,
explicitly accepted by the security owner, or documented as not applicable with
evidence. Do not suppress a finding only to make CI green.

## Root Cause From Release `d25a5b4`

The failed release built successfully and passed the application smoke tests.
Trivy found:

| Package | Installed finding | Fixed version | Source |
|---|---:|---:|---|
| `jaraco.context` | `5.3.0` | `6.1.0` | Vendored inside base-image `setuptools` |
| `wheel` | `0.45.1` | `0.46.2` | Vendored inside base-image `setuptools` |

Neither package was requested by `requirements-web.lock`. The scan also showed
the separately installed top-level `wheel` metadata at `0.46.3` as clean. The
HIGH findings came from `/usr/local/lib/python3.10/site-packages/setuptools/_vendor/`:
the Python base image included build tooling that the runtime does not need.

## Implemented Fix

The runtime Docker stage installs the application wheels, then removes
`setuptools` and `wheel` before copying the application and starting Gunicorn.
The builder stage retains build tooling, but it is discarded from the runtime
image. This preserves application imports while removing the vulnerable vendored
metadata from the deployed image.

## Developer Verification

Run from the repository root:

```bash
docker build --platform linux/amd64 -t pdfsearch-security-check .
docker run --rm --platform linux/amd64 --entrypoint python \
  pdfsearch-security-check -m pip check
docker run --rm --platform linux/amd64 --entrypoint python \
  pdfsearch-security-check -c \
  'import importlib.metadata as m; print([d.metadata.get("Name") for d in m.distributions() if d.metadata.get("Name", "").lower() in {"setuptools", "wheel"}])'
```

The dependency check should report no broken requirements and the packaging-tool
check should print an empty list. GitHub Actions remains the authoritative Trivy
scan because it scans the pushed immutable digest with the release scanner.

## Remediation Order

1. Rebuild the image from the changed Dockerfile.
2. Confirm the image digest changed and the exact digest passes application smoke tests.
3. Review the Trivy job summary for HIGH and CRITICAL findings.
4. Update the base image or locked dependency when a finding remains.
5. Record the finding, fix version, image digest, and review decision before production.
