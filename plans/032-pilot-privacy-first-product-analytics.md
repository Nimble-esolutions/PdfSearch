# Plan 032: Pilot privacy-first product analytics on stage

> **Executor instructions**: Read
> `docs/PRODUCT_ANALYTICS_RECOMMENDATION.md` first. This plan is blocked until a
> privacy owner approves the recommended independent Umami stage pilot or
> explicitly selects PostHog EU Cloud/no analytics. Do not infer approval from
> the recommendation, an installed package, or a running analytics service.
>
> **Drift check (run first)**:
> `git diff --stat 796bdd5..HEAD -- flowdocs/core/templates flowdocs/core/static/main/js flowdocs/core/views.py flowdocs/core/metrics.py requirements-web.lock docs/PRODUCT_ANALYTICS_RECOMMENDATION.md`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: HIGH — analytics can disclose public questions, evidence, or admin activity
- **Depends on**: privacy-owner decision and notice/consent review
- **Category**: product analytics, privacy, tests
- **Planned at**: commit `796bdd5`, 2026-08-07

## Scope

**In scope**: an independent Umami proof stack, stage-only manual events from
the documented allowlist, one vendor-neutral browser adapter, strict property
schemas, cookieless/no-identity posture, payload-leakage tests, retention and
capacity controls, notice/consent behavior, and a two-week evidence review.

**Out of scope**: session replay, autocapture, raw URLs, user identity, server
calls on the search path, LLM observability, safety-critical feature flags,
production enablement, self-hosted PostHog, Parseable/DataLens deployment, and
using Tianji/Aptabase/Plausible/OpenPanel/Rybbit/Matomo without a recorded gap
that Umami cannot satisfy.

## Steps

1. Record the privacy decision: independent Umami pilot, PostHog EU Cloud, or
   no analytics. Record custody/region, consent posture, retention, owner,
   backup owner, deletion authority, and emergency disable authority without
   storing credentials.
2. If Umami is approved, deploy it as a separate project with a pinned immutable
   image, dedicated PostgreSQL volume, authenticated dashboard, closed database
   port, TLS, healthcheck, resource limits, tested backup/restore, and a tested
   retention/deletion runbook. Create separate stage and production website
   records, but leave production collection disabled.
3. Implement a disabled-by-default adapter with a compile-time event/property
   allowlist and a final `before_send` rejection gate. Analytics must load
   asynchronously and fail open. Application code supplies a transport-neutral
   event; only the adapter knows Umami/PostHog APIs.
4. Instrument only completed user outcomes in Classic, Workbench, intake, and
   maintenance. Reuse event names across themes with a bounded `view` property;
   do not share theme controller code beyond the adapter contract.
5. Add unit and browser leakage tests with sentinel questions, answers,
   filenames, source URLs, users, and recovery identifiers. Block every unknown
   property and prove unavailable analytics never changes UI behavior/timing.
6. Run a two-week stage pilot. Review event usefulness, funnel completeness,
   database growth, browser overhead, notice compliance, and sampled payloads.
   Delete unused events before requesting a separate production approval.
7. Write a capability-gap log. Escalate from Umami only when a named product
   decision cannot be answered with the approved schema. Re-score Aptabase for
   anonymous event timelines, OpenPanel/Rybbit for richer product analysis, or
   Tianji for intentional monitoring consolidation; do not add Parseable or
   DataLens to solve a product-event gap.

PdfSearch connects only through HTTPS event ingestion and never through a
shared network, database, volume, synchronous backend call, or readiness gate.

## Done criteria

- [ ] Privacy decision and accountable owners are recorded.
- [ ] No forbidden content or identity is captured in automated leakage tests.
- [ ] Session replay, autocapture, identification, raw URLs, and exceptions are disabled.
- [ ] Search and operations work unchanged when analytics is blocked.
- [ ] Umami database restore and retention deletion are proven independently.
- [ ] Measured browser overhead stays within the approved performance budget.
- [ ] Production remains disabled pending a separate evidence review.

## STOP conditions

- Required retention is shorter than the provider can enforce.
- The implementation needs question, answer, source, document, auth, or stable-user data.
- Analytics adds a server-side search dependency or affects readiness.
- The selected product requires ClickHouse, Redis, replay, or identity before a
  documented Umami capability gap and owner approval exist.
