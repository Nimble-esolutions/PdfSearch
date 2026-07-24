# Hallmark Public Search Redesign

## Evidence and defect

- `flowdocs/core/templates/search.html:127-180` uses a raster banner, absolute text placement, inline styles, and image-based support controls.
- `flowdocs/core/static/main/css/search.css:1-166` uses fixed `height:80vh`, generic rounded chat bubbles, mixed colors, and a mobile rule that reduces support images to approximately 10px.
- `flowdocs/core/static/main/js/search.js:18-100` has no request timeout/abort path; result rendering uses a 20ms typewriter effect and emoji labels.
- `flowdocs/flowdocs/settings.py:204-226` contains public-search visibility and rate-limit policy that must remain a server-side safety boundary.

## Target experience

Make the search surface an official, calm document-service interface: identity and purpose first, prominent search input, language-aware query affordance, transparent result/source metadata, and a useful empty/error/no-result state. Preserve the current Hallmark identity direction and purge competing themes only after parity and visual QA. Do not turn the page into a generic AI chat screen.

Use responsive CSS grid/flex, intrinsic sizing, logical properties, `content-visibility` only where measured, optimized local assets, explicit image dimensions, lazy loading for below-fold media, and a controlled font strategy. Support 320px–1440px+ widths, touch targets, keyboard navigation, reduced motion, Marathi/English text, and high contrast.

## Backend and performance boundaries

Keep public folder scoping, authentication/authorization, rate limiting, query limits, and retrieval semantics on the server. Add bounded request cancellation, request IDs, safe error categories, and server-timed metadata. Preserve streaming/progressive results only if the API contract supports it; never expose internal prompts, stack traces, storage keys, or hidden folders.

## Implementation steps

1. Freeze the canonical Hallmark search route and capture feature parity for query, references, feedback, support, and language actions.
2. Replace absolute banner text and inline layout with semantic responsive markup; retain approved institutional assets and Nimble only as partner credit where appropriate.
3. Rebuild result, reference, feedback, and support components with accessible controls and explicit loading/error states.
4. Add abort/timeout, debouncing where appropriate, bounded rendering, and performance instrumentation to the existing JS without changing search policy.
5. Remove unused alternate theme assets/styles after dependency scan and parity tests prove they are not needed.
6. Run responsive visual and accessibility checks before adoption of any logo variant.

## Done criteria

- Search works at mobile, tablet, desktop, and widescreen sizes without clipping, horizontal scroll, or tiny support controls.
- First meaningful render and interaction remain within agreed budgets on a cold and warm cache; large assets are measured and optimized.
- Keyboard, screen-reader labels, focus states, reduced motion, Marathi text, and error recovery are covered.
- Public visibility and rate-limit tests remain green; no server-side safety policy is weakened.
