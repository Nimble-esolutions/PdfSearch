Status: Active
Audience: Developer, Release
Owner: FlowDocs maintainers
Last verified: 2026-08-02
Canonical source: docs/SECURITY_SCAN.md
Supersedes: None

# Image Security Scanning

## Current 2026-08-02 remediation

Trivy reported two HIGH findings against cryptography 45.0.7:

| Finding | Fixed version |
| --- | ---: |
| CVE-2026-26007 | 46.0.5 |
| GHSA-537c-gmf6-5ccf | 48.0.1 |

The repository remediation is PR #169. It constrains the application to
cryptography >=48.0.1,<49.0.0 and refreshes only the corresponding
requirements-web.lock entry and hashes. The hash-enforced resolver passed.
PR contract, source/deployment contract, and pre-merge certification passed.

The immutable candidate and Trivy scan intentionally run only after a push to
dev, so they are skipped on the pull request. Do not call PR green proof that
the new image is clean; merge, build the digest, scan that exact digest, and
record the result before stage deployment.

The stage currently remains on the previous certified b71 image and is not
activated by this dependency change. Retain that digest as the rollback
reference until the new image passes smoke, lifecycle, and Trivy gates.

## Current CI Behavior

The release workflow scans the exact published image digest with Trivy v0.72.0
after image smoke tests. Debian, Python, and library findings are written to the
GitHub Actions job summary. HIGH and CRITICAL findings are currently reported as
a non-blocking warning so the `dev` image build and compatibility-tag workflow
can continue; this is not an approval for production deployment.

Before production promotion, every HIGH or CRITICAL finding must be fixed,
explicitly accepted by the security owner, or documented as not applicable with
evidence. Do not suppress a finding only to make CI green.

The current production baseline is the Redis-enabled immutable image revision
from PR #53 at merged source `2e1ca38`. Promotion still requires the exact
published digest, not a tag or stale local image, plus the Compose, health, data
count, FAISS count, and representative-search gates.

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

## Environment Identity Security

`environment.py` enforces fail-closed environment identity at startup. The
`APP_ENV` variable must be set to `production`, `staging`, or `development`.
An unknown or missing value causes the application to refuse startup. This
prevents production credentials from being used in a misconfigured environment.

## Side-Effect Safety

`side_effects.py` gates all external calls (email, AI embeddings, AI chat)
through `EXTERNAL_SIDE_EFFECTS_MODE`. In `disabled` mode, all external calls
are no-ops. In `sandbox` mode, calls return fake responses. Only
`enabled` mode permits real external communication. The email backend in
`settings.py` is also gated by environment identity.

## AI Call Guarding

`ai_guard.py` wraps all OpenAI API calls (embeddings and chat completions).
Before any call, it checks `EXTERNAL_SIDE_EFFECTS_MODE` and the environment
identity. In non-production environments with side effects disabled, AI calls
return safe defaults instead of contacting OpenAI. This prevents accidental
API usage and billing in development and staging.

## Data Sanitization

`sanitize.py` provides a sanitization pipeline for production data before it
enters non-production environments. It strips PII, resets passwords, anonymizes
user identities, and replaces production file references with placeholder
values. Sanitized data is suitable for development, staging, and CI
environments.

## Global Writer Fencing

`global_writer.py` implements CAS-based mutual exclusion for the global writer
role. Only one instance may hold the writer lease at any time. The writer
record is stored as a CAS-guarded object in the artifact vault. Writer
handover requires an explicit epoch increment and takeover ceremony with a
recorded reason. This prevents split-brain publication races.

## Known Accepted Risks (2026-07-24)

### Health Endpoint Authentication
All health endpoints (`/livez`, `/readyz`, `/health/data/`, `/health/lease/`, `/health/metrics/`) are intentionally unauthenticated. They are only accessible via Traefik reverse proxy on the internal Docker network (port 8000 bound to 127.0.0.1). They must never be exposed on a public interface.

### Environment Variable Secrets
Credentials (`SECRET_KEY`, `OPENAI_API_KEY`, `ARTIFACT_VAULT_ACCESS_KEY`, `ARTIFACT_VAULT_SECRET_KEY`, `DJANGO_SUPERUSER_PASSWORD`) are passed as environment variables rather than Docker secrets. This is the current Dokploy deployment model. Migration to Docker secrets is planned for a future release.

### API Key Export in start.sh
`OPENAI_API_KEY` is exported in the entrypoint script for runtime availability. This makes it visible in `/proc/<pid>/environ` for processes with container access. Mitigation: production containers are single-purpose with no multi-tenant access.
