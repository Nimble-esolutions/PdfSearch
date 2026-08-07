# Plan 032: Pilot privacy-first product analytics on stage

> **Executor instructions**: Read
> `docs/PRODUCT_ANALYTICS_RECOMMENDATION.md` first. This plan is blocked until a
> privacy owner chooses the recommended PostHog EU Cloud stage pilot, the
> independent Umami self-hosted transport, or no analytics. Do not infer
> approval from the presence of a package.
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

**In scope**: stage-only manual events from the documented allowlist, one
vendor-neutral browser adapter, strict property schemas, cookieless/no-identity
posture, payload-leakage tests, quota controls, notice/consent behavior, and a
two-week evidence review.

**Out of scope**: session replay, autocapture, raw URLs, user identity, server
calls on the search path, LLM observability, safety-critical feature flags,
production enablement, and self-hosted PostHog.

## Steps

1. Record the privacy decision: EU Cloud pilot, independent Umami stack, or no
   analytics. Record region/custody, consent posture, retention, owner, backup
   owner, and disable authority without storing credentials.
2. Implement a disabled-by-default adapter with a compile-time event/property
   allowlist and a final `before_send` rejection gate. Analytics must load
   asynchronously and fail open.
3. Instrument only completed user outcomes in Classic, Workbench, intake, and
   maintenance. Reuse event names across themes with a bounded `view` property;
   do not share theme controller code beyond the adapter contract.
4. Add unit and browser leakage tests with sentinel questions, answers,
   filenames, source URLs, users, and recovery identifiers. Block every unknown
   property and prove unavailable analytics never changes UI behavior/timing.
5. Run a two-week stage pilot. Review funnels, quota, event usefulness, notice
   compliance, and payload samples. Delete unused events before requesting a
   separate production approval.

If Umami is selected, deploy it in a separate project with a pinned application
image, dedicated PostgreSQL volume, authenticated dashboard, separate stage and
production website records, and tested backup/restore/deletion procedures.
PdfSearch connects only through HTTPS event ingestion and never through a
shared network, database, or volume.

## Done criteria

- [ ] Privacy decision and accountable owners are recorded.
- [ ] No forbidden content or identity is captured in automated leakage tests.
- [ ] Session replay, autocapture, identification, raw URLs, and exceptions are disabled.
- [ ] Search and operations work unchanged when analytics is blocked.
- [ ] Production remains disabled pending a separate evidence review.

## STOP conditions

- Required retention is shorter than the provider can enforce.
- The implementation needs question, answer, source, document, auth, or stable-user data.
- Analytics adds a server-side search dependency or affects readiness.
