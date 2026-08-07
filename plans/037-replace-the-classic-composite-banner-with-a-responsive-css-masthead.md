# Plan 037: Replace the Classic composite banner with a responsive CSS masthead

> **Executor instructions:** Preserve the approved Classic public identity
> while replacing only its composite header implementation. Do not touch the
> Workbench visual system, dashboard/admin UI, search response contract, or
> public theme-selection semantics. Read the public UI contract and run the
> visual accessibility gates before pushing.

## Status

- **Priority:** P2
- **Effort:** M
- **Risk:** MED
- **Depends on:** Plan 006 verification gate; validate against Plan 036 if its
  static-pipeline change has already landed
- **Planned at:** `3591956` on 2026-08-07
- **Roadmap status:** TODO

## Objective

Replace the fixed-height `search-classic-banner*.webp` composite image with a
semantic, CSS-first Classic masthead that retains the two official marks as
individual approved assets. The result must match the approved Classic visual
language across 320px mobile, desktop, short landscape, legal pages, and
public error pages—without hard-coded title offsets, heavy bitmap background,
or hidden text inside a banner image.

## Evidence and root cause

| Finding | Source evidence | Root cause | Impact |
| --- | --- | --- | --- |
| The header is a composite bitmap with overlaid semantic text. | `components/public/classic_header.html` renders `search-classic-banner.webp` / mobile variant and an absolute title block. | The historic visual was delivered as one image instead of a layout. | Bitmap weight, brittle crops, and translation/responsive constraints. |
| Mobile positioning relies on percentage padding and fixed banner heights. | `search-classic.css` uses `.classic-banner__title` percentage padding and 128px/125px breakpoint heights. | The text is positioned against a fixed image rather than a responsive layout grid. | Narrow devices, zoom, Marathi text, and short landscape can crop or overlap. |
| Legal/error headers repeat the same visual implementation. | `public-legal.css`, `public-legal.spec.ts`, and `public-error.spec.ts` reference `.classic-banner`. | The header component is shared inside the Classic family, but its CSS duplicates image assumptions. | A search-only fix would leave public information and recovery pages visually inconsistent. |
| Existing browser tests correctly require a semantic Classic title. | `browser_tests/classic-search.spec.ts` asserts `.classic-banner h1`. | The semantic title must be preserved through the visual refactor. | Accessibility and automated release evidence could regress silently. |

## Design decision

Keep the two official emblems as approved individual image files. “CSS-first”
does **not** mean redrawing or synthesising official emblems in CSS; it means
CSS owns the responsive layout, gradient surface, spacing, type hierarchy, and
state behavior. Remove the composite banner assets from active templates after
all consumers are migrated and an asset inventory confirms they are not used by
another allowed legacy route.

```text
Classic masthead
├─ utility navigation (existing accessible links/language control)
└─ civic identity bar
   ├─ approved Maharashtra/Registrar mark (decorative or accurately labelled)
   ├─ service identity
   │  ├─ h1 on search; neutral heading/p on policy/error pages
   │  └─ Registrar Co-operative Societies subtitle
   └─ approved national emblem (decorative or accurately labelled)
```

Use CSS Grid for the identity bar, with `minmax(0, 1fr)` for the central text
column and intrinsic-size side columns. Do not use absolute positioning or
percentage title padding. The central text must be allowed to wrap safely, and
the two marks must shrink within defined minimum/maximum bounds without
causing horizontal overflow.

## Scope and non-goals

### In scope

1. Replace the Classic header component markup and Classic-family CSS for
   search, legal, and safe public error pages.
2. Preserve the approved dark utility strip, official marks, centered English
   and Marathi service identity, cream-to-cyan civic surface, and footer/body
   flow from the approved Classic direction.
3. Establish responsive behavior at 320, 360, 480, 768, 1024, 1440px and
   short landscape heights; use logical properties and safe-area padding.
4. Add accessible image treatment, heading semantics, keyboard-visible focus,
   44px utility-nav targets, robust focus ring styling, and reduced-motion
   behavior.
5. Remove only now-unused composite banner assets in a later, isolated cleanup
   commit after template/CSS/test/source-history inventory confirms they have
   no supported consumer.

### Non-goals

- No redesign of Workbench, administrator pages, the public footer, chat
  composer, result rendering, or answer behavior.
- No Bootstrap/CDN/framework/inline script, new bitmap art, CSS-drawn state
  emblems, or dependency on external image hosting.
- No change to `?view=workbench`, primary theme settings, analytics event
  semantics, public/legal/error routing, or authentication.

## Implementation slices

### Slice A — asset, semantic, and responsive contract

1. Inventory `logo1.png`, `logo2.png`, `national-emblem.svg`,
   `registrar-seal.webp`, and the composite banner files. Confirm which exact
   approved marks correspond to the existing public identity before swapping
   them; do not infer a logo's official role from its filename alone.
2. Define a compact component API for the Classic family: search pages render
   one `<h1>`, while policy/error pages retain the existing non-duplicative
   heading semantics. The component must accept translated subtitle/link labels
   but never need Workbench variables.
3. Establish a visual reference sheet from approved screenshots and the
   current legacy/public theme. Record desired type scale, mark aspect ratio,
   contrast, and max line count—rather than attempting pixel equality against
   a bitmap at every viewport.

