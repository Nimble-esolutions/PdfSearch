Status: Draft for approval
Audience: Maintainers
Owner: FlowDocs maintainers
Last updated: 2026-07-22

# SEO and AEO Optimization Plan

## Objective

Improve the public main search page for search engines, answer engines, and
assistive summaries without weakening the application security boundary or
misrepresenting AI-generated answers as official legal advice.

Primary page: `/` and `/search/`, rendered by `flowdocs/core/templates/search.html`.

## Approval Questions

1. Should the public page target the brand phrase **Sahakar AI** or the official
   department phrase **Registrar Co-operative Societies Maharashtra** as the
   primary title?
2. Should answer engines be allowed to index generated answers, or should only
   the static landing/search interface be indexable?
3. Which contact channels are official: WhatsApp, feedback form, department
   website, office address, or support email?
4. Should Marathi and English pages be exposed as separate canonical alternates
   or remain language-toggled versions of the same URL?

## Proposed Changes

### 1. Metadata Baseline

- Add a precise `<title>` for `/` and `/search/`.
- Add a static meta description that names the service, jurisdiction, and use
  case.
- Add canonical URL tags for `https://ai-sahakar.net/` and
  `https://www.ai-sahakar.net/`.
- Add Open Graph and Twitter card metadata for previews.
- Keep the legal disclaimer visible and include a concise machine-readable
  version in metadata where appropriate.

### 2. Structured Data

Add JSON-LD with conservative schema types:

- `WebSite` for the public site.
- `GovernmentOrganization` or `Organization` after confirming the preferred
  official identity.
- `SearchAction` only if the public search endpoint remains approved for
  anonymous/shared-corpus usage.
- `FAQPage` only for static guidance such as “how to ask better questions,” not
  for generated answers.

### 3. Answer Engine Optimization

- Add a short static “What this service does” section in HTML, not only inside
  JavaScript chat state.
- Add static guidance for good questions, reference verification, Marathi/English
  support, and legal-disclaimer boundaries.
- Preserve source-reference links in generated answers and avoid exposing private
  PDFs unless the server marks them public.
- Keep generated answer text out of permanent indexed HTML unless explicitly
  approved.

### 4. Technical SEO

- Add `robots.txt` and `sitemap.xml` routes or static files.
- Confirm production serves correct status codes for `/`, `/search/`, `/livez`,
  and `/readyz`; health endpoints should not be promoted as content pages.
- Add `hreflang` alternates if separate Marathi/English canonical URLs are
  approved.
- Ensure external links use `rel="noopener noreferrer"` and official links use
  accurate anchor text.
- Optimize hero/banner image dimensions and alt text for LCP and accessibility.

### 5. Content Quality

- Replace vague button/image alt text such as “Banner” or “Logo 4” with
  descriptive labels.
- Add static page copy that explains:
  - What document corpus can be searched.
  - That answers are generated assistance, not legal advice.
  - That references should be reviewed before official decisions.
  - How users can report incorrect or incomplete results.

## Verification Gates

Before approval:

```bash
python manage.py check
python manage.py test core
curl -fsS http://localhost:8000/ | python -m scripts.ci.validate_public_html
```

If no HTML validator exists yet, add a small CI script that checks:

- `<title>`, description, canonical, Open Graph, and JSON-LD are present.
- JSON-LD parses as JSON.
- No generated answer content is embedded in the initial HTML.
- External links with `target="_blank"` include `rel="noopener noreferrer"`.
- The disclaimer is present in English and Marathi.

Before production promotion:

- Google Rich Results / Schema validator check.
- Lighthouse SEO/accessibility pass on desktop and mobile.
- Public smoke for `https://ai-sahakar.net/` and `https://www.ai-sahakar.net/`.
- Confirm robots and sitemap behavior on the canonical domains.

## Rollout

1. Land metadata and static copy behind ordinary template changes.
2. Validate locally and in an isolated Dokploy preview.
3. Confirm canonical host and `ALLOWED_HOSTS` parity.
4. Merge into `dev` after checks pass.
5. Let the GitHub workflow publish the new immutable image.
6. Deploy through Dokploy only after image digest, Compose hash, route, and data
   generation evidence are recorded.

## Non-Goals

- Do not publish private PDF contents for SEO.
- Do not index generated answers unless explicitly approved.
- Do not add misleading legal-service claims.
- Do not change the production public-search allowlist without a separate data
  and policy review.
