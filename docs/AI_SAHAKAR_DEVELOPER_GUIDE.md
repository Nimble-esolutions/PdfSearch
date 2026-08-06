# AI Sahakar Developer Guide

**Status:** Active
**Audience:** Developers, reviewers, and coding agents
**Last verified:** 2026-08-06
**Canonical design:** [`design/AI_SAHAKAR_UI_CONTRACT.md`](design/AI_SAHAKAR_UI_CONTRACT.md)

## Start with the contract

AI Sahakar has isolated Classic and Knowledge Workbench public frontends over
one secured search backend. Read the UI contract before touching either public
view or the admin interface. Do not solve presentation drift by sharing CSS,
JavaScript, or template fragments *between* the two themes. A theme-specific
header partial may be reused by that same theme's search and public-information
shell so identity and navigation cannot drift.

## File map

| Area | Files |
| --- | --- |
| Theme selector | `flowdocs/core/search_ui.py`, allowlisted `SiteSetting` and request-only override |
| Classic frontend | `search_classic.html`, `search-classic.css`, `search-classic.js` |
| Workbench frontend | `search.html`, `civic-workbench.css`, `search.css`, `search.js` |
| Theme-specific public headers | `components/public/classic_header.html`, `components/public/workbench_header.html` |
| Public information shell | `legal_base.html`, `terms.html`, `privacy.html`, `disclaimer.html`, `data_policy.html`, `cookie_policy.html` |
| Public information assets | `public-legal.css`, `public-legal.js` |
| Shared public backend | `flowdocs/core/views.py`, existing search URL and JSON/PDF contract |
| Admin shell | `flowdocs/core/templates/base.html`, `dashboard.html` |
| Admin styling | `flowdocs/core/static/main/css/style.css` |
| Locale catalogs | `flowdocs/locale/en/LC_MESSAGES/django.po`, `flowdocs/locale/mr/LC_MESSAGES/django.po` |
| Browser coverage | `browser_tests/classic-search.spec.ts`, `browser_tests/civic-workbench.spec.ts`, `browser_tests/search-motion.spec.ts`, `browser_tests/public-legal.spec.ts` |

Use existing Django partials, translation tags, `json_script`, and CSS tokens.
Do not copy production data or secrets into fixtures.

### Public theme boundary

The persisted key is `PUBLIC_SEARCH_PRIMARY_VIEW` with only `classic` or
`workbench` accepted. It is managed through the superadmin settings form, not
an environment variable. Missing/invalid values resolve to Classic. A valid
`?view=` query overrides one response and is never stored.

Shared behavior stops at server contracts: search request/response fields,
protected PDF URLs, CSRF, authentication, locale session, and approved public
links. Each frontend owns its markup, state rendering, accessibility behavior,
responsive layout, CSS selectors, and JavaScript lifecycle. A change to one
theme must not require loading the other's static files.

Public information routes use `_public_view_context()` and
`_render_legal_page()` in `core.views`. They may reuse the matching theme's
header partial, but use only `legal_base.html`, `public-legal.css`, and
`public-legal.js` for document layout and behavior. Preserve only a valid
`?view=classic|workbench` across return, policy-navigation, and locale URLs;
discard unknown values and keep canonical URLs query-free.

### Search intent and answer language

Both themes consume one backward-compatible JSON contract with `answer` and
`references` plus typed `kind` and backend-resolved `language`. Never classify
intent or infer answer language in frontend code. Exact whole-query small-talk
matching prevents domain words such as `updated` or `membership` from
colliding with short greeting/date tokens.

Derive answer language from the question. Use the page/client locale only when
the query has no language-bearing letters. Validate the dominant script before
caching a provider answer, allow one bounded repair attempt, and return the
explicit `answer_language_mismatch` error if repair still fails. Both themes
must apply the returned `language` to the rendered answer's `lang` attribute.

## Operator presentation API

`flowdocs/core/operator_presentation.py` is the sole presentation boundary for
Dashboard and Workbench machine evidence. Keep `reason_code`,
`safe_error_code`, state, operation, and job fields stable. Add the adjacent
`presentation` or `*_label` fields by calling `decorate_operator_state`; JSON
responses keep the code and add a sibling presentation object.

Templates render guidance with:

```django
{% include "components/operator_evidence.html" with
  presentation=item.presentation technical_code=item.reason_code only %}
```

The component presents title, explanation, consequence, and action first. It
places the exact code in collapsed, LTR Technical details. Unknown codes use
neutral review guidance and must never be formatted by replacing underscores.
Register new UI reasons with authored English and Marathi copy before use.

Marathi terminology follows a reviewed glossary:

