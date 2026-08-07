Status: Proposed
Audience: Product owner, privacy owner, developer, operator
Owner: FlowDocs maintainers
Last verified: 2026-08-07
Canonical source: docs/PRODUCT_ANALYTICS_RECOMMENDATION.md
Supersedes: None

# Privacy-first product analytics recommendation

## Decision summary

Product analytics must remain separate from operational search telemetry. Keep
search latency, provider timing, corpus state, cache behavior, and readiness in
content-free structured logs/OpenTelemetry metrics. If product analytics is
approved, pilot **PostHog EU Cloud Free on stage only** for two weeks using
manual allowlisted events. Do not self-host PostHog on the application server.

The pilot is optional and must not block search, document processing, backup,
restore, activation, or readiness. The no-external-cloud fallback is Umami with
an explicit retention/deletion policy; it adds a PostgreSQL service and should
not be deployed merely to avoid making the privacy decision.

## Recommended self-hosted topology

If analytics data must remain under operator custody, deploy Umami v3 as an
independent Dokploy/Compose project with a pinned Umami image, a dedicated
PostgreSQL database/volume, and its own backup/restore and retention policy.
Use separate Umami website records for stage and production. Protect the
dashboard with its own authentication and expose only the documented tracker
and event-ingestion surface required by browsers.

```text
PdfSearch browser
  -> HTTPS manual allowlisted event
     -> independent Umami application
        -> dedicated PostgreSQL volume
```

PdfSearch must not join the analytics Docker network, mount its volumes, query
its database, or make analytics a backend/readiness dependency. The shared
vendor-neutral adapter changes transport only (`disabled`, `umami`, or approved
`posthog_eu`); event names and property schemas remain identical. Analytics
failure stays invisible and fail-open.

OpenPanel is the nearest self-hosted PostHog-style alternative, but its basic
stack requires PostgreSQL, Redis, ClickHouse, API/dashboard services, and
workers. Adopt it only if measured product needs require cohorts, experiments,
or richer funnels that Umami cannot supply. See the official
[OpenPanel self-hosting requirements](https://openpanel.dev/docs/self-hosting/environment-variables).

## Options

| Option | Fit | Operational/privacy cost | Recommendation |
| --- | --- | --- | --- |
| PostHog EU Cloud Free | Funnels and bounded product events across Classic, Workbench, intake, and maintenance | Event data leaves the server; free plan currently has one-year retention and quota limits | Recommended stage pilot after privacy approval |
| PostHog self-hosted | Same product surface under operator custody | PostHog documents self-hosting as unsupported; the stack is heavy relative to this application | Reject |
| Umami self-hosted | Lightweight aggregate/custom-event analytics without cookies or cross-site tracking | Adds PostgreSQL, upgrades, backup, restore, and explicit retention work | Best no-cloud fallback |
| Plausible | Strong aggregate public-site analytics | Cloud is not permanently free and admin workflow funnels are not its main strength | Secondary public-site option |
| OpenTelemetry plus existing metrics | Vendor-neutral operational latency and failure evidence | Needs a metrics/traces backend, but no product identity model | Adopt for search operations, not product analytics |

Primary references: [PostHog pricing](https://posthog.com/pricing),
[privacy](https://posthog.com/docs/privacy),
[JavaScript configuration](https://posthog.com/docs/libraries/js/config),
[self-hosting](https://posthog.com/docs/self-host),
[session-replay privacy](https://posthog.com/docs/session-replay/privacy),
[Umami documentation](https://docs.umami.is/docs),
[Plausible privacy model](https://plausible.io/privacy-focused-web-analytics),
and [OpenTelemetry metrics](https://opentelemetry.io/docs/concepts/signals/metrics/).

## Non-negotiable privacy contract

Use one internal analytics adapter that rejects unknown events and properties;
application code must not call a vendor SDK directly. The adapter must fail
open and load after the usable UI. The default client posture is:

```text
autocapture=false
capture_pageview=false
capture_pageleave=false
capture_performance=false
capture_exceptions=false
disable_session_recording=true
person_profiles=identified_only
cookieless_mode=always
mask_all_text=true
mask_all_element_attributes=true
```

Never call identify/alias/person-merge APIs. Never capture raw questions,
answers, prompt/context text, PDF text, titles, filenames, source URLs, route
parameters, query strings, document IDs, user/account/session IDs, usernames,
emails, roles, IP-derived identity, dataset/profile/bucket/generation names,
manifest digests, credentials, auth data, or recovery identifiers. Session
replay and PostHog LLM observability remain disabled, not merely masked.

The browser project token may be public; personal API keys never enter browser
code. Cookieless operation still requires an updated privacy notice and the
privacy owner's jurisdiction-specific consent decision.

## Allowed shared properties

Every event may contain only bounded enums/buckets:

| Property | Allowed values |
| --- | --- |
| `schema_version` | integer contract version |
| `deployment_tier` | `stage`, `production` |
| `surface` | `classic`, `workbench`, `admin`, `document_intake`, `data_maintenance` |
| `ui_language` | `en`, `mr` |
| `viewport_class` | `mobile`, `tablet`, `desktop` |
| `release_version` | reviewed non-secret application release identifier |

## Event allowlist

| Journey | Events | Additional bounded properties |
| --- | --- | --- |
| Public search | `search_viewed`, `search_submitted`, `search_completed`, `search_retry_clicked` | `view`, `question_language`, word/latency/reference-count bucket, `outcome`, `cache_path`, `failure_family` |
| Evidence use | `search_sources_opened`, `search_source_selected`, `search_share_started`, `search_feedback_opened`, `search_help_opened` | source-rank/count bucket, `channel`, `answer_kind`; never source identity or content |
| Theme continuity | `search_view_override_used` | bounded `from_view`, `to_view` |
| Intake | `upload_batch_started`, `upload_batch_completed`, `document_processing_completed`, `indexing_completed` | file/size/page/document-count/duration buckets, `native_text` or `ocr_fallback`, outcome/failure family |
| Maintenance | `maintenance_preview_created`, `maintenance_action_submitted`, `maintenance_action_completed`, `maintenance_action_blocked`, `admin_guidance_opened` | bounded operation/scope/outcome/blocker/count/duration enums |
| Data protection | `data_protection_step_completed` | bounded step/outcome/duration only; no storage or recovery-point identity |

Search outcomes are strict enums: `evidence`, `no_evidence`,
`provider_timeout`, `provider_unavailable`, `rate_limited`, `invalid_request`,
or `internal_error`. Use duration buckets (`<1s`, `1–3s`, `3–10s`, `10–30s`,
`>=30s`) rather than high-cardinality raw values.

Capture all failures and high-value workflow outcomes. After baseline, sample
successful performance events at 10–20%. Alert at 70% of the monthly quota and
disable low-value sampled events at 90%; never drop authored failure events.

## Release gate

Before stage capture:

1. Privacy owner approves cloud region, notice/consent posture, and one-year
   provider retention or selects the independent Umami no-cloud stack with an
   explicit database backup and deletion schedule.
2. Unit tests reject every forbidden property and unknown event.
3. Browser tests inspect emitted payloads for both themes and admin journeys;
   sentinel questions, answers, filenames, URLs, and identities must be absent.
4. Analytics blocked/unreachable tests prove no UI or backend failure, delay,
   retry, or readiness degradation.
5. Stage uses one project with a bounded `deployment_tier`; production capture
   remains disabled until the two-week evidence review.

Do not use feature flags for authentication, authorization, source selection,
indexing, backup, restore, activation, or other safety-critical behavior.
