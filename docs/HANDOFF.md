Status: Active, living handoff
Audience: Maintainer, Operator, Developer, AI agent, Reviewer
Owner: FlowDocs maintainers
Last verified: 2026-08-08
Canonical source: docs/HANDOFF.md
Supersedes: docs/STATUS-2026-08-03.md for current operational state
Update trigger: Every merged runtime/release/data/operations change, deployment, incident, rollback, or material blocker decision

# Current project handoff

## Read this first

This is the single secret-free source for current repository, stage, legacy
production, data-recovery, and next-action state. Read it before changing code,
deployment configuration, persistent data, recovery behavior, or environment
posture.

The dated `STATUS-*.md` files are immutable evidence snapshots. They explain
how the current state was reached, but they do not override this handoff.
Detailed contracts remain in their owning runbooks; this page links to them
instead of duplicating their procedures.

Evidence on this page was refreshed on 2026-08-08 (Asia/Kolkata). A live system
can change after that time, so repeat the read-only checks in
[Resume checks](#resume-checks) before a mutation.

## Non-negotiable boundary

- Legacy production at `www.ai-sahakar.net` remains authoritative and online.
- Production traffic, DNS, Traefik routing, the legacy service, and
  `prod_flowdocs` were not changed by the 2026 stage rehearsal.
- The project named `sahakar-ai-sahakar-frontend-2026-prod-ruhj6z` is **stage**
  despite the historical `prod` substring. It serves `2026.ai-sahakar.net`.
- `/root/prod-2026.env` is a prepared future-production configuration file. No
  corresponding 2026 production Dokploy project exists yet, and the file alone
  does not authorize or constitute a deployment.
- Production cutover requires a separate explicit approval, immutable image
  selection, rollback evidence, and traffic-change plan.
- Never use `docker compose down -v`, delete or reuse a source volume as a
  restore target, or make the legacy mount writable.

## Current verified state

| Boundary | Verified state | Evidence / consequence |
| --- | --- | --- |
| Repository integration baseline | `dev` contains `ffdb6dfe3f4ed8081c9ae7875ae47590e132d62f` (PR #200), with PRs #185–#198 in its ancestry | Role-aware Settings ownership, unified cookie consent, corrected Classic continuity, and the isolated Maharashtra Service theme are integrated; this is repository evidence, not stage deployment proof |
| Maharashtra Service theme | PR [#200](https://github.com/Nimble-esolutions/PdfSearch/pull/200) is merged and its `dev` release pipeline passed | Adds the third public-search presentation using the shared typed search contract, theme-native legal/recovery pages, responsive vector identity marks, and dedicated browser coverage. The certified image was published and promoted by CI; stage continues to run the older image and cannot be used as evidence for this theme. |
| Settings and Classic corrective work | PR [#198](https://github.com/Nimble-esolutions/PdfSearch/pull/198) merged into `dev` after all required checks passed | The rejected all-`auto` Grid shell is replaced by intrinsic-height bands and a flexible conversation canvas; admin/superadmin scope, ENV locks, saved/default precedence, secret redaction, analytics ownership, and Classic empty/long-answer/second-question behavior are covered by focused tests |
| Classic focus-ring fix | PR #193 is merged into `dev`; not yet deployed to stage | The Classic compound composer now owns one accessible focus ring instead of drawing a second global input outline. The disposable Docker-backed Classic suite passed all 12 desktop tests, including a computed-style regression for the Marathi input field. No stage or production change has been made. |
| Persistent analytics integration | PRs [#196](https://github.com/Nimble-esolutions/PdfSearch/pull/196) and [#198](https://github.com/Nimble-esolutions/PdfSearch/pull/198) are merged; the independent service remains operator-managed | The normal cookie notice is the single visitor-consent surface. Stage and production use separate host-scoped tenants, local collection is hard-disabled, Global Privacy Control blocks collection, and analytics mode/Website ID are ENV-owned only when either key is explicitly defined; otherwise the superadmin control is editable. This remains repository verification, not deployment proof. |
| Public recovery pages | PR [#196](https://github.com/Nimble-esolutions/PdfSearch/pull/196) is merged; the pending Maharashtra branch extends the same contract | Standard 400/403/404/500 responses use the active public-search visual language without tracker/search scripts or failed-URL reflection. No stage deployment claim is implied. |
| Local development | Development Compose stack is currently stopped | Do not infer local data fitness from historical round-trip evidence; start and verify it when local runtime work resumes |
| Stage route | `https://2026.ai-sahakar.net/` returned HTTP 200 | Reachability only; `/readyz` remains authoritative |
| Stage services | Redis, web, and maintenance are running and healthy | Same Compose project and persistent volumes remain active |
| Stage application artifact | `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:8649af368c2ba84f272942d7ab969055f0c2eaa502b4032f7bd52aa955cc52cc` | Web and maintenance are healthy on the same image ID and OCI revision `1067c054edd6a21a7881371ca0670e428dc4cc81` |
| Repository versus stage | Stage remains on OCI revision `1067c054edd6a21a7881371ca0670e428dc4cc81`; current `dev` is newer | Stage canaries prove the deployed revision only. PRs #187–#190 still require an integrated image certification and stage rollout before their behavior can be claimed live. |
| Stage readiness | `status=ready`; database, cache, migrations, data, backup, and DataOps checks are `ok` | Backup, restore, and import actions report `ready` |
| Stage inventory | 242 PDF rows, 242 indexed PDFs, 46 folders, 7 users | Indexing ratio is `1.0` |
| Signed runtime | Generation `dataops-import-legacy-20260802-86288855-stage-2026` | Signature verifies; runtime pointer is authoritative |
| Active manifest | `80d8dc81d27956d86054953d1651ff5a88e1597449fc73c34f82a351c1a83c96` | Exact digest reported by `/readyz` |
| Stage recovery receipts | Backup and restore receipts are present | Stage backup remains manual and is not a web-availability prerequisite |
| Legacy production | `www.ai-sahakar.net` returned HTTP 200; Swarm service `sahakar-dev-frontend-dockerfile-1cubi5` remains at one replica | Legacy production remains unchanged and authoritative |
| Environment files | `/root/stage-2026.env` and `/root/prod-2026.env` are present | Never print or commit their values; audit posture by key only |

## Data and recovery lineage

```text
legacy production (authoritative, read-only source)
  prod_flowdocs
    -> stable source generation legacy-20260802T085639Z-86288855
       dataset ai-sahakar-prod-v2
       bucket ai-sahakar-prod-flowdocs-artifact-vault-v2
         -> verified clone/rebind clone-legacy-20260802T085639Z-86288855
            dataset ai-sahakar-stage-2026
            bucket ai-sahakar-stage-2026-flowdocs-artifact-vault
              -> reconciled stage import
                 -> signed active generation
                    dataops-import-legacy-20260802-86288855-stage-2026
                    manifest 80d8dc81...3c96
                      -> manual stage backup
                         -> isolated disposable restore rehearsal
```

The stage clone contains 416 logical objects and 1,093,501,777 bytes. A
recovery point is logically full: “incremental” means unchanged
content-addressed objects can be reused instead of uploaded again. It is not a
fragile chain of partial archives.

The latest readiness evidence reports the stage backup receipt manifest as
`01aa7e0ff7c8ac6583c35098f6b3aab55eb480eaa99f0ea32a972e868f3b0e31`
and the restore receipt manifest as the active manifest above. Automatic stage
backup remains off unless the operator explicitly changes that policy.

## Runtime and policy posture

| Concern | Current decision |
| --- | --- |
| Data Operations | DataOps v3 is the active backup/import/restore product and workbench. VaultOps remains an internal compatibility API, control schema, selected maintenance surface, and signed activation/runtime bridge; it is not a second operator product. |
| Stage image selector | `:latest` is intentional for stage; the resolved running digest and OCI revision are the evidence |
| Production image selector | Must be an explicitly approved immutable `repo@sha256:<digest>` |
| Stage public authentication | Production-account exposure exception is approved and its owner, monitoring, incident response, and rollback authority are recorded |
| Search provider | Real external AI is enabled for the AI lane; unrelated external side effects remain sandboxed |
| Document custody | Original PDFs stay on the server; native extraction runs first and local OCR handles scanned/blank pages |
| OCR | Local Tesseract fallback supports English, Marathi, and Hindi (`eng+mar+hin`) with bounded pages, pixels, and time |
| Embeddings | Extracted text may reach the configured external embedding provider under the existing side-effect policy |
| Backup cadence | Stage backup is manual; a successful receipt is recovery evidence, not a readiness prerequisite |
| Activation | Signed runtime pointer plus exact manifest digest is required; quarantine presence and HTTP 200 are insufficient |
| Public search presentation | Classic search is the fail-closed primary view. Knowledge Workbench and the pending Maharashtra Service view remain isolated presentations that can be selected through the existing superadmin setting or previewed with `?view=workbench` and `?view=maharashtra`; a preview does not change the saved primary view. |
| Public search response contract | Exact standalone greetings/thanks/identity prompts are `small_talk`; document questions are `evidence_answer` or `no_evidence`; `language` follows the question (`en`/`mr`), not the selected UI. Classic, Workbench, and Maharashtra Service consume the same typed JSON contract; the third theme adds no search backend or feature fork. |
| Product analytics production posture | Collection is allowed for the future 2026 production app only after its dedicated Umami Website ID, canonical apex routing, independent-stack proof, and normal production canary; it is not enabled on the legacy service by this repository |

PR #186 corrected the legacy substring small-talk predicate that caused words
such as `updated` and `membership` to collide with `date` or `hi`. Matching is
now normalized and whole-query, responses are typed, and the backward-compatible
`answer` and `references` fields remain stable. Answer language is derived from
the question, validated against the returned script before caching, and repaired
once before failing explicitly.

The previously certified release passed the disposable runtime, activation,
RustFS, MinIO, browser, and package gates. Stage canaries then proved Classic and Workbench
rendering plus both mismatched-locale directions: Marathi input posted as English
returned Marathi/Devanagari evidence with three references, and English input
posted as Marathi returned English/Latin evidence with three references. The
active generation remained `dataops-import-legacy-20260802-86288855-stage-2026`
and indexing remained `1.0`.

## Recently completed work

| PR | Outcome |
| --- | --- |
| #176 | Simplified backup, import, restore, and recovery around DataOps v3 |
| #177 | Corrected public search routing across authorized documents |
| #178 | Unified operator-workbench truth, scale behavior, and stage image policy |
| #179 | Added pre-merge maintenance lifecycle certification |
| #180 | Added reviewed multi-file intake for up to 50 PDFs with queued processing |
| #182 | Recorded and closed the activated-runtime migration incident |
| #183 | Corrected Compose volume-ownership recovery guidance |
| #184 | Established this enforced living project handoff |
| #185 | Rebuilt the approved Classic public search as the primary view while preserving an isolated Knowledge Workbench secondary view |
| #186 | Corrected search-intent false positives and enforced question-derived English/Marathi answer language across both themes |
| #187 | Added a standalone, theme-consistent shell for Terms, Privacy, Disclaimer, Data Policy, and Cookie Policy |
| #188 | Made local macOS documentation rendering honor an explicit Puppeteer browser and select Playwright's matching headless shell instead of launching the crashing GUI Chrome-for-Testing app |
| #189 | Keeps the Classic composer reachable after long answers, safely formats structured responses, validates typed payloads and PDF references, and gates continuity across the viewport matrix |
| #190 | Updated the living handoff after the Classic continuity merge |
| #193 | Removes the duplicate Classic composer focus outline and adds a browser regression gate; merged into `dev` |
| #194 | Redesigns Settings & Configuration around truthful runtime controls, validation, confirmations, and read-only deployment posture; merged into `dev` |
| #195 | Recorded the first Settings control-center merge; its integration references are now superseded by this handoff |
| #196 | Added consent-led host-scoped analytics and themed public recovery pages; retained, with duplicate consent and settings-ownership regressions corrected on the active branch |
| #197 | Added public-performance, responsive-masthead, and Umami capability plans; documentation only, with no runtime behavior to claim |
| #200 | Added the isolated Maharashtra Service public-search theme, official vector identity, theme-aware legal/recovery surfaces, safe rendering contracts, responsive/browser certification, and updated architecture guidance; merged into `dev` and release-certified, but not deployed to stage |

PR #200 implements the approved third presentation without changing the search
API, admin site, primary-view default, or existing Classic and Workbench assets.
Local source-backed browser evidence covers desktop, mobile, a 320-pixel
budget-phone viewport, long-answer document scroll, composer continuity, a
second question, English/Marathi behavior, theme-aware legal/recovery pages,
safe structured rendering, protected source links, and automated accessibility
checks. The post-merge `dev` workflow also passed source/deployment lifecycle,
published-image startup, image-size, and release-promotion gates. It is not live
stage evidence until that image is deployed and the stage canary is repeated.

The stage volume warning is resolved. The three project-scoped volumes were
copied while quiescent, digest-verified, recreated with Docker Compose's
internal ownership labels, restored, re-verified, and restarted. No
`COMPOSE_PROJECT_NAME`, `external: true`, custom backup labels, or
Dokploy-specific volume workaround was added. A second Compose apply emitted
no ownership warning.

## Open decisions and next actions

There is no data-readiness or search-language blocker. PR #198 is merged and
locally/CI certified; search latency remains a measured release task until an
integrated image is deployed and canaried. Remaining work is:

1. **Integrated stage rollout:** after the merged image reaches stage, prove
   the unchanged signed generation and all three themes; compare uncached/repeat
   duration, phase telemetry, corpus bytes/vectors, and web-worker RSS. Roll
   back the image if authorization, continuity, latency, or memory headroom
   regresses.
2. **Settings stage canary:** verify admin/superadmin role separation, explicit
   ENV field locks, saved override/default sources, redacted inventory, and
   host-specific analytics without changing persistent data or production.
3. **Future production project:** create and validate the dedicated 2026
   production Dokploy project only after explicit approval. Treat
   `/root/prod-2026.env` as prepared input, not deployment evidence.
4. **Production rehearsal:** before traffic changes, select an immutable image,
   validate rendered Compose and key-only environment posture, restore into
   isolated production-candidate volumes, run search/PDF/auth smoke checks, and
   record rollback image and generation.
5. **Production cutover:** remains out of scope until separately authorized.
   Do not change legacy service routing or `prod_flowdocs` while preparing it.
6. **Stage recovery retest:** run another manual backup and disposable restore
   only when recovery/data contracts change or when explicitly requested.
7. **Local development:** start the native development stack and rerun focused
   local recovery/search tests when a new implementation task requires it; the
   stack is intentionally stopped now.
8. **Consent-led analytics stage canary:** PRs #196 and #198 are merged; after
    their integrated image is deployed, verify the independent pinned
    Umami/PostgreSQL stack, reviewed tracker version/source, TLS, dashboard
    authentication, retention/deletion authority, and database restore. Then use
    the per-host Settings control and run the exact browser canary in
    [`PERSISTENT_ANALYTICS_OPERATIONS.md`](PERSISTENT_ANALYTICS_OPERATIONS.md).
    Production collection is authorized in principle, but only for the future
    canonical 2026 app with a separate production tenant and normal cutover
    evidence; never add analytics to application readiness.

## Known traps that must not recur

- A root-page 200 or healthy container does not prove signed data readiness.
- `docker system prune --all --volumes` does not repair ownership labels and
  must not be used as a volume-recovery technique.
- Do not silence Compose ownership warnings by making application volumes
  external, adding a project-name variable, or adding arbitrary YAML labels.
- Do not pre-create ordinary project-scoped volumes without Compose's internal
  labels. If historical labels are missing, use the verified copy/recreate/
  restore procedure in
  [DOKPLOY_DATA_PERSISTENCE.md](DOKPLOY_DATA_PERSISTENCE.md).
- Do not expose enabled UI controls whose real handler will reject the default
  request. Capability, default action, authored refusal, and retry behavior
  must be tested together.
- On macOS, documentation and browser automation must use Playwright's matching
  `chrome-headless-shell`; never pass the GUI Google Chrome for Testing app to
  Mermaid/Puppeteer. Honor an explicit `PUPPETEER_EXECUTABLE_PATH`, reject a
  missing path, and keep machine-specific browser-cache paths out of Git.
- Never classify conversational intent with substring matching. A small-talk
  fast path must match the complete normalized query, and regression tests must
  include domain words containing short conversational tokens.
- All public themes must preserve the backend response `kind`; a friendly
  answer without evidence must not be presented as a document-backed answer.
- Do not replace the approved Classic flex shell with all-`auto` CSS Grid rows.
  Surplus viewport space will stretch intrinsic utility/footer bands. Protect
  both states in browser tests: empty content fits one viewport, while a long
  answer grows the document and leaves the composer available for question two.
- Validate the complete success envelope before mutating either theme's DOM:
  evidence answers require valid sources, non-evidence outcomes forbid them,
  and only English/Marathi response languages are accepted.
- A Playwright pass is not source evidence when an unrelated process owns its
  base URL. Use an isolated source-backed service or an explicit deployed
  canary, and record which one was tested.
- `overflow: auto` does not make a transcript scrollable unless every grid/flex
  ancestor gives it a bounded height and `min-height: 0`. Long-answer tests must
  prove transcript scroll ownership, a visible composer, protected sources,
  and a successful second query on portrait and short-landscape viewports.
- Public answer formatting must use allowlisted DOM nodes and text nodes. Never
  fix literal Markdown with unsanitized `innerHTML`; hostile HTML must remain
  inert text while headings, lists, and bold markers gain semantic structure.
- The page/session locale is a presentation preference, not proof of question
  language. Derive answer language from the question, expose it in the API and
  DOM, and never cache a provider response that fails the script check.
- Do not cache search results across mutable or unsigned data, authorization
  scopes, provider policies, models, languages, or answer-contract versions.
  Signed activation alone is not immutable: bind reusable corpora and exact
  results to the durable mutation epoch, reject reuse during active writes or
  barriers, and reauthorize every cached reference at the response boundary.
  Cache failure must reduce speed, never search availability.
- Do not simulate token streaming after a completed JSON response. All public
  themes must format the completed answer immediately and announce only a
  concise completion status to assistive technology.
- Model-authored Markdown links are untrusted output. Only same-origin,
  allowlisted public PDF routes may become clickable source links; preserve all
  other link labels as inert text and never use unsanitized `innerHTML`.
- Public-search word limits belong to the server-owned runtime contract. Render
  the effective limit into each theme instead of duplicating a JavaScript
  constant that can drift from `PUBLIC_SEARCH_MAX_WORDS`.
- Every public theme needs unique accessible landmark names and a valid heading
  hierarchy. Theme isolation must not introduce duplicate navigation labels,
  skipped headings, global CSS leakage, or a separate backend behavior fork.
- Do not deploy a migration to an activated SQLite runtime unless the safe
  runtime migration classifier accepts it as recovery-backed and additive.
- Do not expose deployment-only environment identity, data lineage, backup
  posture, or credentials as editable database settings. If a UI control cannot
  affect the running request path safely, it must be read-only and explain the
  reviewed deployment path. High-impact runtime changes require explicit
  acknowledgement and server-side validation.
- Do not describe the historic cookieless stage pilot as current behaviour.
  Current analytics documentation must distinguish code under review from live
  service evidence, use one exact host per tenant, and state that Website IDs
  are public configuration while tracker integrity, retention, and deletion
  remain independent operational controls.
- Do not resurrect VaultOps as a parallel product surface. DataOps v3 replaced
  its operator workbench to reduce operator and code complexity. Do not claim
  that the package was deleted: its internal compatibility API, control schema,
  selected maintenance handlers, and signed activation/runtime bridge remain.
- Current operator documentation must say **Data protection** and **Search
  maintenance**. Historical VaultOps material must identify itself as
  historical/internal compatibility and link to the current DataOps v3 source.

## Resume checks

Run these read-only checks before continuing operational work:

```bash
git checkout dev
git pull --ff-only origin dev
git status --short --branch
gh pr list --state open
curl -fsS https://2026.ai-sahakar.net/readyz
curl -fsS -o /dev/null -w '%{http_code}\n' https://www.ai-sahakar.net/
```

For server inspection, use the approved SSH host from the local operations
inventory and summarize environment posture by key; never print `.env` values.
Verify the stage Compose project, running digest/revision, volume labels,
database integrity, counts, signed pointer, receipts, and active jobs before a
mutation.

## Handoff update contract

“Keep it updated” means event-driven, evidence-backed updates in the same PR or
immediately after an approved remote operation—not speculative timestamps or
claims of continuous monitoring.

Update this file whenever any of the following changes:

- merged application, workflow, deployment, environment, migration, data,
  recovery, security, or agent-operating contract;
- stage or production image, revision, Compose identity, route, or readiness;
- volume, database, object-store generation, manifest, backup, restore,
  activation, rollback, or retention posture;
- public authentication, OCR, embedding, external-side-effect, or search policy;
- incident, blocker, rejected option, operator approval, or next action.

Every update must:

1. refresh `Last verified` from observed evidence;
2. separate observed state, historical evidence, pending decisions, and plans;
3. state what changed, what was validated, what is blocked, and the exact next
   safe command or required approval;
4. preserve full digests and immutable identities where operationally relevant;
5. avoid secrets, document contents, credentials, and raw environment values;
6. update `LEGACY_VS_CURRENT_STATE.md` when the old-versus-current comparison
   changes materially;
7. record non-trivial operations in `~/.ai-audit`;
8. pass the handoff and documentation contracts before publication.

The fast PR contract requires this file to change with operationally meaningful
paths. Dated status pages remain historical and must not be rewritten to look
current.