| English concept | Required Marathi rendering |
| --- | --- |
| Dashboard | नियंत्रण फलक |
| Workbench | कार्यपटल |
| operator | परिचालक |
| Vault | व्हॉल्ट |
| runtime | कार्यरत प्रणाली |
| profile | रूपरेषा |
| rollback | मागील आवृत्ती पुनर्स्थापना |
| writer lease | रायटर लीज |
| credentials | प्रवेश-प्रमाण / प्रमाणीकरण माहिती |
| embedding | एम्बेडिंग |
| manifest | मॅनिफेस्ट |
| checkpoint | चेकपॉइंट |
| garbage collection / GC | जीसी (सुरक्षित साफसफाई) |
| retention hold | जतन स्थगिती |
| generation | निर्मिती संच |
| reindex | पुनःअनुक्रमण |

Use an established literal Marathi term wherever it stays precise, including
ordinary file/media language (`संचिका`), runtime posture
(`कार्यरत प्रणाली`), connection profiles (`जोडणी रूपरेषा`), operators
(`परिचालक`), and rollback actions (`मागील आवृत्ती पुनर्स्थापित करा`). Retain
a reviewed Marathi-script product term only when a literal rendering would
change a specialist identity, such as Vault, manifest, embedding, or database.
Latin-script English is reserved for exact
technical evidence such as codes, API fields, UUIDs, hashes, filenames, and
paths. Do not translate those identifiers.

For obvious operator language, literal Marathi is mandatory rather than a
Latin-script fallback or unnecessary phonetic spelling:

| English operator term | Required Marathi copy |
| --- | --- |
| unavailable | उपलब्ध नाही |
| warning | इशारा |
| failed | अयशस्वी |
| active | सक्रिय |
| previous | मागील |
| reason | कारण |
| action | कृती |
| result | परिणाम |
| observed | निरीक्षण केले |
| search readiness | शोध तयारी |
| technical details | तांत्रिक तपशील |

Translate the complete sentence and review its grammar, including older catalog
entries touched by the same concept. Do not perform blind word replacement,
and do not translate immutable codes or identifiers.

## Operations information architecture

Keep the two operator journeys distinct even though they share the existing
Django route and backend read model:

- **Search maintenance** is the ordinary, local document-care journey:
  validate, repair stored indexes, bounded reindex, and monitor maintenance.
- **Data protection** is the supported DataOps v3 custody journey: recovery
  points, backup, foreign-dataset import/rebind, isolated restore candidates,
  activation evidence, and recovery history. The retained `vaultops` package is
  an internal compatibility/activation bridge, not a second workbench.

Dashboard document-readiness actions must deep-link to `?section=maintenance`.
Do not force an administrator through remote authority or profile setup before
local maintenance. On that section, advanced generation/runtime evidence is
collapsed by default. Other sections retain complete operator evidence and all
existing authorization and confirmation gates.

A reachable profile with no published generation is a first-run state, not a
failed connection. Explain that the read-only probe checks storage access and
that inventory verification applies after publication. Never invent a
generation or weaken inventory verification to manufacture a healthy state.

## Safe UI change workflow

1. Inspect the current implementation and the relevant browser tests.
2. Identify the component contract, responsive range, states, locale strings,
   and backend data fields involved.
3. Make a small patch. Keep search routes, CSRF, auth, PDF access, feedback,
   WhatsApp, and response fields unchanged. Verify the untouched public theme
   does not gain the changed theme's template, stylesheet, or script.
4. Add or update English/Marathi copy in catalogs; never concatenate translated
   fragments in JavaScript or mutate user-entered questions. Remove fuzzy flags
   only after reviewing the complete Marathi sentence in context; a successful
   locale compilation does not establish translation parity. Run the
   registry-wide operator-language validator; it rejects missing, fuzzy, empty,
   and unchanged-English Marathi registry entries.
5. Test static and dynamic states, including no-result and failure paths.
6. Update the contract or this guide if the rule itself changed.

Never remove an old template, route, asset, or CSS block based only on a
visual assumption. Prove references and record cleanup separately; see
[`DEV_CLEANUP_SCOPE.md`](DEV_CLEANUP_SCOPE.md).

## Local DataOps and compatibility-maintenance bootstrap

`docker-compose.dev.yml` starts an isolated RustFS service and creates the
`pdfsearch-dev` bucket. DataOps v3 uses one owned recovery connection for local
backup and same-dataset restore. Before a stored control-database connection
exists, the `ARTIFACT_VAULT_*` values below are accepted only as a temporary,
secret-free bootstrap description; the worker resolves credentials at execution
time.

