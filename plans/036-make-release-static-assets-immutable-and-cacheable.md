# Plan 036: Make release static assets immutable and cacheable

> **Executor instructions:** Static assets are release code, not mutable data.
> Read `AGENTS.md`, `docs/HANDOFF.md`, the Docker entrypoints, and the compose
> files before editing. This plan does not authorize deleting a volume,
> rebuilding an active stage container in place, or changing production
> routing.

## Status

- **Priority:** P1
- **Effort:** M
- **Risk:** MED
- **Depends on:** Plan 006 verification gate
- **Planned at:** `3591956` on 2026-08-07
- **Roadmap status:** TODO

## Objective

Serve hashed static assets from the immutable application image, configure
Django's supported static-files storage setting, and stop collecting static
files into the mutable data volume at process startup. This removes repeated
startup work, enables safe long-lived browser caching for fingerprinted assets,
and prevents a data restore from silently changing application code assets.

## Evidence and root cause

| Finding | Source evidence | Root cause | Impact |
| --- | --- | --- | --- |
| The image builds static assets with `collectstatic`, but runtime runs it again. | `Dockerfile` collects assets; `start.sh` creates `$DATA_ROOT/staticfiles` and invokes `python manage.py collectstatic --noinput`. | Build-time and runtime asset ownership are mixed. | Every start performs avoidable I/O and may depend on mutable state. |
| Static root defaults beneath `DATA_ROOT`. | `flowdocs/flowdocs/settings.py` derives `STATIC_ROOT` from the data root; compose files set `/app/data/staticfiles`. | Release assets were treated like restored application data. | A data volume can retain stale or conflicting assets. |
| The intended WhiteNoise backend is set through the legacy setting. | `STATICFILES_STORAGE` is present without a `STORAGES["staticfiles"]` configuration. | Django's supported storage configuration was not adopted with the current runtime version. | Manifest/compression behavior can silently differ from the intended release contract. |
| Current static responses are short-cacheable. | Stage observation showed static `Cache-Control` around one minute despite fingerprintable assets. | Mutable static location prevents treating hashed paths as immutable. | Repeat visits revalidate assets unnecessarily and first-load speed suffers on constrained networks. |

## Target architecture

```text
Docker build
  source static + collectstatic
             |
             v
      /app/staticfiles (image layer, manifest + compressed variants)
             |
             v
WhiteNoise serves fingerprinted release assets
             |
             v
browser immutable cache for hashed URLs

Mounted /app/data
  database, uploads, indexes, control state only
  never release static assets
```

An old `/app/data/staticfiles` directory may remain on an existing volume after
the release. It is intentionally ignored, not deleted. Reclaiming its disk
space is a separately approved, inventory-first maintenance action.

## Scope and non-goals

### In scope

1. Move the runtime static target to an image-owned path such as
   `/app/staticfiles`.
2. Configure `STORAGES` with an explicit staticfiles backend using
   WhiteNoise's compressed manifest storage, while preserving the project's
   default Django file storage behavior explicitly.
3. Run `collectstatic` exactly at image build for release assets; remove only
   the runtime static-asset collection/copy path from startup/bootstrap.
4. Render all static template references through the manifest and verify that
   every public/admin asset referenced by a release exists in the image.
5. Make cache headers long-lived only for fingerprinted assets; keep HTML,
   manifests, and mutable response classes correctly revalidated.

### Out of scope

- Media/uploads, PDFs, SQLite, FAISS/Chroma, RustFS/DataOps, or user data
  migration.
- A CDN, new object storage bucket, a JavaScript bundler, or a CSS redesign.
- Removing any existing Docker volume or running `docker compose down -v`.
- Altering the active `PDFSEARCH_IMAGE` tag policy or proxy routing.

## Implementation slices

### Slice A — prove the current asset graph

1. Build the current source image in an isolated local environment and list
   the generated manifest, hashed assets, compressed siblings, and their paths
   without exposing unrelated filesystem contents.
2. Inventory template `{% static %}` calls, CSS `url(...)` assets, JavaScript
   dynamic asset paths, and admin assets. Classify each as fingerprintable,
   intentionally unhashed, or a defect to fix.
3. Confirm how the current Django version resolves `STORAGES`,
   `STATICFILES_STORAGE`, and default file storage. Add a focused regression
   test so a future Django upgrade cannot silently fall back to a non-manifest
   backend.

### Slice B — make the image the sole static producer

1. Set a single image-owned `STATIC_ROOT`; make the Docker build invoke
   `collectstatic` there after all static source is present.
2. Replace legacy static configuration with an explicit `STORAGES` dictionary
   containing both `default` and `staticfiles` entries. Preserve any existing
   media/default-storage configuration exactly; Django does not merge omitted
   storage definitions for us.