### Slice B — semantic layout and CSS implementation

1. Replace `<picture>` composite-banner markup with a single semantic header
   layout: visual mark elements, a central service identity block, and the
   existing utility navigation. Set explicit intrinsic width/height for each
   retained raster asset to avoid layout shift.
2. Implement the masthead with a CSS grid and custom properties local to the
   Classic CSS files. Use `min-width: 0`, `overflow-wrap`, `clamp()`, logical
   padding, `env(safe-area-inset-*)`, and a real color/contrast token set.
3. Add explicit breakpoints only where layout needs them. At narrow widths,
   preserve title readability before mark size; at short landscape heights,
   reduce nonessential vertical spacing without collapsing touch targets.
4. Replace duplicated Classic header rules in `search-classic.css` and
   `public-legal.css` with equivalent local component rules or a narrowly
   scoped static component stylesheet used only by the Classic family. Do not
   import it into Workbench or admin pages.
5. Ensure visible keyboard focus is a single intentional outline/ring. Avoid
   `outline: none`, `transition: all`, animation that causes layout shift, and
   styling browser focus twice.

### Slice C — public route and regression certification

1. Test Classic search, English/Marathi language toggle, public policy pages,
   and 400/403/404/500 recovery pages at the required viewport matrix.
2. Verify error pages still contain no search composer, search script, tracker
   configuration, or tracker script. Theme consistency must not introduce
   interaction or analytics on recovery pages.
3. Run keyboard-only and screen-reader checks for heading order, alternate-text
   decision, utility navigation, skip link, language control, and zoom to
   200%. Check horizontal overflow on real narrow viewport emulation.
4. Capture visual snapshots only after the behavior tests pass. Review image
   diff changes against the approved Classic direction, not an arbitrary
   browser's font rasterization.

## File-level implementation map

| File / area | Intended change | Guardrail |
| --- | --- | --- |
| `flowdocs/core/templates/components/public/classic_header.html` | Semantic masthead markup and retained heading contract. | No Workbench variable/template sharing. |
| `flowdocs/core/static/main/css/search-classic.css` | Search-route Classic masthead layout. | Preserve composer continuity and safe viewport sizing. |
| `flowdocs/core/static/main/css/public-legal.css` | Legal/error Classic masthead layout. | Error pages remain static, no tracker/search behavior. |
| `flowdocs/core/static/main/images/*` | Retain approved individual marks; remove composite assets only after an isolated proof. | No unapproved asset substitution or heavy replacement bitmap. |
| `browser_tests/classic-search.spec.ts` | Preserve visible semantic title and search continuity. | Test more than screenshot appearance. |
| `browser_tests/public-legal.spec.ts`, `browser_tests/public-error.spec.ts` | Verify theme-consistent header and safe error behavior. | Cover all supported error status paths. |

## Impact analysis

| Area | Expected effect | Required guardrail |
| --- | --- | --- |
| Performance | Removes the composite banner network/decoding cost and prevents responsive image duplication. | Measure image transfer and LCP before/after; do not claim a gain without evidence. |
| Accessibility | Real text and logical order replace text positioned on a bitmap. | Keep meaningful heading hierarchy and image alternative treatment truthful. |
| Localization | Marathi subtitle/title can wrap without image edits. | Verify script/font/rendering in both languages. |
| UI consistency | Legal/error routes use the same Classic identity language. | Workbench remains visually and technically isolated. |
| Security/privacy | No new script, font CDN, analytics behavior, or remote asset is introduced. | Public recovery pages stay analytics-free. |
| Rollback | Revert component/CSS/image references together. | Keep composite files through the first verified release until cleanup is approved. |

## Verification matrix

| Layer | Required proof |
| --- | --- |
| Template | Search page has one Classic `h1`; policy/error pages preserve non-duplicative headings; static asset references resolve. |
| Accessibility | Axe/no critical violations, keyboard focus, screen-reader heading order, link targets, 200% zoom, and no horizontal scroll. |
| Browser | 320/360/480/768/1024/1440 and short landscape screenshots for Classic search, legal, and each public error route. |
| Continuity | Long answer scroll, composer remains reachable, language toggle, public links, source access, and focus behavior all work. |
| Regression | Workbench screenshots/behavior and admin pages remain unchanged; manifest/static pipeline gate runs if Plan 036 landed. |

## Rollout, rollback, and stop conditions

Deploy only the candidate image to stage after local source-backed browser
coverage passes. Roll back the entire image if marks are incorrect, the title
is cropped, a layout exceeds the viewport, the composer loses continuity, or a
legal/error route diverges from the selected theme.

Stop and request product/legal review if the approved emblem asset cannot be
identified, a route needs a third visual system, preserving the legacy image
would be required for copyright/authority reasons, or a proposed change would
put a tracker/search interaction on an error page.

## Done criteria

- Classic uses a semantic, CSS-responsive masthead rather than an active
  composite banner image.
- The approved public identity remains recognizable across Classic search,
  legal, and safe error routes.
- Classic is responsive and accessible across the supported viewport/language
  matrix, with no composer/scroll regression.
- Workbench/admin presentation and public analytics boundaries are unchanged.