```text
ARTIFACT_VAULT_ENABLED=1
ARTIFACT_VAULT_ENDPOINT=http://rustfs:9000
ARTIFACT_VAULT_BUCKET=pdfsearch-dev
ARTIFACT_VAULT_REGION=us-east-1
ARTIFACT_VAULT_CREDENTIAL_REF=env://ARTIFACT_VAULT
VAULT_ALLOWED_S3_ENDPOINTS=http://rustfs:9000
VAULT_ALLOW_HTTP_S3_ENDPOINTS=1
VAULT_BLOCK_PRIVATE_S3_ENDPOINTS=0
```

The development access and secret keys are disposable RustFS Compose defaults and
must never be reused outside local development. Production secrets remain
server-managed and must be inspected only as set/unset posture.

Opening Data protection materializes or reads the owned v3 connection. The
read-only connection check verifies endpoint, bucket, credentials, ownership,
and conditional-write capability before publication. Migration-created legacy
VaultOps profiles are compatibility evidence and must not be presented as the
normal DataOps v3 connection path.

Local development enables validation, stored-index repair, and sandboxed
reindexing independently of remote Vault authority. The development Compose
stack also enables `VAULT_MUTATION_TRACKING_ENABLED=1`: repair and reindex
create mutable candidates and must remain disabled when the worker cannot prove
that source documents stayed consistent while preparing them. Read-only
validation does not require mutation tracking. Production Compose defaults
remain disabled until the deployment supplies and verifies that evidence.

An unexpected candidate-preparation error fails the affected durable job with
a bounded reason code and audit event; it must not terminate the maintenance
worker or leave the job indefinitely in `running`. Keep exception text out of
operator-visible summaries and use the collapsed technical evidence for the
stable code.

The development web container's Compose healthcheck uses `/livez`; `/readyz`
also requires a fresh maintenance-worker heartbeat. This prevents a startup
cycle in which the worker waits for web readiness while web readiness waits for
the worker.

### Activation verifier failure evidence

The PID-1 supervisor continues to discard management-command stdout and stderr.
`verify_activation_runtime` reports failures through a transient, mode-`0600`
JSON record whose path is supplied only in the child environment. The record
contains schema version `1` and one reviewed reason from
`SAFE_RUNTIME_VERIFICATION_REASONS`; it never contains exception messages,
paths, document names, command output, or credentials.

The supervisor removes stale evidence before execution, accepts only the exact
schema and allowlist, and deletes the record after reading it. Missing,
oversized, malformed, or unapproved evidence resolves to
`activation_runtime_command_failed`. Add a new verifier reason only by updating
the cross-process allowlist, operator presentation, English/Marathi copy, and
the supervisor and catalog-parity tests together.

## Verification

Run the smallest applicable checks first:

```bash
git diff --check
python manage.py check
python manage.py makemigrations --check --dry-run
msgfmt --check flowdocs/locale/mr/LC_MESSAGES/django.po -o /tmp/django-mr.mo
msgattrib --only-fuzzy flowdocs/locale/mr/LC_MESSAGES/django.po
python3 -m unittest scripts.ci.test_operator_language
python3 scripts/ci/validate_operator_language.py
node --check flowdocs/core/static/main/js/search.js
node --check flowdocs/core/static/main/js/search-classic.js
node --check flowdocs/core/static/main/js/public-legal.js
```

For UI changes, run the repository browser suite with the disposable local
fixture and test at least 320px mobile, 768px tablet, 1024px laptop, and
1440px desktop. Check English, Marathi, reduced motion, loading, success,
no-result, error, source drawer, keyboard search, and account visibility.

For image/runtime changes, rebuild the web image before browser verification:

```bash
docker build -t pdfsearch-ci:source .
PDFSEARCH_IMAGE=pdfsearch-ci:source REDIS_IMAGE=redis:7-alpine \
  bash scripts/ci/run_compose_smoke.sh
```

The smoke gate proves the actual image entrypoint, Redis dependency, runtime
routes, migrations, static files, search landing page, core tests, data, and
index gates. It is not a substitute for browser interaction checks.

## PR checklist

- [ ] UI contract read and affected component recorded.
- [ ] No unapproved direction change or fabricated civic content.
- [ ] English and Marathi states reviewed.
- [ ] Keyboard/focus/live-region/reduced-motion behavior reviewed.
- [ ] No horizontal overflow or sticky composer obstruction.
- [ ] Relevant tests and `git diff --check` pass.
- [ ] Documentation updated for user-visible behavior.
- [ ] Changes are committed logically and the PR targets `dev`.
## Unavailable document-media contract

