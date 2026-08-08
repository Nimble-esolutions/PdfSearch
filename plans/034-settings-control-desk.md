# Plan 034: Make Settings & Configuration a safe operator control desk

> **Process correction (2026-08-08):** implementation edits began before this
> plan was presented for review. That sequencing was incorrect. Work was paused,
> audited against PRs #195–#197 and the supplied Classic reference, and
> re-planned before implementation continued. Future redesign work must publish
> its audit, impact boundary, and acceptance contract before code changes begin.

> **Executor instructions:** This plan is intentionally separate from the
> consent-led analytics implementation. It modernizes the existing settings
> center in small, verifiable slices; it does not create a second configuration
> system, revive a VaultOps UI, or turn deployment-only values into web edits.

## Status

- **Priority:** P1
- **Effort:** M
- **Risk:** MED
- **Depends on:** Plan 002 registry reconciliation; Plan 006 verification gate
- **Planned:** 2026-08-07
- **Roadmap status:** Implementation candidate on `feat/settings-control-room`;
  local verification complete, PR and deployment proof pending

### Merged-PR reconciliation (2026-08-08)

| PR | Preserve | Correct on this branch |
| --- | --- | --- |
| #195 | Living-handoff discipline | Its integration snapshot became stale after later merges |
| #196 | Host-scoped analytics, GPC, pseudonymous identity, themed error pages, Classic formatting | Remove the duplicate consent concept; restore long-answer document scrolling; make settings ownership and roles explicit |
| #197 | Performance, responsive-masthead, and Umami capability plans | Do not claim its documentation as implemented runtime behavior |

## Why this plan exists

The page currently combines environment identity, a public-search choice,
analytics, object-store health, editable runtime settings, and an exhaustive
configuration inventory in one flat, long scroll. All sections use near-equal
visual weight, while a save error returns to the page top through a generic
flash message. An operator therefore has to remember where a setting was,
whether it is live, whether it needs confirmation, and what to do next.

The corrected safety model is explicit environment → saved override →
application default, runtime editing is allowlisted, high-impact changes require a
server-side confirmation, and secrets are redacted. The experience must make
those guarantees obvious instead of making users discover them after a failed
submission.

## Evidence and root causes

| Finding | Evidence | Root cause | Impact |
| --- | --- | --- | --- |
| The page has no “next safe action” or posture summary. | `flowdocs/core/templates/dashboard_settings.html:7-304` renders six peer sections in source order. | Information architecture reflects backend groups, not an operator task. | Important warnings and safe actions are easy to miss. |
| Read-only environment facts dominate before editable work. | `dashboard_settings.html:14-94` renders three equal cards before every control. | Facts and actions share the same card pattern. | A user cannot quickly distinguish context from an actionable change. |
| Errors lose their field and section context. | `flowdocs/core/views.py:2669-2675` stores generic flash messages then redirects; `base.html:122-129` renders them above all content. | PRG feedback has no field key, section anchor, or preserved draft. | Operators re-scan a long page and can resubmit the wrong value. |
| Confirmation appears required for every runtime save even though the server requires it only for changed high-impact controls. | `dashboard_settings.html:269-276`; actual policy at `views.py:2673-2675`. | The template is a blanket safety affordance rather than a change-aware one. | Routine low-risk edits look harder than they are; required risk review is diluted. |
| Runtime controls lack local change review and concurrency protection. | `views.py:2643-2682` compares the current value and writes it without a submitted revision token. | No UI-level diff or optimistic-concurrency contract. | Two superadmins can silently overwrite each other; no receipt proves the effective result. |
| Inventory is exhaustive by default and visually repeats the editable controls. | `dashboard_settings.html:281-303`; `configuration_registry.py:55-148`. | Registry output is rendered as a full table, not filtered by operator intent. | High cognitive load, slow mobile scanning, and redundant information. |
| Input semantics and validation feedback are incomplete. | `dashboard_settings.html:258` applies `inputmode="numeric"` to every non-select input; field errors are not associated with controls. | Rendering metadata lacks validation/error presentation state. | Assistive technology and mobile keyboards receive inaccurate hints; fixes are not local. |
| Analytics administration now shows host, tier, Website-ID source, and a safe enabled/disabled state, but has no tenant reachability, audited receipt, or focused error recovery. | `dashboard_settings.html:140-190`; `core/product_analytics.py`. | It correctly separates host configuration from remote tracker evidence, but remains a flat form with generic redirect messages. | A superadmin can configure the safe boundary, but still lacks a reviewable before/after change, section-local validation, and explicit independent-service proof. |
| Recovery status is a health fact, but its location encourages it to be treated as a setting. | `dashboard_settings.html:191-219`. | Controls and observability live in the same narrative. | Operators may expect backup/restore behavior to be edited here instead of using Operations. |

