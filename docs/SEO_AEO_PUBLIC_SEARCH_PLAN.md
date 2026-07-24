# Public Search SEO and AEO Plan

## Objective

Make Sahakar AI discoverable for people searching for Maharashtra cooperative
society law, acts, rules, registration, audit, housing societies, elections,
disputes, and related official documents. The goal is useful, trustworthy,
crawlable content for Indian users in English and Marathi. No implementation
can guarantee a first position; ranking depends on relevance, authority,
competition, crawlability, and ongoing quality signals.

## Research conclusions

- Google says AI Overviews and AI Mode use the same core SEO foundations as
  ordinary Search. There is no special AI file or schema that guarantees
  inclusion.
- Important content must be available as visible text, and structured data
  must match the visible page content.
- Canonical URLs, sitemap URLs, and redirects must identify one preferred URL
  for each page. The current `/` and `/search/` routes render the same page.
- Google recommends distinct URLs for localized versions. The current
  cookie-driven language switch cannot provide reliable crawlable English and
  Marathi variants, so hreflang should not claim more than the site supports.
- The Maharashtra Department of Cooperation, Marketing and Textiles identifies
  the Maharashtra Cooperative Societies Act, 1960 and Rules, 1961, along with
  registration, bylaws, audit, inspection, disputes, liquidation, and appeals
  as central public topics. Those topics should become first-class, crawlable
  pages rather than only prompt suggestions.

## Findings in the current application

1. `/` and `/search/` are duplicate GET pages but both appear in the sitemap.
2. `SearchAction` advertises a query URL that renders the shell but does not
   server-render a query result.
3. FAQ JSON-LD is emitted while its matching visible content is optional and
   disabled by default.
4. The homepage is mostly an interactive JavaScript application; its useful
   explanatory copy is too thin when the service footer is disabled.
5. Health endpoints are included in the sitemap even though they are not
   search content.
6. English and Marathi alternates currently point to the same URL and rely on
   language state rather than stable locale URLs.

## Delivery phases

### Phase 1: technical eligibility and truthful markup

- Redirect GET `/search/` to the canonical homepage while retaining POST
  `/search/` for the existing JavaScript API contract.
- Keep only canonical, indexable public content URLs in the sitemap.
- Remove or replace unsupported `SearchAction` markup.
- Make the visible service explanation and FAQs default-on so JSON-LD and page
  content agree.
- Add `WebPage`, `Organization`, and `WebSite` data only where the values are
  visible or directly supported by official source links.
- Add automated checks for canonical uniqueness, sitemap hygiene, JSON-LD
  validity, and no accidental admin indexing.

### Phase 2: multilingual discovery

- Add stable `/en/` and `/mr/` homepage URLs with self-referencing canonicals.
- Emit complete reciprocal `hreflang` annotations only after both variants
  contain genuinely translated primary content.
- Keep the existing language switch working and redirect legacy cookie-only
  navigation to the stable locale URL.
- Add Marathi titles, descriptions, headings, FAQs, and topic labels authored
  for public comprehension rather than machine-translated filler.

### Phase 3: topical authority and answer extraction

- Create crawlable topic pages for the Act, Rules, society registration,
  cooperative housing societies, audit and inspection, elections, disputes,
  liquidation, and appeals.
- Give every topic page a short answer-first summary, source authority block,
  last-reviewed date, related official documents, and links back to search.
- Create stable, public document detail pages with HTML metadata and protected
  PDF access. Do not expose private media or unpublished documents.
- Link the homepage to the highest-value topics using descriptive anchor text.
- Use visible question-and-answer sections for genuine public questions. Treat
  FAQ markup as secondary semantics, not a ranking shortcut.

### Phase 4: trust, performance, and measurement

- Show source title, issuing authority, document date, and source link for every
  generated answer where available.
- Make the legal disclaimer prominent without weakening answer usefulness.
- Optimize images, font loading, cache headers, and JavaScript so first content
  is useful before the search bundle finishes loading.
- Connect Google Search Console and Bing Webmaster Tools, submit the sitemap,
  inspect indexing, and monitor queries for English and Marathi separately.
- Measure impressions, clicks, indexed pages, Core Web Vitals, unanswered
  topics, source click-through, and answer quality. Do not use fabricated
  keyword volumes or ranking guarantees.

## Acceptance criteria

- One canonical homepage URL and no duplicate homepage URL in the sitemap.
- All structured data parses and describes visible content.
- Public content remains crawlable without executing the search request.
- Admin, authentication, health, and private document routes remain excluded.
- English and Marathi pages are independently crawlable before `hreflang` is
  expanded.
- Search policy, rate limits, CSRF protection, public folder scope, and
  protected document URLs remain unchanged.

## Primary references

- Google Search Essentials: https://developers.google.com/search/docs/essentials
- Google SEO Starter Guide: https://developers.google.com/search/docs/fundamentals/seo-starter-guide
- Google AI features and your website: https://developers.google.com/search/docs/appearance/ai-features
- Google canonicalization: https://developers.google.com/search/docs/crawling-indexing/canonicalization
- Google multilingual sites: https://developers.google.com/search/docs/advanced/crawling/managing-multi-regional-sites
- Google structured data policies: https://developers.google.com/search/docs/appearance/structured-data/sd-policies
- Maharashtra Acts and Rules: https://mahasahakar.maharashtra.gov.in/en/document-category/acts-rules/
- Commissionerate and Registrar responsibilities: https://mahasahakar.maharashtra.gov.in/en/commissioner-cooperation-and-rcs/
