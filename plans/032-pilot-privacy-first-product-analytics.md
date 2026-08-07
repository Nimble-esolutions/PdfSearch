# Plan 032: Pilot privacy-first product analytics on stage

> **Executor instructions**: Read
> `docs/PRODUCT_ANALYTICS_RECOMMENDATION.md` first. Self-hosted Umami is selected
> and the application integration is implemented in PR #192. The operator will
> deploy the independent service manually. Do not enable stage collection until
> retention, backup/restore, deletion authority, TLS, and dashboard access are
> verified; production remains a separate decision.
>
> **Drift check (run first)**:
> `git diff --stat 796bdd5..HEAD -- flowdocs/core/templates flowdocs/core/static/main/js flowdocs/core/views.py flowdocs/core/metrics.py requirements-web.lock docs/PRODUCT_ANALYTICS_RECOMMENDATION.md`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: HIGH — analytics can disclose public questions, evidence, or admin activity
- **Depends on**: operator-managed Umami deployment and retention/restore proof
- **Category**: product analytics, privacy, tests
- **Planned at**: commit `796bdd5`, 2026-08-07

## Scope

**In scope**: an independent Umami proof stack, stage-only manual events from
the documented allowlist, one Umami-specific browser adapter with a
transport-neutral event vocabulary, strict property schemas, cookieless/no-app-
identity posture, payload-leakage tests, retention and
capacity controls, notice/consent behavior, and a two-week evidence review.

**Out of scope**: session replay, autocapture, raw URLs, application user
identity or explicit distinct IDs, server
calls on the search path, LLM observability, safety-critical feature flags,
production enablement, self-hosted PostHog, Parseable/DataLens deployment, and
using Tianji/Aptabase/Plausible/OpenPanel/Rybbit/Matomo without a recorded gap
that Umami cannot satisfy.

## Steps

1. **Done in application scope:** select independent self-hosted Umami, publish
   the cookieless notice, and keep the emergency disable control in the existing
   superadmin Settings page. The independent stack still needs named backup,
   deletion, and incident owners without recording credentials.
2. If Umami is approved, deploy it as a separate project with a pinned immutable
   image, dedicated PostgreSQL volume, authenticated dashboard, closed database
   port, TLS, healthcheck, resource limits, tested backup/restore, and a tested
   retention/deletion runbook. Create separate stage and production website
   records, but leave production collection disabled.
3. **Done in PR #192:** implement a disabled-by-default adapter with a compile-time event/property
   allowlist and a final `beforeSend` rejection gate. Analytics must load
   asynchronously and fail open. Application code supplies a transport-neutral
   event; only the adapter knows Umami/PostHog APIs.
4. **Public search done in PR #192:** instrument submitted/completed/retry,
   evidence, feedback, help, share, and theme-view outcomes in Classic and
   Workbench. Intake and maintenance remain separate follow-up slices with the
   same adapter; no theme controller code is shared.
5. **Public search done in PR #192:** add unit and browser leakage tests with sentinel questions, answers,
   filenames, source URLs, users, and recovery identifiers. Block every unknown
   property and prove adapter failure never changes UI event handling. The
   deployed, enabled Classic/Workbench outage rehearsal remains an operator gate.
6. Run a two-week stage pilot. Review event usefulness, funnel completeness,
   database growth, browser overhead, notice compliance, and sampled payloads.
   Delete unused events before requesting a separate production approval.
7. Write a capability-gap log. Escalate from Umami only when a named product
   decision cannot be answered with the approved schema. Re-score Aptabase for
   anonymous event timelines, OpenPanel/Rybbit for richer product analysis, or
   Tianji for intentional monitoring consolidation; do not add Parseable or
   DataLens to solve a product-event gap.

## Manual same-server deployment handoff

The operator-managed stack is deliberately independent of PdfSearch:

1. Deploy a pinned Umami image and dedicated PostgreSQL service/volume; record
   the image digest, tracker version, and served `script.js` SHA-256. Review the
   tracker before first enablement and repeat the hash/review after each upgrade.
   Do not join the PdfSearch application network or reuse its database or volumes.
2. Route only the Umami web service through TLS at
   `analytics.ai-sahakar.net`; keep PostgreSQL private. Before public routing,
   replace Umami's default `admin` / `umami` credentials and protect the
   dashboard with separate credentials.
3. In Umami, confirm that the stage website record is for
   `2026.ai-sahakar.net` and that its public website ID matches the ID already
   compiled into the stage-only adapter. Do not reuse it for production.
4. Verify the script endpoint, event ingestion, healthcheck, and PostgreSQL
   backup/restore. Create a daily, version-reviewed purge job that removes event,
   session, and related proxy-log records older than 30 days; assign its owner,
   alert when it has not succeeded for 24 hours, and retain a content-free
   max-record-age report as evidence. Self-hosted Umami otherwise retains data
   indefinitely.
5. Set explicit CPU, memory, PID, log-size, and storage limits for Umami and
   PostgreSQL. Confirm PdfSearch retains CPU/memory/disk/inode headroom and make
   the analytics stack the first service stopped on host pressure.
6. From PdfSearch superadmin **Settings**, enable **Stage Umami collection**.
   No application restart or environment change is required.
7. Inspect browser requests and the Umami dashboard: only the documented event
   names and bounded properties may appear; raw URLs, questions, answers,
   sources, titles, referrers, application identity, and explicit distinct IDs
   must remain absent. Confirm and approve Umami's documented pseudonymous
   session hash and browser/device/OS/country metadata separately.
8. Block or stop Umami and repeat both search themes. Search, retries, sources,
   legal pages, `/livez`, and `/readyz` must remain unchanged; then restore the
   independent service.
9. For rollback, disable collection in PdfSearch first and verify the tracker is
   absent from both themes. Preserve the analytics database, restore the last
   tested Umami image/database pair, and roll DNS/TLS back independently. If the
   analytics stack affects host health, stop it immediately; PdfSearch has
   priority over analytics recovery.
10. Treat the analytics origin as executable-code trust, not only an event
    destination. Restrict the future application CSP to the exact
    `https://analytics.ai-sahakar.net` script/connect origin. Add SRI only when
    the tracker URL is immutable and its reviewed hash can be released with the
    application; never pin a hash to an operationally mutable `script.js` URL.

PdfSearch connects only through HTTPS event ingestion and never through a
shared network, database, volume, synchronous backend call, or readiness gate.

## Done criteria

- [ ] Privacy decision and accountable owners are recorded.
- [x] No forbidden content or identity is captured in public-search adapter leakage tests.
- [x] The application adapter rejects session replay, autocapture, identity,
  raw URLs, and unknown event properties; confirm the independently deployed
  Umami service preserves this boundary before enabling collection.
- [x] Adapter-level UI event handling remains functional without the vendor object.
- [ ] Enabled Classic and Workbench searches pass the deployed service outage rehearsal.
- [ ] Umami database restore and retention deletion are proven independently.
- [ ] Measured browser overhead stays within the approved performance budget.
- [ ] Pinned image, tracker hash, upgrade owner, exact CSP origins, and remote-
  script incident response are recorded.
- [ ] Production remains disabled pending a separate evidence review.

## STOP conditions

- Required retention is shorter than the provider can enforce.
- The implementation needs question, answer, source, document, auth, or stable-user data.
- Analytics adds a server-side search dependency or affects readiness.
- The selected product requires ClickHouse, Redis, replay, or identity before a
  documented Umami capability gap and owner approval exist.