## Design direction

**Intent.** A superadmin has arrived after noticing a public-search problem,
an operations warning, or a request to alter presentation. They must make the
smallest safe change, see when it takes effect, and leave evidence for the next
operator. The page should feel like an official registrar’s control desk:
calm, accountable, and precise—not like a generic SaaS preferences wall.

| Design exploration | Decision |
| --- | --- |
| **Domain** | Registrar’s file desk; signed ledger; seal and authority; custody chain; public-service operating room; reviewed amendment. |
| **Color world** | Parchment paper, maroon seal ink, brass divider, charcoal record text, muted filing-card gray, ledger green for verified state. |
| **Signature** | A compact **operating-posture strip** paired with a **change receipt**: *scope → current effective state/source → consequence → confirmation → receipt*. It replaces generic KPI cards. |
| **Rejecting** | (1) metric-card dashboard → a single action-led posture strip; (2) giant settings form → task-scoped sections with local review; (3) flashy destructive toggles → typed values, explicit consequence and server-enforced confirmation. |

Use the existing admin tokens: parchment surfaces, maroon authority, brass
focus/attention, and restrained green success. Keep the existing low-radius,
border-led civic aesthetic; reduce repeated heavy card outlines instead of
introducing a new component library or a consumer-SaaS visual language.

## Target information architecture

```text
Settings & Configuration
│
├─ Operating posture (read-only, concise)
│  ├─ Environment / deployment / active data posture
│  ├─ Settings editing: locked or available
│  └─ Next safe action / blocking reason
│
├─ Local section rail (deep-linkable)
│  ├─ Public experience
│  ├─ Analytics tenant
│  ├─ Runtime controls
│  ├─ Recovery status → Operations
│  └─ Configuration inventory
│
├─ Task surface (one focal section at a time)
│  ├─ Current effective state + source
│  ├─ Editable values, local validation, consequence badges
│  ├─ Change review only when values differ
│  └─ Saved/failed receipt anchored to this section
│
└─ Evidence drawer
   ├─ Collapsed, filterable safe inventory
   ├─ Last non-secret change receipts
   └─ Deployment-only explanation / runbook link
```

On desktop, use a narrow sticky rail (about 240–280 px) beside the task
surface; on mobile it becomes an in-flow `<nav>` with anchors. Each section
uses an `id` and scroll margin. `?section=runtime` (or a hash fallback) should
preserve the focused section through a POST/redirect/GET result.

## State model and operator journeys

| Journey | Desired experience | Server contract |
| --- | --- | --- |
| Inspect posture | One sentence says whether editing is locked, which environment is active, and what requires attention. Details remain available without competing with actions. | Existing `ENV_IDENTITY`, registry, and health facts stay read-only and redacted. |
| Change public presentation | Select a view, preview in a new tab, save, receive a local receipt showing “effective immediately.” | Preserve `set_primary_search_view`; add a scoped success/error return target. |
| Change low-risk runtime limit | Only changed rows appear in review; submit remains enabled until the request starts; receipt states new effective value and source. | Keep current allowlist and bounds; use an optimistic revision token. |
| Change high-impact public search switch | The changed row becomes an attention item with a plain-language consequence; confirmation appears only for that diff. | Keep server-side high-impact enforcement regardless of JavaScript. |
| Editing locked | Show why a specific field is locked and the exact deployment-level next step; controls remain readable rather than looking broken. | An explicitly defined setting ENV key owns and locks that field; there is no global edit switch. |
| Configure analytics | Display exact host, tenant presence, mode, consent behavior, and safe status. A production Website ID is never inferred from stage. | Keep host-scoped `SiteSetting` keys; no secret entry or local collection. |
| Inspect recovery | Show verified/not configured/unreachable with a link to the Operations workflow; no recovery mutation controls appear here. | Health remains redacted and read-only. |
| Fix invalid/stale submission | Return to the owning section, focus the first invalid field, preserve submitted non-secret values, and explain the next step. | Field-keyed error payload; server validation and CSRF remain authoritative. |

## Implementation slices

### Slice A — correctness and safety first

1. Extend `RuntimeSettingDefinition` with presentation-safe metadata:
   `risk`, `activation` (`immediate`/`restart`), `help_anchor`, and an
   optional concise consequence. Do not duplicate environment variables.
