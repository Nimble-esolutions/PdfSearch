Status: Released to dev image
Audience: Release
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/releases/2026-07-22-admin-operations-cockpit.md
Supersedes: None

# 2026-07-22 Admin Operations Cockpit Release

## Scope

This record covers the merge and `dev` image release for the Admin Operations
Cockpit work.

- Pull request: https://github.com/Nimble-esolutions/PdfSearch/pull/37
- Branch: `agent/admin-operations-cockpit`
- Base branch: `dev`
- Merge commit: `1962e127e1ebcff0b8b0ba08622656d8eeaacaae`
- GitHub Actions run: https://github.com/Nimble-esolutions/PdfSearch/actions/runs/29953324460
- Published image:
  `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:2a06853c49ad92dc0321d6e0f016926c4a2dd262aa976a6225eb8cc3c6837849`
- Compatibility tags promoted: `dev`, `latest`, `sha-1962e12`

## Delivered Changes

- Admin UI shell and Operations Cockpit redesign.
- Category-level Index Operations controls.
- Owner assignment workflow for PDFs needing owner review.
- OCR/stored-index repair path for image-only PDFs with stored searchable
  artifacts.
- Demo fixture seeding for 20 categories and 20 PDFs across English, Hindi, and
  Marathi.
- Admin UI HTTP smoke script.
- Marathi UI translation updates.
- Operations and SEO/AEO planning docs.

## Validation Evidence

Local validation before merge:

```bash
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr-merge.mo
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python manage.py test core.tests'
docker compose -f docker-compose.dev.yml exec -T web sh -lc 'cd /app/flowdocs && python /app/scripts/ci/admin_ui_smoke.py'
```

Observed local results:

- Marathi message catalog check passed.
- Django `core.tests` passed: 69 tests OK.
- Admin UI HTTP smoke passed.

GitHub Actions results for run `29953324460`:

- `Validate source and deployment contract`: success.
- `Build and publish dev release`: success.
- Published-image smoke through the actual entrypoint: success.
- Trivy image scan step completed.
- Image-size budget gate completed.
- Compatibility tag promotion completed and tags resolved to the tested digest.
- Release evidence step completed.

## Production Promotion Status

This record proves that a tested `dev` image was published. It does not prove
production promotion through Dokploy.

Before production promotion, record:

- Dokploy deployment ID.
- Dokploy checkout SHA.
- Rendered Compose image reference.
- Running container OCI revision and image digest.
- Route evidence for `ai-sahakar.net` and `www.ai-sahakar.net`.
- `/livez`, `/readyz`, Redis, PDF listing, static asset, and representative
  search results.
- Active `/app/data` volume identity and rollback data-generation reference.