`PDFFile.lifecycle="unavailable"` is the non-destructive quarantine for a
preserved row whose source file cannot currently be verified. Only an explicit
admin POST with the exact `MARK UNAVAILABLE` confirmation may enter the state.
When authoritative recovery evidence exists, the request binds the expected
SHA-256 and byte size as a complete pair. A definitive absence may instead be
recorded with both fields blank; restoration then remains disabled until the
separate audited recovery-evidence action binds the complete pair. Every
transition requires an allowlisted human-readable reason code and an
alphanumeric bounded case reference. This
prevents raw custody paths from entering the model, audit, or interface. Inside
one transaction the row is locked, its prior lifecycle
and bounded evidence are persisted, `indexed=False` is set, and a
`media_unavailable` event is appended. Repeated requests report a no-op and do
not create duplicate transition events. Storage keys, absolute paths, and
document content never enter audit or interface evidence.

The quarantine, recovery-evidence binding, and verified restoration services
also own a durable source-mutation scope. They mark that scope changed only
after the existing atomic row-and-audit transaction commits, including when
called from a supported management shell instead of HTTP. Nested request
middleware coalesces with the service scope, while no-op, failed, and
rolled-back transitions do not advance the source epoch or make a sync job
eligible.

The remaining Vault Active Sync retry path is legacy compatibility behavior,
not the DataOps v3 backup contract. When that disabled path is exercised by its
focused tests, retry requests use `VaultJobRetryRequest` as an exact-once control
receipt keyed by job and operator idempotency key. The locked job state version,
resulting retry count, retry mode, and actor are stored in the same control
transaction as the state transition and audit event. A finalized snapshot uses
`checkpoint_resume`; a pre-finalization failure uses `fresh_snapshot` after
a durable, post-commit bounded workspace-cleanup intent. Other operations use
`operation_retry` unless their own service proves resumable evidence. UI copy
must describe those modes separately and must not promise checkpoint reuse for
a failed snapshot.

Each supported media transition must be the outermost transaction owner for
the application database. The service rejects a caller-owned atomic block with
the stable technical reason `media_transition_outer_atomic_unsupported` before
opening a mutation scope or changing row, audit, epoch, or journal state.
`DATABASES["default"]["ATOMIC_REQUESTS"]` must remain `False`. Do not wrap these
services in `transaction.atomic()`; use their existing internal row-and-audit
transaction so the independently durable control epoch can advance only after
that transaction returns successfully.

The legacy Active Sync path may reconcile a stale FAISS index only inside its isolated
incomplete snapshot. The derivation must use the frozen SQLite copy's retained
embeddings in runtime search order, apply the configured vector and dimension
bounds, and write via an atomic candidate-local replacement. It must not call
an embedding provider, import ORM models against the live database, or write
the live database/index tree. Bind per-folder disposition, count, dimensions,
and digest into snapshot evidence, the generation manifest, and publication
validation. Missing or corrupt searchable embeddings remain a fail-closed
snapshot error.

`archived` and `deprecated` are ordinary product lifecycle states, not custody
exceptions. Missing, blank, null, or unsafe media references in either state
must fail inventory, candidate, runtime, and certification gates. Never edit
SQLite lifecycle columns directly to bypass those gates; use the supported
audited transition so history and recovery posture remain trustworthy.

All search querysets, FAISS construction, stored-index repair, maintenance
selection, candidate embedding/media validation, runtime activation file
verification, and readiness denominators must exclude unavailable rows.
Dashboard inventory continues to show the preserved row and its human operator
guidance; the stable code is available only in collapsed technical details.

Restoration opens the configured local-storage object without following a final
symlink, proves it is a regular file, hashes it while checking stable inode,
size, and modification evidence, and compares exact SHA-256 and byte size. It
then returns the row to `media_prior_lifecycle` and records bounded verification
evidence in `media_restored`. Legacy unavailable rows without durable expected
evidence remain safely unavailable. Candidate, snapshot, and activation
evidence include a deterministic unavailable-set attestation: total count,
bounded sorted IDs, truncation, and a digest over the complete canonical
ID/lifecycle/storage-key-status/evidence tuples. Candidate validation compares
the exact prepared attestation, immutable publication stores it in both the
manifest and `ArtifactValidation`, and runtime activation, certification, and
rollback compare the same value. Do not use
`reconcile_media_pdfs` to remap a dangling row: that command only imports media
paths that have no database row.

Verification:

```sh
python manage.py test core.tests.DocumentLifecycleTests \
  core.tests_candidate_cleanup.CandidateWorkspaceTests \
  core.tests_maintenance_contract.MaintenancePlanningTests
python manage.py makemigrations --check --dry-run
python ../scripts/ci/validate_operator_language.py
```