2. Submit a revision token based on the current non-secret `SiteSetting`
   metadata. Reject a stale update with a named conflict and re-read the
   effective values. Do not attempt a blind retry.
3. Replace anonymous flash-only errors with a scoped, field-keyed result
   contract. Preserve the section in the redirect and focus the first error.
4. Render `inputmode="numeric"` only for number controls; add `step`,
   `autocomplete="off"`, `aria-describedby`, and inline bounds/error text.
5. Keep confirmation server-side. Use minimal progressive enhancement to show
   a diff and high-impact confirmation only if a high-impact value actually
   changes; no-JavaScript flow remains safe and understandable.

### Slice B — information architecture and civic visual refinement

1. Add the operating-posture strip and section rail without changing any
   setting semantics.
2. Demote environment facts into a compact “read-only deployment record”
   disclosure. Promote the current task’s heading, primary action, and impact.
3. Make Public Experience, Analytics, Runtime Controls, and Recovery clearly
   separate task surfaces. Recovery links to Operations rather than imitating
   a configurable setting.
4. Use existing admin CSS tokens and semantic component classes; add
   `:focus-visible`, reduced-motion-safe transitions, `text-wrap: balance` on
   headings, tabular numbers for values, and responsive hit areas.

### Slice C — evidence and inventory usability

1. Make inventory groups collapsed by default except the focused group; add a
   client-side, content-free filter that does not expose raw secrets.
2. Add safe source/lifecycle/risk labels and a “why not editable?” explanation.
3. Add a redacted last-changes list (actor, timestamp, key label, effect,
   source and result; never secret or raw credential values).
4. Add optional, consent-governed analytics events only for settings UI
   workflow outcomes; never send setting names/values, actor IDs, IPs, or error
   text. This is lower priority than the operator audit receipt.

## Impact analysis

| Area | Change | Guardrail |
| --- | --- | --- |
| Runtime behavior | No new live setting is introduced by the visual work. | Registry remains the only editable allowlist. |
| Security | No secret becomes editable or observable. | Redaction is tested in HTML, messages, audit output, and browser payloads. |
| Data operations | Settings gains a link and truthful posture only. | No backup, restore, activation, profile, or credential mutation is added. |
| Accessibility | Local errors, semantic fieldsets, visible focus, navigation anchors, and mobile order improve. | Keyboard and screen-reader tests are required. |
| Localization | All new operator copy is English/Marathi parity work. | Translation tests/copy review block merge. |
| Performance | Collapsed inventory reduces initial scan burden; no client framework is added. | Verify no extra network request or large DOM work on first paint. |
| Rollback | Each slice is independently reversible UI/server behavior. | No data migration is needed for Slice B; receipt schema, if added, gets a migration and backward-compatible reader. |

## Verification matrix

| Gate | Required proof |
| --- | --- |
| Unit | Resolver precedence (`False`, `0`, empty), bounds, stale revision, high-impact confirmation, redaction, audit receipt, analytics host isolation. |
| Request/HTML | Locked editing, normal save, invalid field, stale form, high-impact diff, analytics tenant state, recovery link, section-preserving redirect. |
| Accessibility | Heading order, skip/section navigation, keyboard focus, input labels, inline errors, `aria-live`, 44px touch targets, Marathi text. |
| Browser | Desktop + mobile screenshots for default, locked, changed, error, success, and long-inventory states; no overlap or horizontal scroll. |
| Regression | `python manage.py check`, migrations check, focused tests, existing dashboard/settings/theme suites, static JS syntax check, source-backed Playwright gate. |
| Security | No setting values in analytics payloads; no secret in HTML/log/message; administrators are server-limited to harmless controls; analytics changes remain superadmin-only; local/unapproved hosts remain denied. |

## Stop conditions and non-goals

Stop and seek explicit review if a proposed setting is only read at startup,
requires a secret, would alter data recovery or production routing, or cannot
explain a safe default action. Do not introduce a SPA, generic feature-flag
SDK, new environment-variable matrix, editable DataOps profiles, or direct
object-store controls. Do not merge a broad visual rewrite with unrelated
analytics or search changes.

## Done criteria

- A superadmin can identify the current environment, next safe action, and
  editable scope in one screen without scanning an inventory.
- Every change displays effective source, activation timing, consequence, and
  a scoped result/receipt.
- Invalid/stale submissions are actionable at the affected field and do not
  persist an unsafe value.
- The inventory is useful without overwhelming the page or exposing secrets.
- English and Marathi operator copy, screen-reader behavior, desktop/mobile
  layouts, and server-side safety gates are verified together.