3. Remove the runtime `collectstatic` call and any legacy static-file copy from
   `start.sh`, entrypoints, and bootstrap code. Continue to bootstrap only
   declared data artifacts (database, media, indexes, and control state).
4. Remove or update compose `STATIC_ROOT` settings so web and maintenance
   containers see the image-owned static path. Do not mount a static volume.
5. Keep `whitenoise.middleware.WhiteNoiseMiddleware` in the correct middleware
   order and test direct static responses through the same Gunicorn/WhiteNoise
   path used in production.

### Slice C — certify caching and rollout behavior

1. Verify HTML references hashed filenames and that every requested static URL
   is present in the image manifest.
2. Verify immutable cache headers only on content-hashed files. Define the
   expected policy in a regression test rather than depending on a browser
   observation. Verify compressed and uncompressed variants produce the same
   content type and cache policy.
3. Run the Compose smoke test twice against the same image and a fresh
   disposable data volume. The second start must not run `collectstatic`,
   write static artifacts into `/app/data`, or depend on stale volume content.
4. Stage deploy only after the image digest and static manifest hash are
   recorded. Test a normal browser, a hard reload, and a previous browser cache
   against the new image before considering cache policy complete.

## File-level implementation map

| File / area | Intended change | Guardrail |
| --- | --- | --- |
| `flowdocs/flowdocs/settings.py` | Explicit `STORAGES` configuration and image static root default. | Preserve default/media storage semantics; do not change user-upload paths. |
| `Dockerfile` | Build manifest and compressed assets into the immutable image path. | Fail image build if manifest generation fails. |
| `start.sh`, entrypoints, bootstrap helpers | Remove runtime static generation/import only. | Do not change data activation, migrations, or recovery behavior in this slice. |
| `docker-compose*.yml`, deployment examples | Stop declaring `/app/data/staticfiles` as runtime static root. | Keep data/control/Redis volumes intact; validate compose render. |
| Templates/CSS/JS static references | Fix non-manifest paths discovered in Slice A. | No remote/CDN dependency or inline assets introduced. |
| Tests and smoke scripts | Add manifest/header/no-runtime-write coverage. | Exercise web and maintenance image entrypoints. |

## Impact analysis

| Area | Expected effect | Required guardrail |
| --- | --- | --- |
| First load and repeat visits | Browser can reuse fingerprinted CSS, JavaScript, and marks safely. | Never assign immutable caching to HTML or an unhashed asset. |
| Startup | Less runtime I/O and fewer mutable filesystem dependencies. | Container must fail loudly if its image lacks a static manifest. |
| Recovery | Restoring data no longer changes code assets. | No data-volume removal or destructive cleanup is bundled. |
| Theme integrity | Both Classic and Workbench reference the image's same release manifest. | Do not share theme CSS or change their asset ownership boundaries. |
| Admin/public UI | Django admin and public pages keep working with compressed manifest paths. | Cover login, settings, legal/error, Classic, and Workbench routes. |
| Rollback | Prior image retains its own static bundle. | Roll back the image as a whole; never mix a new static directory with an old image. |

## Verification matrix

| Layer | Required proof |
| --- | --- |
| Django | `collectstatic --noinput`, manifest lookup, `manage.py check`, and migrations check succeed in the built image. |
| Static contract | Hashed URL exists, content type is correct, cache header matches the documented fingerprint policy, and missing manifest entries fail build/test. |
| Startup | Web and maintenance start without runtime `collectstatic`; `/app/data` remains free of newly written static artifacts. |
| UI | Classic, Workbench, public legal/error pages, admin login/settings, English/Marathi, and mobile headers load all required assets. |
| Compose | `docker compose config` and the repo smoke test run against an immutable source image. |
| Stage | Resolved image digest, manifest hash, static header samples, `/livez`, `/readyz`, and user-facing route checks are recorded without secrets. |

## Rollout, rollback, and stop conditions

Deploy a fully built, digest-pinned image to stage. Keep the existing data and
control volumes mounted but do not use their old static directory. If a static
asset is missing, cache headers are applied to non-fingerprinted content, or a
previous browser cache displays mixed release assets, revert by redeploying the
previous complete image. Do not attempt a partial file-level rollback inside a
volume.

Stop and seek review if a static reference cannot be made manifest-safe, if
the deployment platform overwrites image paths with a volume mount, if default
file storage would change, or if a proposed optimization needs an unreviewed
CDN/proxy configuration.

## Done criteria

- Release static assets live only in the immutable image and have a valid
  WhiteNoise manifest/compressed representation.
- Runtime startup does not collect, copy, or mutate static assets.
- Fingerprinted assets have a tested immutable cache policy; HTML does not.
- Existing data/control volumes remain untouched and compatible.
- Stage verification proves both public themes and admin routes work from one
  resolved image digest.
